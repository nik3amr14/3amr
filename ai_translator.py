"""
ai_translator.py — Kurdish Sorani Cinematic Subtitle Translator
Engine  : Google Gemini
Models  : gemini-3.5-flash · gemini-3.1-flash-lite · gemini-3-flash-preview
          gemini-2.5-flash · gemini-2.5-pro · gemini-3.1-pro-preview
Author  : bashdar77 / nik3amr14  |  v4.0
"""

import json
import re
import time
import random
import logging
from typing import Optional
from pydantic import BaseModel
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# MODELS  (6 models, best-first)
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_MODELS = [
    "gemini-3.5-flash",          # fastest + smartest  (2026)
    "gemini-3.1-pro-preview",    # premium quality
    "gemini-3.1-flash-lite",     # ultra-fast lite
    "gemini-3-flash-preview",    # GA Dec 2025
    "gemini-2.5-pro",            # high quality
    "gemini-2.5-flash",          # stable, reliable
]
GEMINI_FALLBACKS = GEMINI_MODELS[:]

# ─────────────────────────────────────────────────────────────────────────────
# THINKING PRESETS
# ─────────────────────────────────────────────────────────────────────────────
THINKING_PRESETS: dict = {
    "Ultra Fast (minimal)": 512,
    "Balanced (medium)":    4096,
    "Deep (high)":          16384,
    "Dynamic (بێ لیمیت)": -1,
}

# ─────────────────────────────────────────────────────────────────────────────
# PYDANTIC SCHEMA
# ─────────────────────────────────────────────────────────────────────────────
class SubtitleItem(BaseModel):
    start: float
    end:   float
    text:  str

class SubtitleResponse(BaseModel):
    translations: list[SubtitleItem]

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM PROMPT — Natural Kurdish Sorani, Context-Aware, Character Speech
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """تۆ وەرگێڕی پرۆفیشناڵی ژێرنووسی کینەماتۆگرافیکی کوردی سۆرانی یت.
You are an elite Kurdish Sorani subtitle translator — think like a native speaker
from Sulaymaniyah who has deeply studied cinema, drama, and anime for 20 years.

══════════ یاساکانی زێرین ══════════

١. مانا وەرگێرە نەک وشە — TRANSLATE MEANING, NOT WORDS:
   تێبگە کارەکتەرەکە ڕاستی چی دەوێت بڵێت، نەک ئەوەی بە زبانی ئینگلیزی یان ژاپۆنی
   دەڵێت. پاشان ئەو مانایە بە کوردی سۆرانی خۆجێی وەرگێرە.
   ✗ نادروست: "I will destroy you" → "ئەم تۆی تێک دەدەم"
   ✓ دروست:   "I will destroy you" → "لووتت دەکەمەوە" / "داوای خاکت دەدەم"

٢. CONTEXT — سەیری هەموو ڕیزەکان بکە پێش وەرگێڕان:
   پێش ئەوەی وەرگێرم بدات، هەموو سێگمێنتەکانی چەنکەکە بخوێنەرەوە تا بزانیت:
   - کێ قسە دەکات و بۆ کێ
   - ئایا خۆشحاڵە، تووڕەیە، دڵتەنگە، شۆخیەکە دەکات؟
   - چی ڕووی داوە لەمەوپێش؟
   پاشان هەر ڕیزێک بە ئەو کۆنتێکستەوە وەرگێرە.

٣. SORANI GRAMMAR (Subject-Object-Verb):
   ✓ "کتێبەکەم خوێندەوە"    ✗ "خوێندم کتێبەکەم"
   ✓ "ئەو دڵتەنگی دەکەم"    ✗ "من دڵتەنگم دەکات"
   ئەندامەکانی کردار: ـم / ـت / ـێت / ـین / ـن / ـمان / ـتان / ـان

٤. SPEECH REGISTER — وەک کارەکتەرەکەی خۆی قسە بکە:
   • گفتوگۆی ئاسایی/جوان:   "دەزانیت چیمە؟" / "هەڵە نەکەی" / "وەربێ"
   • تووڕەیی:               "دەمت ببەسەوە" / "چەتم لێت گرتووە" / "خاکەتم خواردەوە"
   • دڵتەنگی/ئازار:         "دڵم شکا" / "نابمەوێتی" / "ئەوەم کوشت"
   • خۆشەویستی:             "خۆشم دەوێیت" / "دڵم لەتەوەیە" / "بێتۆ ناتوانم"
   • شۆک/سەرسام:           "ئەی خودایە" / "بەراستی؟" / "چۆن بووە؟"

٥. HONORIFICS — هەرگیز -سان / -کوون / -چان بە کاک / خاتوون وەرنەگێرە
   تەنها لە دۆخی فەرمی (پەرلەمان، دادگا) بەکاربهێنە.
   لە هەموو دۆخی تر: ناوی کارەکتەرەکەی خۆی بەکاربهێنە.

٦. NATURAL KURDISH EXPRESSIONS:
   "Oh my God"          → "یارب" / "خودایە" / "ئەی خودا"
   "Are you serious?"   → "ڕاستی دەڵێیت؟" / "شووخیت پێمکرد؟"
   "Shut up"            → "دەمت ببەسەوە" / "بێدەنگ بە"
   "Let's go"           → "بڕۆین" / "هەستە"
   "I told you"         → "نەمتگوت؟" / "خۆم پێت گوتبوو"
   "Be careful"         → "بەتەنها بە" / "ئاگاداربە"
   "I'm fine"           → "باشم" / "خۆشم"
   "Whatever"           → "خۆش" / "خوا زانیا"
   "Come on!"           → "وەربێ" / "هەستە" / "وەڕوو"

٧. گۆرانی — SONG LYRICS:
   ئەگەر تێکستەکە گۆرانی بوو (بە نیشانەی ♪ ♫ 🎵):
   وەرگێرانی بکە بە کوردی سۆرانی شاعیرانە و ریتمیک — سەیری ئاهەنگەکەی بکە
   و هەوڵبدە ئاهەنگی ڕیزەکان بپارێزیت.

٨. NO CENSORSHIP — هەرگیز سانسۆر مەکە:
   ئەگەر کارەکتەرەکە فیشەکی کرد یان بە توندی قسە کرد، هەمان توندی بە کوردی وەرگێرە.

٩. NO PUNCTUATION — هیچ نووکتەیەک مەخەرە لە ئاراستەی کوردی:
   (؟ . ، ! : ؛ " ' - _ …) هەموو ئەمانە سڕاندنەوە

١٠. TIMESTAMPS — هەرگیز start و end نەگۆڕە

١١. ROW COUNT — ژمارەی ڕیزەکانی دەرچوون دەبێت بە تەواوی وەک دەرچوونی ئێوارانی بێت

══════════ OUTPUT FORMAT ══════════
JSON تەنها — بێ markdown، بێ ڕوونکردنەوە:
{
  "translations": [
    {"start": <float>, "end": <float>, "text": "<Kurdish Sorani>"},
    ...
  ]
}"""

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _strip(raw: str) -> str:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()

