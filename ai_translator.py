"""
ai_translator.py v5.1
Kurdish Sorani Subtitle Translator — Simple, Natural, All Lines Guaranteed
"""
import json, re, time, random, logging
from typing import Optional
from pydantic import BaseModel
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ── Gemini Models (updated June 2026) ────────────────────────────────────────
GEMINI_MODELS = [
    "gemini-3.5-flash",       # تازەترین و باشترین (مەی ٢٠٢٦)
    "gemini-3.1-pro",         # بەهێزترین (فێبرووەری ٢٠٢٦)
    "gemini-3.1-flash-lite",  # ئەرزانترین و خێراترین
    "gemini-2.5-flash",       # باکئەپ
    "gemini-2.5-pro",         # باکئەپ
]

GEMINI_FALLBACKS = GEMINI_MODELS[:]

THINKING_PRESETS: dict = {
    "Ultra Fast (minimal)": 512,
    "Balanced (medium)":    4096,
    "Deep (high)":          16384,
    "Dynamic (بێ لیمیت)":  -1,
}

class SubtitleItem(BaseModel):
    start: float
    end:   float
    text:  str

class SubtitleResponse(BaseModel):
    translations: list[SubtitleItem]

# ── SYSTEM PROMPT ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """تۆ وەرگێری ژێرنووسی کوردی سۆرانی یت.

═══ یاساکانی سەرەکی ═══

١. کوردیی ئاسایی و سادەی ڕۆژانە بەکاربهێنە — نە قسەی کتێبی
   ✓ "چی بوو؟" — نە "چی ڕووی داوە؟"
   ✓ "باشە" — نە "بەشایستەیی"
   ✓ "بڕۆ!" — نە "تکایە بڕۆ!"
   ✓ "بخوو" — نە "تکایە خواردن بکە"

٢. کورت و ڕاستەوخۆ — هەر قسەیەک بە کەمترین وشە
   ✓ "بوەستە!" نە "تکایە وەستان بکە!"
   ✓ "ئایا باشی؟" نە "ئایا حاڵتان باشە؟"

٣. هەمان هیجان — تووڕەیی بە وشەی تووڕانە، شادی بە وشەی شادانە

٤. ناوی کارەکتەر بەهەمان شێوە بنووسە — هەرگیز -san یان -kun مەوەرگێرە

٥. گۆرانی (♪♫): کورت و شاعیرانە وەرگێرە

٦. هیچ نووکتەیەک (؟ . ، ! : ؛) لە دەقی کوردیدا مەخەرە

٧. ژمارەی ڕیزەکانی دەرچوون = ژمارەی ئینپووتەکان — هیچ ڕیزێک مەپەڕێنە

