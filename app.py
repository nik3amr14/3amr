import os
import re
import time
import base64
import shutil
import tempfile
import subprocess

import streamlit as st
import streamlit.components.v1 as components
from faster_whisper import WhisperModel

# ── هێێنانی مێشکی وەرگێڕان لە فایلەکەی کڵاودەوە ──
from ai_translator import gemini_translate

# ═══════════════════════════════════════════════════════════════════
#  AUTO-GENERATE STREAMLIT CONFIG
# ═══════════════════════════════════════════════════════════════════
APP_DIR  = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)

_CFG_DIR  = os.path.join(APP_DIR, ".streamlit")
_CFG_FILE = os.path.join(_CFG_DIR, "config.toml")
if not os.path.exists(_CFG_FILE):
    os.makedirs(_CFG_DIR, exist_ok=True)
    with open(_CFG_FILE, "w") as _f:
        _f.write("[server]\nmaxUploadSize = 700\n\n[browser]\ngatherUsageStats = false\n")

# ═══════════════════════════════════════════════════════════════════
#  CONSTANTS & UTILS
# ═══════════════════════════════════════════════════════════════════
KU_FONT_FILE = "Bahij Janna-Bold.ttf"
KU_FONT_PATH = os.path.join("/tmp", KU_FONT_FILE)
KU_FONT_NAME = "Bahij Janna"
MAX_SUB_DURATION = 4.0

MODEL_LIST = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite",
]

# مۆدی زمانە فەرمییەکان بۆ لۆجیکی قوفڵکردن [25]
LANG_MAP = {
    "Auto-Detect (خۆکارانە بدۆزەرەوە)": None,
    "Japanese (ژاپۆنی)": "ja",
    "English (ئینگلیزی)": "en",
    "Persian (فارسی)": "fa",
    "Arabic (عەرەبی)": "ar"
}

THINKING_MAP = {
    "⚡ Ultra Fast  (بێ بیرکردنەوە)": "minimal",
    "⚖️ Standard / Balanced": "medium",
    "🧠 Deep / Precise  (بیرکردنەوەی بەرز)": "high",
}

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
        bg_css = f"background-image: linear-gradient(rgba(0,0,0,0.88),rgba(0,0,0,0.88)), url('data:{mime};base64,{b64}'); background-size: cover; background-attachment: fixed;"
    else:
        bg_css = "background-color: #121212;"

    st.markdown(f"<style>.stApp {{ {bg_css} }} section[data-testid='stSidebar'] {{ background-color: rgba(18,18,18,0.95); }}</style>", unsafe_allow_html=True)

def find_kurdish_font() -> str:
    possible_paths = [os.path.join(APP_DIR, KU_FONT_FILE), os.path.join(ROOT_DIR, KU_FONT_FILE), KU_FONT_FILE]
    for path in possible_paths:
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            try:
                os.makedirs("/tmp", exist_ok=True)
                shutil.copy(path, KU_FONT_PATH)
                return KU_FONT_NAME
            except: pass
    return "Arial"

def secs(ts: str) -> float:
    try:
        ts = ts.strip().replace(",", ".")
        h, m, sf = ts.split(":")
        s, frac = (sf.split(".", 1) + ["0"])[:2]
        return int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
    except: return 0.0