def _validate(data: dict, expected: int) -> list:
    rows = data.get("translations", [])
    if len(rows) != expected:
        raise ValueError(f"Row mismatch: expected {expected}, got {len(rows)}")
    return rows

def _prompt(chunk: list, ctx_b: list, ctx_a: list) -> str:
    items = [{"start": s["start"], "end": s["end"], "text": s["text"]} for s in chunk]
    out = f"Translate the following {len(items)} subtitle segments to Kurdish Sorani.\n\n"
    if ctx_b:
        out += f"[CONTEXT before — for understanding only]:\n"
        out += " / ".join(s["text"] for s in ctx_b[-4:]) + "\n\n"
    out += f"[TRANSLATE THESE {len(items)} LINES]:\n"
    out += json.dumps(items, ensure_ascii=False, indent=2)
    if ctx_a:
        out += f"\n\n[CONTEXT after — for understanding only]:\n"
        out += " / ".join(s["text"] for s in ctx_a[:4])
    return out

def _backoff(attempt: int) -> float:
    return min(15.0, (2 ** attempt) + random.uniform(0.5, 2.0))

def _log(w, msg: str) -> None:
    log.info(msg)
    if w:
        try: w.info(msg)
        except: pass

# ─────────────────────────────────────────────────────────────────────────────
# GEMINI ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _call_gemini(keys, key_idx, chunk, ctx_b, ctx_a, budget, model_sel, widget):
    if not keys:
        raise RuntimeError("کلیلی API نییە.")

    models = [model_sel] + [m for m in GEMINI_FALLBACKS if m != model_sel]
    full_msg = SYSTEM_PROMPT + "\n\n" + _prompt(chunk, ctx_b, ctx_a)
    expected = len(chunk)

    for model in models:
        _log(widget, f"[Gemini] ▶ {model}")
        for attempt in range(10):
            key = keys[key_idx % len(keys)]
            try:
                client = genai.Client(api_key=key)
                cfg: dict = {
                    "temperature": 0.60,
                    "response_mime_type": "application/json",
                    "response_schema": SubtitleResponse,
                }
                if budget is not None:
                    if budget == -1:
                        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=-1)
                    elif budget > 0:
                        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=budget)

                resp = client.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user",
                               parts=[types.Part(text=full_msg)])],
                    config=types.GenerateContentConfig(**cfg),
                )
                rows = _validate(json.loads(_strip(resp.text or "")), expected)
                _log(widget, f"[Gemini] ✅ {model} — {expected} ڕیز")
                return rows, key_idx

            except Exception as exc:
                s = str(exc)
                if "429" in s or "quota" in s.lower() or "rate" in s.lower():
                    key_idx = (key_idx + 1) % max(len(keys), 1)
                    _log(widget, f"[Gemini] 429 ← {model} (هەوڵ {attempt+1}) — کلیل گۆڕدرا")
                    time.sleep(1.5)
                elif "404" in s or "not found" in s.lower():
                    _log(widget, f"[Gemini] 404 ← {model} — بەدواییدا")
                    break
                else:
                    wait = _backoff(attempt)
                    _log(widget, f"[Gemini] هەڵە ← {model} (هەوڵ {attempt+1}): {s[:80]} | {wait:.1f}s")
                    time.sleep(wait)

    raise RuntimeError("هەموو مۆدێلەکان و هەوڵەکان تەواو بوون.")

# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def ai_translate(
    gemini_keys: list,
    cur_gem_idx: int,
    transcript_chunk: list,
    thinking_budget: Optional[int],
    selected_model: str,
    status_msg,
    ctx_before: Optional[list] = None,
    ctx_after:  Optional[list] = None,
) -> tuple:
    if not transcript_chunk:
        return [], cur_gem_idx
    return _call_gemini(
        gemini_keys, cur_gem_idx, transcript_chunk,
        ctx_before or [], ctx_after or [],
        thinking_budget, selected_model, status_msg,
    )
