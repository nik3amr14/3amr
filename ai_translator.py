"""
ai_translator.py  v5.0
Kurdish Sorani Subtitle Translator — Simple, Natural, All Lines Guaranteed
"""

import json, re, time, random, logging
from typing import Optional
from pydantic import BaseModel
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

GEMINI_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-2.5-pro",
    "gemini-2.5-flash",
]
GEMINI_FALLBACKS = GEMINI_MODELS[:]

THINKING_PRESETS: dict = {
    "Ultra Fast (minimal)": 512,
    "Balanced (medium)":    4096,
    "Deep (high)":          16384,
    "Dynamic (بێ لیمیت)": -1,
}

class SubtitleItem(BaseModel):
    start: float
    end:   float
    text:  str

class SubtitleResponse(BaseModel):
    translations: list[SubtitleItem]

# ── SIMPLE NATURAL KURDISH SYSTEM PROMPT ─────────────────────────────────────
SYSTEM_PROMPT = """وەرگێڕی ژێرنووسی کوردی سۆرانی یت. بە کوردیی ئاسایی و سادە وەرگێرە.

═══ یاساکانی سەرەکی ═══

١. کوردیی ڕۆژانەی ئاسایی — وەک گفتوگۆی خۆمانی:
   "Are you okay?" → "باشیت؟"   ✓     "ئایا حاڵتان باشە؟"   ✗
   "Let's go!"     → "بڕۆین!"   ✓     "با بەیەک بچین"        ✗
   "I'm sorry"     → "ببووره"   ✓     "داخم هەیە"            ✗
   "No way!"       → "ناکرێت!" ✓     "ئەوە ناکرێت بە هیچ شێوەیەک" ✗
   "What happened?"→ "چیت بوو؟" ✓     "چی ڕووی داوە؟"        ✗

٢. کورت و دروست — ئەگەر یەک وشەیە، یەک وشەی کوردی بدەرێت:
   "Stop!" → "وەستە!" NOT "تکایە وەستان بکە"
   "Run!"  → "بەجوو!" NOT "خێرا بڕوو"
   "Liar!" → "درۆکەر!" NOT "تۆ کەسێکی درۆکەری"

٣. ئەحساس — هەمان هیجانی کارەکتەرەکە بنووسە:
   تووڕە → وشە و ڕستەی تووڕانە بەکاربهێنە
   دڵتەنگ → وشەی دڵسۆزانە
   شۆخ/کێفی → سووک و شادمانە

٤. ناوی کارەکتەر + پەیوەندی:
   هەرگیز -san/-kun/Mr./Mrs. بە کاک/خاتوون مەوەرگێرە لە ئەنیمی
   تەنها ناوی کارەکتەرەکە بەکاربهێنە

٥. گۆرانی (♪♫): بیوەرگێرە، کورت و شاعیرانە

٦. هیچ نووکتەیەک (؟ . ، ! : ؛) لە دەقی کوردیدا مەخەرە

٧. ژمارەی ڕیزەکانی دەرچوون = ژمارەی ئینپووتەکان بە تەواوی

دەرچوون: JSON تەنها — بێ markdown:
{"translations":[{"start":<num>,"end":<num>,"text":"<کوردی>"},...]}"""

# ── Helpers ───────────────────────────────────────────────────────────────────
def _strip(r):
    r=r.strip()
    r=re.sub(r"^```(?:json)?\s*","",r)
    r=re.sub(r"\s*```$","",r)
    return r.strip()

def _backoff(a): return min(15.0,(2**a)+random.uniform(0.5,2.0))

def _log(w,msg):
    log.info(msg)
    if w:
        try: w.info(msg)
        except: pass

def _build_prompt(chunk):
    items=[{"start":s["start"],"end":s["end"],"text":s["text"]} for s in chunk]
    return f"وەرگێرە {len(items)} ڕیز بۆ کوردی سۆرانی:\n\n"+json.dumps(items,ensure_ascii=False,indent=2)