def float_to_ass(t: float) -> str:
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60); cs = int((t - int(t)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def clean_punctuation(t: str) -> str:
    bad_chars = "؟.:!ـ؛”’?,;\"'!-_()[]{}،,+=*#$@^&|~`"
    for char in bad_chars: t = t.replace(char, "")
    return " ".join(t.split())

_CUE_RE = re.compile(r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*[|\t]\s*(.+)")

def parse_raw_text(raw: str) -> list:
    out = []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m: out.append({"start": m.group(1).replace(",", "."), "end": m.group(2).replace(",", "."), "text": m.group(3).strip()})
    return out

def shift_transcript(raw: str, delay: float) -> str:
    if delay == 0.0: return raw
    lines = []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            ns = max(0.0, secs(m.group(1)) + delay)
            ne = max(0.0, secs(m.group(2)) + delay)
            lines.append(f"{float_to_ass(ns)} --> {float_to_ass(ne)} | {m.group(3)}")
        else: lines.append(line)
    return "\n".join(lines)

def validate_cues(cues: list) -> list:
    out, cs, ce = [], 0.0, 0.0
    for c in cues:
        try: ns = float(c["start"]); ne = float(c["end"]); nt = str(c.get("text","")).strip()
        except: continue
        if not nt or ne <= ns: continue
        if ns < cs: ns = ce
        if ne <= ns: continue
        out.append({"start": round(ns,3), "end": round(ne,3), "text": nt})
        cs, ce = ns, ne
    return out

# ═══════════════════════════════════════════════════════════════════
#  FASTER-WHISPER & AUDIO (Auto-Language Detection & Locking)
# ═══════════════════════════════════════════════════════════════════
@st.cache_resource
def load_whisper():
    return WhisperModel("medium", device="cpu", compute_type="int8")

def extract_audio(video_path: str, audio_path: str):
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", "-af", "dynaudnorm=f=150:g=15", audio_path], capture_output=True, check=True)

def transcribe_audio(audio_path: str, selected_lang: str = None) -> list:
    model = load_whisper()
    
    # ناسینەوە و قفڵکردنی خۆکارانەی زمانی ڤیدیۆ پێش دەستپێکردنی وەرگێڕان [25]
    if not selected_lang:
        _, info = model.transcribe(audio_path, beam_size=1)
        detected_lang = info.language
        lang_prob = info.language_probability
        st.info(f"🌐 زمانی زاڵی ڤیدیۆکە بە خۆکاری ئاشکرا کرا: **[{detected_lang.upper()}]** (بڕواپێکراوی: {int(lang_prob*100)}٪)")
        final_lang = detected_lang
    else:
        st.info(f"🌐 زمانەکە بە دەستی قوفڵ کراوە لەسەر: **[{selected_lang.upper()}]**")
        final_lang = selected_lang
    
    kwargs = dict(
        beam_size=5, word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
        no_speech_threshold=0.25, compression_ratio_threshold=2.4, temperature=0.0,
        vad_parameters=dict(min_silence_duration_ms=300),
        language=final_lang
    )
    segments, _ = model.transcribe(audio_path, **kwargs)
    cues, buf, t0, t1 = [], [], None, None

    def flush():
        nonlocal buf, t0, t1
        if buf and t0 is not None and t1 is not None:
            cues.append({"start": round(t0,2), "end": round(t1,2), "text": " ".join(buf)})
        buf, t0, t1 = [], None, None

    for seg in segments:
        if not seg.words:
            flush()
            seg_text = str(seg.text).strip()
            if seg_text: cues.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": seg_text})
            continue

        for w in seg.words:
            ws, we, wt = float(w.start), float(w.end), str(w.word).strip()
            if not wt: continue
            if t0 is None: t0 = ws
            if t1 is not None and (ws - t1) > 0.3: flush(); t0 = ws
            buf.append(wt)
            t1 = we
            if (we - t0 >= MAX_SUB_DURATION) or wt[-1] in ".!?؟": flush()
    flush()
    return cues

def build_chunks(cues: list, minutes: float) -> list:
    max_s = minutes * 60
    chunks, cur, cs = [], [], None
    for item in cues:
        if cs is None: cs = item["start"]
        if item["end"] - cs > max_s and cur:
            chunks.append(cur); cur = [item]; cs = item["start"]
        else: cur.append(item)
    if cur: chunks.append(cur)
    return chunks

# ═══════════════════════════════════════════════════════════════════
#  ORCHESTRATOR 
# ═══════════════════════════════════════════════════════════════════
def process_full_video(api_keys, video_path, primary_model, thinking_budget, chunk_minutes, selected_lang=None, existing_raw=""):
    audio_path = video_path.replace(".mp4", ".wav")
    last_translated_sec = parse_existing_raw_to_last_time(existing_raw)
    
    with st.spinner("🎵 خەریکی دەرهێنانی دەنگ و سافکردنیەتی (Audio Normalization)..."):
        extract_audio(video_path, audio_path)

    with st.spinner("📝 خەریکی نووسینەوەی دەنگەکەیە بە وردی (Faster-Whisper)..."):
        cues = transcribe_audio(audio_path, selected_lang=selected_lang)
        try: os.remove(audio_path)
        except: pass
        if not cues:
            st.error("❌ هیچ دیالۆگێک لە ڤیدیۆکەدا نەدۆزرایەوە.")
            return existing_raw

    with st.spinner("🧠 وەرگێڕان بۆ کوردی سۆرانی..."):
        all_chunks = build_chunks(cues, chunk_minutes)
        todo = [ch for ch in all_chunks if ch and ch[-1]["end"] > last_sec]
        todo = [[c for c in ch if c["end"] > last_sec] for ch in todo if [c for c in ch if c["end"] > last_sec]]

        if not todo:
            st.info("✅ هەموو ڤیدیۆکە پێشتر وەرگێڕاوە.")
            return existing_raw

        total = len(todo)
        prog = st.progress(0, text="⏳ دەستپێکردن...")
        new_cues, current_key_index = [], 0
        status_msg = st.empty()

        for i, ch in enumerate(todo):
            chunk_label = f"بڕگەی {i+1} لە {total}"
            pct = int((i / total) * 100)
            prog.progress(i / total, text=f"🔄 لە ٪{pct} ی ڤیدیۆکە تەواو بووە... ({chunk_label})")

            # بانگکردنی فەنکشنەکە لە فایلەکەی کڵاودەوە
            translated, current_key_index = gemini_translate(
                api_keys=api_keys, 
                current_key_index=current_key_index, 
                transcript_chunk=ch, 
                thinking_budget=thinking_budget, 
                selected_model=primary_model,
                status_msg=status_msg
            )
            
            if not translated:
                st.error(f"❌ پڕۆسەکە وەستا لە {chunk_label}. تکایە دواتر 'بەردەوام بوون' دابگرە.")
                break
                
            new_cues.extend(translated)
            if i < total - 1: time.sleep(2)

        prog.progress(1.0, text="🎉 لە ٪100 تەواو بوو!")
        status_msg.empty()

        new_cues.sort(key=lambda x: x["start"])
        validated = validate_cues(new_cues)

    lines = [f"{float_to_ass(c['start'])} --> {float_to_ass(c['end'])} | {c['text']}" for c in validated]
    new_raw = "\n".join(lines)

    if existing_raw.strip() and new_raw: return existing_raw.rstrip() + "\n" + new_raw
    return new_raw or existing_raw

# ═══════════════════════════════════════════════════════════════════
#  ASS BUILDER & FFMPEG
# ═══════════════════════════════════════════════════════════════════
def hex_to_ass(h: str) -> str:
    h = h.lstrip("#").upper().ljust(6, "0")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"

def get_video_resolution(video_path: str) -> tuple:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path], capture_output=True, text=True, check=True)
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except: return 1280, 720

def build_ass_file(cues: list, font_size: int, wm_text: str, wm_color: str, wm_font_size: int, wm_align: int, video_path: str = "") -> str:
    fn = find_kurdish_font()
    wma = hex_to_ass(wm_color)
    vw, vh = get_video_resolution(video_path) if video_path else (1280, 720)

    header = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {vw}", f"PlayResY: {vh}", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{fn},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        f"Style: CornerStyle,{fn},30,&H00E0E0E0,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,9,20,20,20,1",
        f"Style: WatermarkStyle,Arial,{wm_font_size},{wma},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1.5,0,7,15,20,20,1",
        f"Style: TranslatorStyle,{fn},40,&H0000FF00,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        f"Style: TechStyle,{fn},40,&H00FFFF00,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    events = []
    if wm_text: events.append(f"Dialogue: 0,0:00:00.00,9:59:59.99,WatermarkStyle,,0,0,0,,{{\\an{wm_align}}}{wm_text}")

    for c in cues:
        txt   = clean_punctuation(c.get("text", ""))
        a_tag = c.get("alignment_tag", "{\\an2}")
        style = c.get("style", "Default")
        events.append(f"Dialogue: 0,{c['start']},{c['end']},{style},,0,0,0,,{a_tag}{txt}")

    return "\n".join(header + events)

def burn_subtitles(video: str, ass: str, out: str):
    subprocess.run(["ffmpeg", "-y", "-i", video, "-vf", f"ass={ass}:fontsdir=/tmp", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-c:a", "copy", out], capture_output=True, check=True)

def auto_dl(data: bytes, name: str, mime: str):
    b64 = base64.b64encode(data).decode()
    components.html(f'<a id="xdl" href="data:{mime};base64,{b64}" download="{name}"></a><script>setTimeout(()=>document.getElementById("xdl").click(),800)</script>', height=0)

# ══════════════════════════════════════════════════════════
#  MAIN UI
# ══════════════════════════════════════════════════════════
def main():
    def _cleanup_sub_session():
        for k in ["sub_raw", "sub_input_path", "sub_temp_dir"]: st.session_state.pop(k, None)
        st.rerun()

    st.set_page_config(page_title="Sorani Subtitle Studio", layout="wide")
    inject_background()
    st.title("🎬 Kurdish Sorani Subtitle Generator")

    for k in ["sub_raw", "sub_input_path", "sub_temp_dir"]: st.session_state.setdefault(k, None)

    with st.sidebar:
        st.header("⚙️ ڕێکخستنەکان")
        st.subheader("🔑 کلیلەکانی Gemini API")
        keys = [st.text_input(f"کلیلی {i+1}", type="password", key=f"key_{i}") for i in range(4)]
        valid_keys = [k.strip() for k in keys if k and k.strip()]

        st.markdown("---")
        primary_model = st.selectbox("🤖 مۆدێلی AI", MODEL_LIST, index=0)
        thinking_label = st.selectbox("🧠 جۆری بیرکردنەوە", list(THINKING_MAP.keys()), index=1)
        thinking_budget = THINKING_MAP[thinking_label]

        # 🌐 سایدباری هەڵبژاردنی زمان بە ڕیزبەندی ئەلفابێتی زۆر خاوێن
        st.markdown("---")
        st.subheader("🌐 زمانی ڤیدیۆکە")
        lang_choice = st.selectbox(
            "زمانەکە بە دەستی دیاری بکە:",
            list(LANG_MAP.keys()),
            index=0,
            help="دەتوانیت پیت بنووسیت بۆ گەڕانی خێرا بەدوای زمانەکەدا."
        )
        selected_lang = LANG_MAP[lang_choice]

        st.markdown("---")
        chunk_minutes = st.slider("⏱️ قەبارەی پارچەکان (خولەک)", 3, 15, 4)
        st.markdown("---")
        font_size = st.slider("📐 قەبارەی فۆنت", 20, 80, 52)

    video_file = st.file_uploader("📁 ڤیدیۆ بار بکە (MP4 / MOV / MKV / AVI / WEBM / M4V / FLV / TS / WMV)", type=["mp4", "mov", "mkv", "avi", "webm", "m4v", "flv", "ts", "wmv"])
    st.markdown("---")

    with st.expander("ℹ️ زانیاری ناساندنی دەستپێک", expanded=False):
        c1, c2 = st.columns(2)
        with c1:
            anime_name = st.text_input("🎬 ناوی فیلم / زنجیرە")
            translator_name = st.text_input("✍️ ناوی وەرگێڕ")
        with c2:
            season_ep = st.text_input("📺 سیزن / ئەڵقە")
            tech_name = st.text_input("💻 ناوی تەکنیک")
        intro_dur = st.number_input("⏱️ کاتی کرێدیتەکان (چرکە)", min_value=1.0, max_value=15.0, value=3.0, step=0.5)

    with st.expander("🎨 واتەرمارک", expanded=False):
        w1, w2, w3, w4 = st.columns(4)
        with w1: wm_text = st.text_input("📝 نووسینی واتەرمارک")
        with w2: wm_color = st.color_picker("🎨 ڕەنگی لۆگۆ", "#FFFFFF")
        with w3: wm_font_size = st.slider("📏 قەبارە", 10, 150, 30)
        with w4:
            wm_pos = st.selectbox("📍 شوێن", ["چەپ", "ڕاست"])
            wm_alignment = 7 if wm_pos == "چەپ" else 9

    st.markdown("---")
    delay_seconds = st.slider("⏱️ شوێنکردنەوەی کاتی ژێرنووس (چرکە)", -15.0, 15.0, 0.0, 0.05)
    st.markdown("---")

    b1, b2, b3 = st.columns([3, 3, 1])
    with b1: start_btn = st.button("🧠 ١. دەرهێنان و وەرگێڕان", type="primary", use_container_width=True)
    with b2:
        can_resume = bool(st.session_state.sub_raw and st.session_state.sub_input_path and os.path.exists(st.session_state.sub_input_path or ""))
        resume_btn = st.button("▶️ بەردەوام بوون", disabled=not can_resume, use_container_width=True)
    with b3: reset_btn = st.button("🔄 سفر", use_container_width=True)

    if reset_btn: _cleanup_sub_session()

    if start_btn:
        if not valid_keys: st.error("❌ کەمێک کلیلی Gemini بنووسە."); st.stop()
        if not video_file: st.error("❌ ڤیدیۆ بار بکە."); st.stop()

        tmp = tempfile.mkdtemp()
        ext = os.path.splitext(video_file.name)[-1] or ".mp4"
        in_p = os.path.join(tmp, f"input{ext}")
        with open(in_p, "wb") as f: f.write(video_file.read())

        st.session_state.sub_temp_dir = tmp
        st.session_state.sub_input_path = in_p
        st.session_state.sub_raw = None

        result = process_full_video(valid_keys, in_p, primary_model, thinking_budget, chunk_minutes, selected_lang=selected_lang)
        if result:
            st.session_state.sub_raw = result
            st.rerun()

    if resume_btn:
        if not valid_keys: st.error("❌ کلیلی Gemini نوێ بنووسە."); st.stop()
        result = process_full_video(valid_keys, st.session_state.sub_input_path, primary_model, thinking_budget, chunk_minutes, selected_lang=selected_lang, existing_raw=st.session_state.sub_raw)
        if result:
            st.session_state.sub_raw = result
            st.rerun()

    if st.session_state.sub_raw:
        st.success("✅ وەرگێڕان تەواو بوو! دەتوانیت دەسکاریی بکەیت.")
        displayed = shift_transcript(st.session_state.sub_raw, delay_seconds) if delay_seconds != 0.0 else st.session_state.sub_raw
        edited = st.text_area("📝 دەسکاریکردن پێش لکاندن", value=displayed, height=420)

        if st.button("🔥 ٢. لکاندنی ژێرنووس بە ڤیدیۆ", type="primary", use_container_width=True):
            cues = parse_raw_text(edited)
            if not cues: st.error("❌ ستەرەکان ناناسرێنەوە."); st.stop()

            tmp = st.session_state.sub_temp_dir
            in_p = st.session_state.sub_input_path
            ass_p = os.path.join(tmp, "subs.ass")
            out_p = os.path.join(tmp, "output.mp4")

            intro, t = [], 0.0
            if anime_name:
                label = anime_name + (f"\\N({season_ep})" if season_ep else "")
                intro.append({"start": "0:00:00.00", "end": "0:00:15.00", "style": "CornerStyle", "alignment_tag": "{\\an9}", "text": label})
            if translator_name:
                end = t + intro_dur
                intro.append({"start": float_to_ass(t), "end": float_to_ass(end), "style": "TranslatorStyle", "alignment_tag": "{\\an2}", "text": f"وەرگێڕان\\N{translator_name}"})
                t = end
            if tech_name:
                end = t + intro_dur
                intro.append({"start": float_to_ass(t), "end": float_to_ass(end), "style": "TechStyle", "alignment_tag": "{\\an2}", "text": f"تەکنیک\\N{tech_name}"})
                t = end

            has_bottom = bool(translator_name or tech_name)
            for c in cues:
                if has_bottom and secs(c["start"]) < t: c["alignment_tag"] = "{\\an8}"
                else: c.setdefault("alignment_tag", "{\\an2}")

            full_cues = intro + cues
            ass_txt = build_ass_file(full_cues, font_size, wm_text, wm_color, wm_font_size, wm_align, video_path=in_p)

            with open(ass_p, "w", encoding="utf-8") as f: f.write(ass_txt)

            with st.spinner("🔥 لکاندن (FFmpeg)..."):
                try: burn_subtitles(in_p, ass_p, out_p)
                except subprocess.CalledProcessError as e:
                    st.error(f"❌ هەڵەی FFmpeg:\n`{e.stderr.decode() if e.stderr else e}`")
                    st.stop()

            st.success("🎉 بە سەرکەوتوویی تەواو بوو!")
            with open(out_p, "rb") as f: vb = f.read()
            auto_dl(vb, "subtitled.mp4", "video/mp4")
            
            d1 = st.columns(1)[0]
            d1.download_button("⬇️ دابەزاندنی ڤیدیۆ", vb, "subtitled.mp4", "video/mp4", use_container_width=True)
            st.video(vb)

if __name__ == "__main__":
    main()
