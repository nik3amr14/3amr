
"""
Kurdish Sorani Cinematic Subtitle Generator
============================================
Features:
  • Background image (bg.png/bg.jpg) with dark overlay or fallback #121212
  • 4 rotatable API keys with cascade exhaustion
  • Model selector + thinking-mode selector
  • Chunk-size slider (3-15 min)
  • Whisper word-level + segment-level fallback (no silent skips)
  • Smart Resume (continue from last translated second)
  • Strict punctuation stripping
  • Auto-generates .streamlit/config.toml (700 MB upload limit)
  • _cleanup_sub_session defined inside main() → no NameError
"""

import os, re, json, time, base64, shutil, tempfile, subprocess

import streamlit as st
import streamlit.components.v1 as components
from faster_whisper import WhisperModel
from google import genai
from google.genai import types

# ═══════════════════════════════════════════════════════════════════
#  AUTO-GENERATE STREAMLIT CONFIG  (700 MB upload)
# ═══════════════════════════════════════════════════════════════════
_CFG_DIR  = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".streamlit")
_CFG_FILE = os.path.join(_CFG_DIR, "config.toml")
if not os.path.exists(_CFG_FILE):
    os.makedirs(_CFG_DIR, exist_ok=True)
    with open(_CFG_FILE, "w") as _f:
        _f.write("[server]\nmaxUploadSize = 700\n\n[browser]\ngatherUsageStats = false\n")

# ═══════════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════════
APP_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)

KU_FONT_FILE = "Bahij Janna-Bold.ttf"
KU_FONT_PATH = os.path.join("/tmp", KU_FONT_FILE)
KU_FONT_NAME = "Bahij Janna"

MAX_SUB_DURATION = 4.0
THROTTLE_SECONDS = 50

MODEL_LIST = [
    "gemini-3.5-flash",        # ✅ باشترین هەڵبژاردن
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",   # ⚠️  تەنها دەستی خۆت هەڵیبژێرە
]

# ئەم مۆدێلە خۆکار بەکار ناهێنرێت بە Fallback
_FALLBACK_EXCLUDE = {"gemini-3.1-flash-lite"}

THINKING_MAP = {
    "⚡ Ultra Fast  (بێ بیرکردنەوە)":        "minimal",
    "⚖️ Standard / Balanced":               "medium",
    "🧠 Deep / Precise  (بیرکردنەوەی بەرز)": "high",
}

# Gemini 2.x uses thinking_budget (int); Gemini 3.x uses thinking_level (str)
_BUDGET_MAP = {"minimal": 0, "medium": 2048, "high": -1}

def _is_gemini3(model: str) -> bool:
    return bool(re.match(r"gemini-3[\.\-]", model))