# ── Core Gemini Call ──────────────────────────────────────────────────────────
def _call_gemini_raw(keys,idx,chunk,budget,model_sel,widget):
    """Single attempt: call Gemini and return (rows, idx) or raise."""
    if not keys: raise RuntimeError("کلیلی API نییە.")
    models=[model_sel]+[m for m in GEMINI_FALLBACKS if m!=model_sel]
    msg=SYSTEM_PROMPT+"\n\n"+_build_prompt(chunk)
    expected=len(chunk)

    for model in models:
        _log(widget,f"[Gemini] ▶ {model}  ({expected} ڕیز)")
        for attempt in range(10):
            key=keys[idx%len(keys)]
            try:
                client=genai.Client(api_key=key)
                cfg={"temperature":0.55,"response_mime_type":"application/json",
                     "response_schema":SubtitleResponse}
                if budget is not None:
                    if budget==-1: cfg["thinking_config"]=types.ThinkingConfig(thinking_budget=-1)
                    elif budget>0: cfg["thinking_config"]=types.ThinkingConfig(thinking_budget=budget)
                resp=client.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user",parts=[types.Part(text=msg)])],
                    config=types.GenerateContentConfig(**cfg))
                data=json.loads(_strip(resp.text or ""))
                rows=data.get("translations",[])
                if len(rows)!=expected:
                    raise ValueError(f"Row mismatch {expected}≠{len(rows)}")
                _log(widget,f"[Gemini] ✅ {model} — {expected} ڕیز")
                return rows,idx
            except Exception as exc:
                s=str(exc)
                if "429" in s or "quota" in s.lower() or "rate" in s.lower():
                    idx=(idx+1)%max(len(keys),1)
                    _log(widget,f"[Gemini] 429 ← کلیل گۆڕدرا (هەوڵ {attempt+1})")
                    time.sleep(1.5)
                elif "404" in s or "not found" in s.lower():
                    _log(widget,f"[Gemini] 404 ← {model} — بەدواییدا"); break
                elif "Row mismatch" in s and attempt>=4:
                    raise  # let split-retry handle it
                else:
                    wait=_backoff(attempt)
                    _log(widget,f"[Gemini] هەڵە (هەوڵ {attempt+1}): {s[:60]} | {wait:.1f}s")
                    time.sleep(wait)
    raise RuntimeError("هەموو مۆدێلەکان تەواو بوون.")

# ── Split-Retry: guarantees ALL lines translated ───────────────────────────────
def _translate_chunk(keys,idx,chunk,budget,model,widget):
    """Translate chunk. If row-count mismatch → split in half & retry recursively."""
    if not chunk:
        return [],idx
    try:
        return _call_gemini_raw(keys,idx,chunk,budget,model,widget)
    except (ValueError, RuntimeError) as e:
        if len(chunk)==1:
            # Single segment — can't split, return original text as fallback
            _log(widget,f"⚠️ یەک سێگمێنت نەوەرگێرا، دەقی ئەصلی بەکار دەهێنرێت")
            return [{"start":chunk[0]["start"],"end":chunk[0]["end"],"text":chunk[0]["text"]}],idx
        # Split in half and retry each part
        mid=len(chunk)//2
        _log(widget,f"⟳ دووبەش دەکرێت: {len(chunk)} → {mid}+{len(chunk)-mid}")
        rows1,idx=_translate_chunk(keys,idx,chunk[:mid],budget,model,widget)
        rows2,idx=_translate_chunk(keys,idx,chunk[mid:],budget,model,widget)
        return rows1+rows2,idx

# ── Public API ────────────────────────────────────────────────────────────────
def ai_translate(gemini_keys,cur_gem_idx,transcript_chunk,thinking_budget,
                 selected_model,status_msg,ctx_before=None,ctx_after=None):
    if not transcript_chunk: return [],cur_gem_idx
    return _translate_chunk(gemini_keys,cur_gem_idx,transcript_chunk,
                            thinking_budget,selected_model,status_msg)