دەرچوون: تەنها JSON — بێ markdown:
{"translations":[{"start":<num>,"end":<num>,"text":"<کوردی>"},...]}"""

# ── Helpers ───────────────────────────────────────────────────────────────────
def _strip(r: str) -> str:
    r = r.strip()
    r = re.sub(r"^```(?:json)?\s*", "", r)
    r = re.sub(r"\s*```$", "", r)
    return r.strip()

def _backoff(a: int) -> float:
    return min(15.0, (2 ** a) + random.uniform(0.5, 2.0))

def _log(w, msg: str):
    log.info(msg)
    if w:
        try:
            w.info(msg)
        except:
            pass

def _build_prompt(chunk: list) -> str:
    items = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in chunk]
    return f"وەرگێرە {len(items)} ڕیز بۆ کوردی سۆرانی:\n\n" + json.dumps(items, ensure_ascii=False)

# ── Core Gemini Call ──────────────────────────────────────────────────────────
def _call_gemini_raw(keys, idx, chunk, budget, model_sel, widget):
    """Single attempt: call Gemini and return (rows, idx) or raise."""
    if not keys:
        raise RuntimeError("کلیلی API نییە.")

    models   = [model_sel] + [m for m in GEMINI_FALLBACKS if m != model_sel]
    msg      = SYSTEM_PROMPT + "\n\n" + _build_prompt(chunk)
    expected = len(chunk)

    for model in models:
        _log(widget, f"[Gemini] ▶ {model} ({expected} ڕیز)")
        for attempt in range(10):
            key = keys[idx % len(keys)]
            try:
                client = genai.Client(api_key=key)

                cfg = {
                    "temperature":         0.45,
                    "response_mime_type":  "application/json",
                    "response_schema":     SubtitleResponse,
                }

                # Thinking config — gemini-3.5-flash uses thinking_level string
                if budget is not None:
                    if model.startswith("gemini-3.5"):
                        # New API: thinking_level enum
                        if budget == -1:
                            cfg["thinking_config"] = types.ThinkingConfig(thinking_level="high")
                        elif budget >= 16384:
                            cfg["thinking_config"] = types.ThinkingConfig(thinking_level="high")
                        elif budget >= 4096:
                            cfg["thinking_config"] = types.ThinkingConfig(thinking_level="medium")
                        else:
                            cfg["thinking_config"] = types.ThinkingConfig(thinking_level="minimal")
                    else:
                        # Old API: thinking_budget integer
                        if budget == -1:
                            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)
                        elif budget > 0:
                            cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)

                resp = client.models.generate_content(
                    model    = model,
                    contents = [types.Content(role="user", parts=[types.Part(text=msg)])],
                    config   = types.GenerateContentConfig(**cfg),
                )

                data = json.loads(_strip(resp.text or ""))
                rows = data.get("translations", [])

                if len(rows) != expected:
                    raise ValueError(f"Row mismatch {expected}≠{len(rows)}")

                _log(widget, f"[Gemini] ✅ {model} — {expected} ڕیز")
                return rows, idx

            except Exception as exc:
                s = str(exc)
                if "429" in s or "quota" in s.lower() or "rate" in s.lower():
                    idx = (idx + 1) % max(len(keys), 1)
                    _log(widget, f"[Gemini] 429 ← گۆڕدرا کلیل (هەوڵ {attempt+1})")
                    time.sleep(1.5)
                elif "404" in s or "not found" in s.lower():
                    _log(widget, f"[Gemini] 404 ← {model} — بەدواییدا ;(")
                    break
                elif "Row mismatch" in s and attempt >= 4:
                    raise
                else:
                    wait = _backoff(attempt)
                    _log(widget, f"[Gemini] هەڵە (هەوڵ {attempt+1}): {s[:60]} | {wait:.1f}s")
                    time.sleep(wait)

    raise RuntimeError("هەموو مۆدێلەکان تەواو بوون.")

# ── Split-Retry: guarantees ALL lines translated ──────────────────────────────
def _translate_chunk(keys, idx, chunk, budget, model, widget):
    """Translate chunk. If row-count mismatch → split in half & retry recursively."""
    if not chunk:
        return [], idx
    try:
        return _call_gemini_raw(keys, idx, chunk, budget, model, widget)
    except (ValueError, RuntimeError) as e:
        if len(chunk) == 1:
            _log(widget, f"⚠️ یەک سێگمێنت نەوەرگێرا، دەقی ئەصلی بەکار دەهێنرێت")
            return [{"start": chunk[0]["start"], "end": chunk[0]["end"], "text": chunk[0]["text"]}], idx
        mid = len(chunk) // 2
        _log(widget, f"⟳ دووبەش دەکرێت: {len(chunk)} → {mid}+{len(chunk)-mid}")
        rows1, idx = _translate_chunk(keys, idx, chunk[:mid],  budget, model, widget)
        rows2, idx = _translate_chunk(keys, idx, chunk[mid:],  budget, model, widget)
        return rows1 + rows2, idx

# ── Public API ────────────────────────────────────────────────────────────────
def ai_translate(gemini_keys, cur_gem_idx, transcript_chunk,
                 thinking_budget, selected_model, status_msg,
                 ctx_before=None, ctx_after=None):
    if not transcript_chunk:
        return [], cur_gem_idx
    return _translate_chunk(
        gemini_keys, cur_gem_idx, transcript_chunk,
        thinking_budget, selected_model, status_msg
    )