# ═══════════════════════════════════════════════════════════════════
#  BACKGROUND CSS
# ═══════════════════════════════════════════════════════════════════
def inject_background():
    bg_file = None
    for name in ("bg.png", "bg.jpg", "bg.jpeg", "bg.webp"):
        p = os.path.join(APP_DIR, name)
        if os.path.exists(p):
            bg_file = p
            break

    if bg_file:
        with open(bg_file, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        ext = bg_file.rsplit(".", 1)[-1]
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
        bg_css = (
            f"background-image: linear-gradient(rgba(0,0,0,0.88),rgba(0,0,0,0.88)),"
            f"url('data:{mime};base64,{b64}');"
            "background-size: cover; background-attachment: fixed;"
        )
    else:
        bg_css = "background-color: #121212;"

    st.markdown(
        f"""<style>
        .stApp {{ {bg_css} }}
        section[data-testid="stSidebar"] {{ background-color: rgba(18,18,18,0.95); }}
        </style>""",
        unsafe_allow_html=True,
    )

# ═══════════════════════════════════════════════════════════════════
#  FONT
# ═══════════════════════════════════════════════════════════════════
def find_kurdish_font() -> str:
    for path in [
        os.path.join(APP_DIR,  KU_FONT_FILE),
        os.path.join(ROOT_DIR, KU_FONT_FILE),
        KU_FONT_FILE,
        os.path.join(os.path.dirname(APP_DIR), KU_FONT_FILE),
    ]:
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            shutil.copy(path, KU_FONT_PATH)
            return KU_FONT_NAME
    for path in [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]:
        if os.path.exists(path):
            return "Noto Sans Arabic"
    return "Arial"

# ═══════════════════════════════════════════════════════════════════
#  TIME UTILITIES
# ═══════════════════════════════════════════════════════════════════
def secs(ts: str) -> float:
    try:
        ts = ts.strip().replace(",", ".")
        h, m, sf = ts.split(":")
        s, frac = (sf.split(".", 1) + ["0"])[:2]
        return int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
    except Exception:
        return 0.0

def float_to_ass(t: float) -> str:
    h  = int(t // 3600)
    m  = int((t % 3600) // 60)
    s  = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def sec_to_srt(t: float) -> str:
    h  = int(t // 3600)
    m  = int((t % 3600) // 60)
    s  = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

# ═══════════════════════════════════════════════════════════════════
#  TEXT UTILITIES
# ═══════════════════════════════════════════════════════════════════
_PUNCT = set('؟.:!ـ؛\u201c\u201d\u2018\u2019?,;\'"!-_()[]{}،,+=*#$@^&|~`')

def clean_punctuation(t: str) -> str:
    return " ".join("".join(ch for ch in t if ch not in _PUNCT).split())

_CUE_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*[|\t]\s*(.+)"
)

def parse_raw_text(raw: str) -> list:
    out = []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            out.append({
                "start": m.group(1).replace(",", "."),
                "end":   m.group(2).replace(",", "."),
                "text":  m.group(3).strip(),
            })
    return out

def shift_transcript(raw: str, delay: float) -> str:
    if delay == 0.0:
        return raw
    lines = []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            ns = max(0.0, secs(m.group(1)) + delay)
            ne = max(0.0, secs(m.group(2)) + delay)
            lines.append(f"{float_to_ass(ns)} --> {float_to_ass(ne)} | {m.group(3)}")
        else:
            lines.append(line)
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════════════════
#  GEMINI  JSON PARSER
# ═══════════════════════════════════════════════════════════════════
def extract_json(text: str):
    text = text.strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
            break
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\[.*\])", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError("JSON parse failed")

# ═══════════════════════════════════════════════════════════════════
#  TIMESTAMP VALIDATION
# ═══════════════════════════════════════════════════════════════════
def validate_cues(cues: list) -> list:
    out, cs, ce = [], 0.0, 0.0
    for c in cues:
        try:
            ns = float(c["start"]); ne = float(c["end"]); nt = str(c.get("text","")).strip()
        except Exception:
            continue
        if not nt or ne <= ns:
            continue
        if ns < cs:
            ns = ce
        if ne <= ns:
            continue
        out.append({"start": round(ns,3), "end": round(ne,3), "text": nt})
        cs, ce = ns, ne
    return out

# ═══════════════════════════════════════════════════════════════════
#  GEMINI TRANSLATION  — Cascade Key Rotation + Model Fallback
# ═══════════════════════════════════════════════════════════════════
def gemini_translate(
    api_keys: list,
    chunk: list,
    primary_model: str,
    thinking_budget: int,
) -> list:

    system = f"""تۆ باشترین و بێهاوتاترین وەرگێڕی سینەمایی و ئەدەبی کوردستانیت. زمانت کوردی سۆرانی پووری ئاڵا و سەرووی ئاستی ئامۆژگاریە. ئەرکەکەت دانانی ژێرنووسی سینەماییانەی زۆر شاز، پاراو، و ناوازەیە بۆ هەموو جۆرە ناوەڕۆکی ڤیدیۆیەک — فیلم، زنجیرە، ئەنیمە، داکیومێنتری، یا هەر شتێکی تر.

═══════════════════════════════════════════════
 زاکانی زەرین  (هەرگیز مەشکێنە)
═══════════════════════════════════════════════

① وەرگێڕانی ١٠٠٪ مرۆڤانە و سینەماییانە
   • هەرگیز وشە بە وشە مەکە — ئەمە وەرگێڕان نییە، ئەمە لێدانی زمانە.
   • مانای نێوان خەتەکان، ئاهەنگی دەق، و هەست و سۆزی کارەکتەرەکان بگرە.
   • بینەر دەبێت هەست بکات ئەم فیلمە بە کوردی دروستکراوە — نەک وەرگێڕاوە.

② بەکارهێنانی ئیدیۆم و گۆرانەکانی کوردی
   • کوردی گۆرانەی خۆی هەیە. نموونەکان:
     "I'm dead serious"    →  "بە جیگەی خوام"  نەک  "زۆر جدی بوم"
     "You're killing me"   →  "دەمکوژیت"       نەک  "دەمکوژیت تۆ"
     "Break a leg"         →  "سەرکەوتوو بیت"
     "That's on you"       →  "ئەمە لە ئەستۆتە"
     "Over my dead body"   →  "تا من زیندووم نابێت"
     "I don't buy it"      →  "بڕواناکەم"
     "Cut it out"          →  "دەست پێوەردا بگرە"
   • ئەگەر ئیدیۆمێکی تر دیت، خۆت دەستەواژەی کوردی لێ بدۆزەرەوە.

③ ئاهەنگ و تۆنی گفتوگۆ بپارێزە
   • قسەی توڕەیانە  →  کوردی توڕەیانە
   • قسەی کوڵەپچانە  →  کوردی کوڵەپچانە
   • قسەی نەرمانە   →  کوردی نەرمانە
   • فەرمی / ئەکادیمی  →  کوردی وردبوونەوەیانە
   • کوردمانجی یان باشووری کوردستان نەکە — تەنها سۆرانی ناوەندی.

④ جێناوەکان و کارەکتەرەکان
   • تۆ → تۆ، من → من، ئێمە → ئێمە، ئەوان → ئەوان
   • ناوی کەسەکان مەگۆڕە.
   • ئەگەر دوو کەس قسە دەکەن لە یەک دێڕدا، بە "/" جیابکەرەوە.

⑤ قەدری ژێرنووس و دیالۆگی نێوخۆیی
   • هیچ دێڕێک مەپەڕێنە — هەتا دەنگی کەسێک دەبیستریت، دەبێت وەربگێڕدرێت.
   • دیالۆگی نێوخۆیی (خەیاڵ، بیرکردنەوە، داهاتوو)  →  وەرگێڕی بێ هیچ جیاوازی.
   • دەنگی کارەکتەری دووەم، باچکە، پشتزمین  →  هەموویان وەربگێڕە.
   • ژێرنووس دەبێت کورت و دروستەوەبێت — نەک درێژ و قرووقلی.

⑥ کاتەکان بپارێزە
   • "start" و "end" بە تەواوی وەک خۆیان بهێڵەوە — هیچ دەستکارییان مەکە.

⑦ بزرتەری خاڵبەندی
   • هیچ نیشانەی خاڵبەندییەک مەبەکاربهێنە: ؟ . : ! ـ ؛ " ' ? , ; - _
   • هیچ ئیموجییەک مەبەکاربهێنە.

⑧ نموونەی وەرگێڕانی باش
   EN: "Are you out of your mind?!"
   ❌ خراپ:  "ئایا دەرچوویت لە مێشکت؟"
   ✅ باش:   "مێشکت چوویەتە لای؟"

   EN: "I told you so."
   ❌ خراپ:  "من پێت گوتم ئاوا."
   ✅ باش:   "گوتم باوەڕم بکە."

   EN: "It's not what it looks like."
   ❌ خراپ:  "ئەوە نییە کە دیارە."
   ✅ باش:   "ئەوەندە ئاسان نییە."

═══════════════════════════════════════════════
Output: JSON array  —  هەمان درێژی inputەکە:
[{{"start":0.00,"end":1.50,"text":"..."}}]
═══════════════════════════════════════════════"""

    user_msg = f"Translate ALL cues:\n{json.dumps(chunk, ensure_ascii=False)}"

    # Build fallback model list — exclude lite model from auto-rotation
    fallback_models = [primary_model] + [
        m for m in MODEL_LIST
        if m != primary_model and m not in _FALLBACK_EXCLUDE
    ]
    valid_keys = [k.strip() for k in api_keys if k and k.strip()]
    if not valid_keys:
        st.error("❌ هیچ کلیلێکی دروست نەدۆزرایەوە.")
        return []

    ph = st.empty()
    key_idx   = 0   # current key index
    model_idx = 0   # current model index

    for attempt in range(90):  # 30 attempts × up to 3 keys
        cur_key   = valid_keys[key_idx   % len(valid_keys)]
        cur_model = fallback_models[model_idx % len(fallback_models)]

        try:
            client = genai.Client(api_key=cur_key)
            cfg_kwargs: dict = dict(
                system_instruction=system,
                temperature=0.2,
                response_mime_type="application/json",
            )
            # ── Thinking config: Gemini 3.x → thinking_level, Gemini 2.x → thinking_budget ──
            if _is_gemini3(cur_model):
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_level=thinking_budget   # "minimal" / "medium" / "high"
                )
            else:
                budget_int = _BUDGET_MAP.get(thinking_budget, 0)
                if budget_int == 0:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
                elif budget_int > 0:
                    cfg_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=budget_int)
                # -1 → omit (let model decide)

            resp = client.models.generate_content(
                model=cur_model,
                contents=[user_msg],
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
            data = extract_json(resp.text)
            ph.empty()
            if data:
                return data

        except Exception as e:
            err = str(e)

            # ── Rate-limit / quota → rotate KEY ──────────────────────
            if any(x in err for x in ("429", "RESOURCE_EXHAUSTED", "Quota exceeded")):
                key_idx += 1
                if key_idx >= len(valid_keys):
                    key_idx = 0          # wrap around; wait before retry
                    ph.warning(
                        f"⚠️ هەموو کلیلەکان سنووریان تێپەڕاوە. چاوەڕێ دەکرێت... "
                        f"(هەوڵی {attempt+1})"
                    )
                    time.sleep(60)
                else:
                    ph.warning(
                        f"⚠️ کلیلی {key_idx} سنووریی تێپەڕاوە — گواستنەوە بۆ کلیلی {key_idx+1}..."
                    )
                    time.sleep(3)
                continue

            # ── 503 / server overload → rotate MODEL ─────────────────
            if any(x in err for x in ("503", "UNAVAILABLE", "overloaded")):
                model_idx += 1
                ph.warning(
                    f"⚠️ مۆدێلی {cur_model} سەرشلۆ. "
                    f"گواستنەوە بۆ {fallback_models[model_idx % len(fallback_models)]}..."
                )
                time.sleep(5)
                continue

            # ── Any other error → show it; limit retries ─────────────
            ph.error(f"❌ هەڵەی نەناسراو (هەوڵی {attempt+1}/3):\n`{err}`")
            if attempt >= 2:
                ph.empty()
                return []
            time.sleep(2)

    ph.empty()
    return []

# ═══════════════════════════════════════════════════════════════════
#  FASTER-WHISPER
# ═══════════════════════════════════════════════════════════════════
@st.cache_resource
def load_whisper():
    return WhisperModel("medium", device="cpu", compute_type="int8")

def extract_audio(video_path: str, audio_path: str):
    # Simple clean extraction — loudnorm removed (distorts early speech)
    subprocess.run(
        ["ffmpeg", "-y", "-i", video_path,
         "-vn", "-ac", "1", "-ar", "16000",
         audio_path],
        capture_output=True, check=True,
    )

def transcribe_audio(audio_path: str) -> list:
    model  = load_whisper()
    kwargs = dict(
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=True,
        # ── پەست کردنی threshold بۆ دەستگیرکردنی قسەی خەیاڵ و داهاتوو ──
        no_speech_threshold=0.2,          # کرا بۆ 0.2 (پێشتر 0.4) — قسەی کەمیش دەگرێت
        compression_ratio_threshold=2.6,  # کرا بۆ 2.6 — دەقی زۆرتر دەپارێزێت
        temperature=0.0,
        vad_parameters=dict(
            min_silence_duration_ms=200,  # کرا بۆ 200ms (پێشتر 250) — نێوانی کورتتر
            threshold=0.35,               # VAD sensitivity بەرز — قسەی نەرمیش دەگرێت
        ),
        # ئاگادارکردنەوەی Whisper کە دیالۆگی ناوخۆیی، خەیاڵ، دەنگی باچکە هەمووی گرنگن
        initial_prompt=(
            "This is an anime episode dialogue. Include all speech: "
            "narration, inner monologue, imagination sequences, "
            "background characters, and whispered lines."
        ),
    )

    segments, _ = model.transcribe(audio_path, **kwargs)

    cues, buf, t0, t1 = [], [], None, None

    def flush():
        nonlocal buf, t0, t1
        if buf and t0 is not None and t1 is not None:
            cues.append({"start": round(t0,2), "end": round(t1,2), "text": " ".join(buf)})
        buf, t0, t1 = [], None, None

    for seg in segments:
        # ── CRITICAL FIX: segment-level fallback when words absent ────
        if not seg.words:
            flush()
            seg_text = str(seg.text).strip()
            if seg_text:
                cues.append({
                    "start": round(float(seg.start), 2),
                    "end":   round(float(seg.end),   2),
                    "text":  seg_text,
                })
            continue

        for w in seg.words:
            ws, we = float(w.start), float(w.end)
            wt = str(w.word).strip()
            if not wt:
                continue
            if t0 is None:
                t0 = ws
            # Silence gap → new cue
            if t1 is not None and (ws - t1) > 0.3:
                flush()
                t0 = ws
            buf.append(wt)
            t1 = we
            # Max duration or sentence boundary
            if (we - t0 >= MAX_SUB_DURATION) or wt[-1] in ".!?؟":
                flush()

    flush()
    return cues

# ═══════════════════════════════════════════════════════════════════
#  CHUNK BUILDER
# ═══════════════════════════════════════════════════════════════════
def build_chunks(cues: list, minutes: float) -> list:
    max_s = minutes * 60
    chunks, cur, cs = [], [], None
    for item in cues:
        if cs is None:
            cs = item["start"]
        if item["end"] - cs > max_s:
            chunks.append(cur); cur = [item]; cs = item["start"]
        else:
            cur.append(item)
    if cur:
        chunks.append(cur)
    return chunks

# ═══════════════════════════════════════════════════════════════════
#  THROTTLE
# ═══════════════════════════════════════════════════════════════════
def throttle_countdown(seconds: int = THROTTLE_SECONDS):
    ph = st.empty()
    for i in range(seconds, 0, -1):
        ph.info(f"⏳ پاراستنی سێرڤەر: {i} چرکە پشوو دەدەین...")
        time.sleep(1)
    ph.empty()

# ═══════════════════════════════════════════════════════════════════
#  ORCHESTRATOR  (Smart Resume + Cascade Keys + Model Fallback)
# ═══════════════════════════════════════════════════════════════════
def process_full_video(
    api_keys: list,
    video_path: str,
    primary_model: str,
    thinking_budget: int,
    chunk_minutes: float,
    existing_raw: str = "",
) -> str:

    # ── Resume point ──────────────────────────────────────────────
    last_sec = 0.0
    if existing_raw.strip():
        prev = parse_raw_text(existing_raw)
        if prev:
            last_sec = secs(prev[-1]["end"])

    audio_path = os.path.splitext(video_path)[0] + ".wav"

    with st.spinner("🎵 دەرهێنانی دەنگ..."):
        extract_audio(video_path, audio_path)

    with st.spinner("📝 نووسینەوە (Whisper)..."):
        cues = transcribe_audio(audio_path)
        try:
            os.remove(audio_path)
        except Exception:
            pass
        if not cues:
            st.error("❌ هیچ دیالۆگێک نەدۆزرایەوە.")
            return existing_raw

    with st.spinner("🧠 وەرگێڕان بۆ کوردی سۆرانی سینەمایی..."):
        all_chunks = build_chunks(cues, chunk_minutes)

        # ── Smart Resume: skip done chunks, trim partial ───────────
        todo = []
        for ch in all_chunks:
            if not ch or ch[-1]["end"] <= last_sec:
                continue
            filtered = [c for c in ch if c["end"] > last_sec]
            if filtered:
                todo.append(filtered)

        if not todo:
            st.info("✅ هەموو ڤیدیۆکە پێشتر وەرگێڕاوە.")
            return existing_raw

        total = len(todo)
        prog  = st.progress(0, text="⏳ دەستپێکردن...")
        new_cues: list = []
        failed_chunks: list = []

        for i, ch in enumerate(todo):
            chunk_label = f"پارچەی {i+1} لە {total}"
            translated = []

            # ── Chunk-level retry: max 3 attempts ──────────────────────
            for retry in range(3):
                translated = gemini_translate(
                    api_keys, ch, primary_model, thinking_budget
                )
                if translated:
                    break
                if retry < 2:
                    st.warning(
                        f"⚠️ {chunk_label} بەتاڵ گەڕایەوە — "
                        f"هەوڵی {retry+2}/3 دەدەین..."
                    )
                    time.sleep(10)

            if translated:
                new_cues.extend(translated)
            else:
                failed_chunks.append(i + 1)
                st.error(
                    f"❌ {chunk_label} سێ جار هەوڵی دراوە و سەرکەوتوو نەبوو — "
                    f"بەردەوام بوون لە پارچەی داهاتوو..."
                )

            pct = round((i + 1) / total * 100)
            prog.progress(
                (i + 1) / total,
                text=f"🔄 {pct}% کراوە — {chunk_label}"
            )
            if i < total - 1:
                throttle_countdown()

        if failed_chunks:
            st.warning(
                f"⚠️ ئەم پارچانە وەرگێڕانیان نەبوو: {failed_chunks}  "
                f"— دەتوانیت بە 'بەردەوام بوون' دیسان هەوڵ بدەیت."
            )

        new_cues.sort(key=lambda x: x["start"])
        validated = validate_cues(new_cues)

    lines = [
        f"{float_to_ass(c['start'])} --> {float_to_ass(c['end'])} | {c['text']}"
        for c in validated
    ]
    new_raw = "\n".join(lines)

    if existing_raw.strip() and new_raw:
        return existing_raw.rstrip() + "\n" + new_raw
    return new_raw or existing_raw

# ═══════════════════════════════════════════════════════════════════
#  ASS / SRT BUILDERS
# ═══════════════════════════════════════════════════════════════════
def hex_to_ass(h: str) -> str:
    h = h.lstrip("#").upper().ljust(6, "0")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"

def get_video_resolution(video_path: str) -> tuple:
    """Returns (width, height) of video, defaults to (1280,720) on failure."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True,
        )
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1280, 720

def build_ass_file(
    cues: list,
    font_size: int,
    wm_text: str,
    wm_color: str,
    wm_font_size: int,
    wm_align: int,
    video_path: str = "",
) -> str:
    fn  = find_kurdish_font()
    wma = hex_to_ass(wm_color)
    vw, vh = get_video_resolution(video_path) if video_path else (1280, 720)

    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {vw}",
        f"PlayResY: {vh}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{fn},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        f"Style: CornerStyle,{fn},30,&H00E0E0E0,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,1.5,0,9,20,20,20,1",
        f"Style: WatermarkStyle,Arial,{wm_font_size},{wma},&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,0,0,1,1.5,0,7,15,20,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events = []
    if wm_text:
        events.append(
            f"Dialogue: 0,0:00:00.00,9:59:59.99,WatermarkStyle,,0,0,0,,{{\\an{wm_align}}}{wm_text}"
        )

    for c in cues:
        raw_text = c.get("text", "")
        # ── CRITICAL: هەرگیز تاگی ASS دەستکاری مەکە ({\ دەتەوێت بمێنێتەوە) ──
        if "{\\" in raw_text:
            txt = raw_text          # intro / credits — تەواو بهێڵەوە
        else:
            txt = clean_punctuation(raw_text)   # دیالۆگی ئاسایی
        a_tag = c.get("alignment_tag", "{\\an2}")
        style = c.get("style", "Default")
        events.append(f"Dialogue: 0,{c['start']},{c['end']},{style},,0,0,0,,{a_tag}{txt}")

    return "\n".join(header + events)

def build_srt_file(cues: list) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        s   = sec_to_srt(secs(c["start"]))
        e   = sec_to_srt(secs(c["end"]))
        txt = clean_punctuation(re.sub(r'\{\\[^}]*\}', '', c.get("text", "")))
        out.append(f"{i}\n{s} --> {e}\n{txt}\n")
    return "\n".join(out)

# ═══════════════════════════════════════════════════════════════════
#  FFMPEG
# ═══════════════════════════════════════════════════════════════════
def burn_subtitles(video: str, ass: str, out: str):
    subprocess.run(
        ["ffmpeg", "-y", "-i", video,
         "-vf", f"ass={ass}:fontsdir=/tmp",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "25",
         "-c:a", "copy", out],
        capture_output=True, check=True,
    )

def auto_dl(data: bytes, name: str, mime: str):
    b64 = base64.b64encode(data).decode()
    components.html(
        f'<a id="xdl" href="data:{mime};base64,{b64}" download="{name}"></a>'
        '<script>setTimeout(()=>document.getElementById("xdl").click(),800)</script>',
        height=0,
    )

# ═══════════════════════════════════════════════════════════════════
#  MAIN UI
# ═══════════════════════════════════════════════════════════════════
def main():

    # ── _cleanup_sub_session lives here → zero NameError risk ─────
    def _cleanup_sub_session():
        for k in ["sub_raw", "sub_input_path", "sub_temp_dir"]:
            st.session_state.pop(k, None)
        st.rerun()
    # ─────────────────────────────────────────────────────────────

    st.set_page_config(page_title="🎬 Sorani Subtitle Studio", layout="wide")
    inject_background()
    st.title("🎬 Kurdish Sorani Cinematic Subtitle Generator")

    # ── Session state init ────────────────────────────────────────
    for k in ["sub_raw", "sub_input_path", "sub_temp_dir"]:
        st.session_state.setdefault(k, None)

    # ════════════════════════════════════════════════════════════
    #  SIDEBAR  — settings
    # ════════════════════════════════════════════════════════════
    with st.sidebar:
        st.header("⚙️ ڕێکخستنەکان")

        st.subheader("🔑 کلیلەکانی Gemini API")
        keys = [
            st.text_input(f"کلیلی {i+1}", type="password", key=f"key_{i}")
            for i in range(4)
        ]
        valid_keys = [k.strip() for k in keys if k and k.strip()]

        st.markdown("---")
        primary_model = st.selectbox("🤖 مۆدێلی AI", MODEL_LIST)

        thinking_label  = st.selectbox("🧠 جۆری بیرکردنەوە", list(THINKING_MAP.keys()))
        thinking_budget = THINKING_MAP[thinking_label]   # "minimal" / "medium" / "high"

        st.markdown("---")
        chunk_minutes = st.slider("⏱️ قەبارەی پارچەکان (خولەک)", 3, 15, 5)

        st.markdown("---")
        font_size = st.slider("📐 قەبارەی فۆنت", 20, 80, 52)

    # ════════════════════════════════════════════════════════════
    #  MAIN AREA
    # ════════════════════════════════════════════════════════════
    video_file = st.file_uploader(
        "📁 ڤیدیۆ بار بکە (MP4 / MOV / MKV / AVI / WEBM / M4V / FLV / TS / WMV)",
        type=["mp4", "mov", "mkv", "avi", "webm", "m4v", "flv", "ts", "wmv"],
    )

    st.markdown("---")

    # ── Intro / Credits ──────────────────────────────────────────
    with st.expander("ℹ️ زانیاری ناساندنی دەستپێک", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            anime_name      = st.text_input("🎬 ناوی فیلم / زنجیرە")
            translator_name = st.text_input("✍️ ناوی وەرگێڕ")
        with c2:
            season_ep = st.text_input("📺 سیزن / ئەڵقە")
            tech_name = st.text_input("💻 ناوی تەکنیک")
        intro_dur = st.number_input(
            "⏱️ کاتی کرێدیتەکان (چرکە)", min_value=1.0, max_value=15.0, value=3.0, step=0.5
        )

    # ── Watermark ────────────────────────────────────────────────
    with st.expander("🎨 واتەرمارک", expanded=False):
        w1, w2, w3, w4 = st.columns(4)
        with w1: wm_text      = st.text_input("📝 نووسینی واتەرمارک")
        with w2: wm_color     = st.color_picker("🎨 ڕەنگ", "#FFFFFF")
        with w3: wm_font_size = st.slider("📏 قەبارە", 10, 150, 30)
        with w4:
            wm_pos   = st.selectbox("📍 شوێن", ["چەپ", "ڕاست"])
            wm_align = 7 if wm_pos == "چەپ" else 9

    st.markdown("---")

    # ── Action buttons ───────────────────────────────────────────
    b1, b2, b3 = st.columns([3, 3, 1])

    with b1:
        start_btn = st.button(
            "🧠 ١. دەرهێنان و وەرگێڕان",
            type="primary",
            use_container_width=True,
        )
    with b2:
        can_resume = bool(
            st.session_state.sub_raw
            and st.session_state.sub_input_path
            and os.path.exists(st.session_state.sub_input_path or "")
        )
        resume_btn = st.button(
            "▶️ بەردەوام بوون",
            disabled=not can_resume,
            use_container_width=True,
        )
    with b3:
        reset_btn = st.button("🔄 سفر", use_container_width=True)

    # ── Button logic ─────────────────────────────────────────────
    if reset_btn:
        _cleanup_sub_session()

    if start_btn:
        if not valid_keys:
            st.error("❌ کەمێک کلیلی Gemini بنووسە."); st.stop()
        if not video_file:
            st.error("❌ ڤیدیۆ بار بکە."); st.stop()

        tmp = tempfile.mkdtemp()
        ext = os.path.splitext(video_file.name)[-1] or ".mp4"
        in_p = os.path.join(tmp, f"input{ext}")
        with open(in_p, "wb") as f:
            f.write(video_file.read())

        st.session_state.sub_temp_dir   = tmp
        st.session_state.sub_input_path = in_p
        st.session_state.sub_raw        = None

        result = process_full_video(
            valid_keys, in_p, primary_model, thinking_budget,
            chunk_minutes,
        )
        if result:
            st.session_state.sub_raw = result
            st.rerun()

    if resume_btn:
        if not valid_keys:
            st.error("❌ کلیلی Gemini نوێ بنووسە."); st.stop()
        result = process_full_video(
            valid_keys,
            st.session_state.sub_input_path,
            primary_model,
            thinking_budget,
            chunk_minutes,
            existing_raw=st.session_state.sub_raw,
        )
        if result:
            st.session_state.sub_raw = result
            st.rerun()

    # ── Editor + Burn ────────────────────────────────────────────
    if st.session_state.sub_raw:
        st.success("✅ وەرگێڕان تەواو بوو! دەتوانیت دەسکاریی بکەیت.")

        delay = st.slider(
            "⏱️ شوێنکردنەوەی کاتی ژێرنووس (چرکە)", -15.0, 15.0, 0.0, 0.05
        )
        displayed = (
            shift_transcript(st.session_state.sub_raw, delay)
            if delay != 0.0
            else st.session_state.sub_raw
        )
        edited = st.text_area("📝 دەسکاریکردن پێش لکاندن", value=displayed, height=420)

        if st.button("🔥 ٢. لکاندنی ژێرنووس بە ڤیدیۆ", type="primary", use_container_width=True):
            cues = parse_raw_text(edited)
            if not cues:
                st.error("❌ ستەرەکان ناناسرێنەوە."); st.stop()

            tmp   = st.session_state.sub_temp_dir
            in_p  = st.session_state.sub_input_path
            ass_p = os.path.join(tmp, "subs.ass")
            srt_p = os.path.join(tmp, "subs.srt")
            out_p = os.path.join(tmp, "output.mp4")

            # ── Build intro cues ──────────────────────────────
            intro, t = [], 0.0

            if anime_name:
                label = anime_name + (f"\\N({season_ep})" if season_ep else "")
                intro.append({
                    "start": "0:00:00.00", "end": "0:00:15.00",
                    "style": "CornerStyle",
                    "alignment_tag": "{\\an9}",
                    "text": label,
                })

            if translator_name:
                end = t + intro_dur
                intro.append({
                    "start": float_to_ass(t), "end": float_to_ass(end),
                    "alignment_tag": "{\\an2}",
                    "text": f"وەرگێڕان\\N{translator_name}",
                })
                t = end

            if tech_name:
                end = t + intro_dur
                intro.append({
                    "start": float_to_ass(t), "end": float_to_ass(end),
                    "alignment_tag": "{\\an2}",
                    "text": f"تەکنیک\\N{tech_name}",
                })
                t = end

            # Push dialogue up if credits overlap
            has_bottom = bool(translator_name or tech_name)
            for c in cues:
                if has_bottom and secs(c["start"]) < t:
                    c["alignment_tag"] = "{\\an8}"
                else:
                    c.setdefault("alignment_tag", "{\\an2}")

            full_cues = intro + cues
            ass_txt   = build_ass_file(full_cues, font_size, wm_text, wm_color, wm_font_size, wm_align, video_path=in_p)
            srt_txt   = build_srt_file(cues)

            with open(ass_p, "w", encoding="utf-8") as f: f.write(ass_txt)
            with open(srt_p, "w", encoding="utf-8") as f: f.write(srt_txt)

            with st.spinner("🔥 لکاندن (FFmpeg)..."):
                try:
                    burn_subtitles(in_p, ass_p, out_p)
                except subprocess.CalledProcessError as e:
                    st.error(f"❌ هەڵەی FFmpeg:\n`{e.stderr.decode() if e.stderr else e}`")
                    st.stop()

            st.success("🎉 بە سەرکەوتوویی تەواو بوو!")
            with open(out_p, "rb") as f:
                vb = f.read()

            auto_dl(vb, "subtitled.mp4", "video/mp4")

            d1, d2, d3 = st.columns(3)
            d1.download_button("⬇️ ڤیدیۆ",  vb,      "subtitled.mp4", "video/mp4",  use_container_width=True)
            d2.download_button("⬇️ SRT",    srt_txt, "subtitle.srt",  "text/plain", use_container_width=True)
            d3.download_button("⬇️ ASS",    ass_txt, "subtitle.ass",  "text/plain", use_container_width=True)
            st.video(vb)


if __name__ == "__main__":
    main()
