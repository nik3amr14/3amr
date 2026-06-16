import os
import re
import json
import time
import shutil
import tempfile
import subprocess
import base64

import streamlit as st
import streamlit.components.v1 as components
from faster_whisper import WhisperModel
from google import genai
from google.genai import types

# ══════════════════════════════════════════════════════════
#  ١. ڕێکخستنە سەرەکییەکان و فۆنتەکان
# ══════════════════════════════════════════════════════════
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)

KU_FONT_FILE = "Bahij Janna-Bold.ttf"
KU_FONT_PATH = os.path.join("/tmp", KU_FONT_FILE)
KU_FONT_NAME = "Bahij Janna"

MAX_SUB_DURATION = 4.0
MAX_TIMESTAMP_JUMP = 10.0
THROTTLE_SECONDS = 50
TRANSLATION_PASS_MINUTES = 5

FORMAT_MAP = {
    "MP4  — H.264  (ئەڵتەرین)":     (["-c:v","libx264","-crf","22","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/mp4",        ".mp4"),
    "MOV  — H.264  (Apple/iPhone)":  (["-c:v","libx264","-crf","22","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/quicktime",  ".mov"),
    "MKV  — H.264  (کوالیتی بەرز)": (["-c:v","libx264","-crf","18","-preset","fast",   "-c:a","aac","-b:a","192k","-threads","2"],    "video/x-matroska", ".mkv"),
    "WebM — VP9    (وێب)":           (["-c:v","libvpx-vp9","-crf","30","-b:v","0","-c:a","libopus","-b:a","128k","-threads","2"],      "video/webm",       ".webm"),
    "MP3  — تەنها دەنگ":             (["-vn","-c:a","libmp3lame","-b:a","320k"],                                                       "audio/mpeg",       ".mp3"),
}

def find_kurdish_font():
    ku_font_src = os.path.join(ROOT_DIR, "Bahij Janna-Bold.ttf")
    if os.path.exists(ku_font_src):
        shutil.copy(ku_font_src, "/tmp/Bahij Janna-Bold.ttf")
        return "Bahij Janna"
        
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/tmp/Bahij Janna-Bold.ttf",
        "Bahij Janna-Bold.ttf",
        "NotoSansArabic-Regular.ttf",
        "NotoNaskhArabic-Regular.ttf",
    ]

    for path in candidates:
        if os.path.exists(path):
            return "Bahij Janna" if "Bahij" in path else "Noto Sans Arabic"
    return "Arial"

def escape_ass(text: str) -> str:
    text = text.replace("\\", r"\\")
    text = text.replace("{", r"\{")
    text = text.replace("}", r"\}")
    return text.strip()

def sec_to_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def sec_to_srt(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs_val = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs_val:02d},{millis:03d}"

def secs(ts: str) -> float:
    try:
        ts = ts.strip().replace(",", ".")
        h, m, sf = ts.split(":")
        s, frac = (sf.split(".", 1) + ["0"])[:2]
        return int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
    except Exception:
        return 999.0

def clean_punctuation(t: str) -> str:
    bad_chars = "؟.:!ـ؛”’?,;\"'!-_()[]{}،,+=*#$@^&|~`"
    for char in bad_chars:
        t = t.replace(char, "")
    return " ".join(t.split())

def soften_explicit_terms(text: str) -> str:
    replacements = {"sex": "پەیوەندیی تایبەت", "sexual": "تایبەت", "fucking": "زۆر", "fuck": "نەفرەت", "motherfucker": "بێڕێز"}
    for src, dst in replacements.items():
        text = re.sub(src, dst, text, flags=re.IGNORECASE)
    return text

# ══════════════════════════════════════════════════════════
#  ٢. ئامرازەکانی کات و دەق
# ══════════════════════════════════════════════════════════
_CUE_RE = re.compile(r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*[|\t]\s*(.+)")

def parse_raw_text(raw: str):
    out = []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m: 
            out.append({
                "start": m.group(1).replace(",", "."), 
                "end": m.group(2).replace(",", "."), 
                "text": m.group(3).strip()
            })
    return out

def get_video_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, check=True)
        return float(r.stdout.strip())
    except Exception:
        return 540.0

def float_to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{ms:02d}"

def shift_transcript(raw_text: str, delay_seconds: float) -> str:
    if delay_seconds == 0.0:
        return raw_text
    lines = []
    for line in raw_text.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            start_sec = secs(m.group(1)) + delay_seconds
            end_sec = secs(m.group(2)) + delay_seconds
            if start_sec < 0: start_sec = 0.0
            if end_sec < 0: end_sec = 0.0
            new_start = float_to_ass_time(start_sec)
            new_end = float_to_ass_time(end_sec)
            lines.append(f"{new_start} --> {new_end} | {m.group(3)}")
        else:
            lines.append(line)
    return "\n".join(lines)

def _dedup(raw: str) -> str:
    seen, out = set(), []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            if m.group(1) in seen:
                continue
            seen.add(m.group(1))
        out.append(line)
    return "\n".join(out)

# ══════════════════════════════════════════════════════════
#  GEMINI JSON PARSER
# ══════════════════════════════════════════════════════════
def extract_json(text: str):
    text = text.strip()
    if text.startswith("```json"):
        text = text.replace("```json", "", 1)
    if text.startswith("```"):
        text = text.replace("```", "", 1)
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    if not match:
        raise ValueError("Gemini JSON parse failed")
    return json.loads(match.group(1))

# ══════════════════════════════════════════════════════════
#  TIMESTAMP PROTECTION
# ══════════════════════════════════════════════════════════
def validate_cues(cues):
    validated = []
    current_start = 0.0
    current_end = 0.0
    for cue in cues:
        try:
            new_start = float(cue["start"])
            new_end = float(cue["end"])
            new_text = str(cue["text"]).strip()
        except Exception:
            continue
        if not new_text:
            continue
        if new_end <= new_start:
            continue
        if new_start < current_start:
            new_start = current_end
        if new_end <= new_start:
            continue
        validated.append({
            "start": round(new_start, 3),
            "end": round(new_end, 3),
            "text": new_text,
        })
        current_start = new_start
        current_end = new_end
    return validated

# ══════════════════════════════════════════════════════════
#  GEMINI TRANSLATION
# ══════════════════════════════════════════════════════════
def gemini_translate(client, transcript_chunk, pass_number=1):
    system_prompt = """
تۆ باشترین و بەتواناترین وەرگێڕ و دەرهێنەری دۆبلاژی سینەماییت لە کوردستان. ئەرکەکەت: دانانی ژێرنووسی سینەماییانەی زۆر شاز و ناوازە بۆ ئەم ڤیدیۆیە بە کوردی سۆرانی.

یاساکانی مێشکی تۆ (زۆر گرنگ و توند):
١. وەرگێڕانی ١٠٠٪ مرۆڤانە و شاز: بە هیچ شێوەیەک وەرگێڕانی وشە بە وشە (حەرفی) یان وەرگێڕانی ئامێری مەکە! مانای قسەکان، هەست و سۆزی کارەکتەرەکان بگرە و بیانکە بە کوردییەکی زۆر پاراو، جوان، و مانا دار. دەبێت بینەر بە هیچ شێوەیەک هەست نەکات کە زیرەکی دەستکرد ئەمەی وەرگێڕاوە.
٢. بەکارهێنانی ئیدیۆم و پەند: ئەگەر لە زمانە بیانییەکەدا ئیدیۆمێک یان قسەیەکی خوازراو هەبوو، ڕێک بەرامبەرە جوانەکەی لە زمانی کوردیدا بەکاربهێنە.
٣. جیاکردنەوەی کارەکتەرەکان: ئەگەر هەستت کرد دوو کەس قسە دەکەن، بە جوانی دیالۆگەکانیان جیا بکەرەوە.
٤. پاراستنی ڕێزمانی و جێناوەکان: ئەگەر کارەکتەرەکە گوتی "تۆ"، دەبێت بە "تۆ" وەربگێڕدرێت، هەرگیز مەکە بە "من".
٥. زۆر گرنگ: تۆ لیستێک لە ژێرنووست پێدەدرێت. دەبێت **هەموو دانە بە دانەی لیستەکە** وەربگێڕیت. بە هیچ شێوەیەک نابێت یەک دێڕیش بپەڕێنیت یان کورت بکەیتەوە.
٦. کاتەکان (start و end) بە تەواوی وەک خۆیان بهێڵەوە و دەستکارییان مەکە.
٧. لابردنی تەواوی خاڵبەندی و هێماکان: بە هیچ شێوەیەک هێماکانی خاڵبەندی وەک (؟ . : ! ، ، " ' - _ ? !) بەکارمەهێنە.

Output format (ALWAYS return a JSON array of the EXACT SAME LENGTH as input):
[
  {
    "start": 0.00,
    "end": 1.50,
    "text": "First translated line..."
  },
  {
    "start": 1.50,
    "end": 3.00,
    "text": "Second translated line..."
  }
]
"""
    user_prompt = f"Translate ALL of these cues without skipping any:\n{json.dumps(transcript_chunk, ensure_ascii=False)}"
    
    status_msg = st.empty() # بۆکسێکی بەتاڵ بۆ ئەوەی نامەکان لەسەر یەک کەڵەکە نەبن
    
    for attempt in range(30):
        try:
            resp = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=[user_prompt],
                config=types.GenerateContentConfig(system_instruction=system_prompt, temperature=0.2, response_mime_type="application/json")
            )
            data = extract_json(resp.text)
            status_msg.empty() # سڕینەوەی نامەکە ئەگەر سەرکەوتوو بوو
            if data: return data
        except Exception as e:
            error_msg = str(e)
            if "503" in error_msg or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                # تەنها یەک نامە پیشان دەدات و خۆی نوێ دەکاتەوە بێ ئەوەی شاشە پڕ بکات
                status_msg.warning(f"⚠️ هێرشکردنە سەر سێرڤەری گووگڵ بۆ وەرگرتنی وەڵام... (هەوڵی {attempt+1}/30)")
                time.sleep(5)
            else:
                time.sleep(2)
                
    status_msg.empty()
    return []

# ══════════════════════════════════════════════════════════
#  FASTER WHISPER (Word-Level Silence Detection)
# ══════════════════════════════════════════════════════════
@st.cache_resource
def load_whisper():
    return WhisperModel("small", device="cpu", compute_type="int8")

def extract_audio(video_path, audio_path):
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", audio_path], capture_output=True, check=True)

def transcribe_audio(audio_path):
    model = load_whisper()
    segments, info = model.transcribe(
        audio_path,
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=300)
    )
    
    cues = []
    current_text = []
    start_time = None
    last_end = None
    
    for seg in segments:
        if not seg.words:
            continue
        for w in seg.words:
            word_start = float(w.start)
            word_end = float(w.end)
            word_text = str(w.word).strip()
            
            if not word_text:
                continue
                
            if start_time is None:
                start_time = word_start
                
            if last_end is not None and (word_start - last_end > 0.3):
                cues.append({
                    "start": round(start_time, 2),
                    "end": round(last_end, 2),
                    "text": " ".join(current_text)
                })
                current_text = [word_text]
                start_time = word_start
            else:
                current_text.append(word_text)
                
            last_end = word_end
            
            if (last_end - start_time > MAX_SUB_DURATION) or word_text[-1] in ".!?؟":
                cues.append({
                    "start": round(start_time, 2),
                    "end": round(last_end, 2),
                    "text": " ".join(current_text)
                })
                current_text = []
                start_time = None
                last_end = None
                
    if current_text and start_time is not None and last_end is not None:
        cues.append({
            "start": round(start_time, 2),
            "end": round(last_end, 2),
            "text": " ".join(current_text)
        })
        
    return cues

# ══════════════════════════════════════════════════════════
#  BUILD CHUNKS FOR GEMINI
# ══════════════════════════════════════════════════════════
def build_translation_chunks(cues, chunk_minutes=5):
    max_seconds = chunk_minutes * 60
    chunks, current = [], []
    chunk_start = None
    for item in cues:
        if chunk_start is None: chunk_start = item["start"]
        if item["end"] - chunk_start > max_seconds:
            chunks.append(current)
            current = [item]
            chunk_start = item["start"]
        else:
            current.append(item)
    if current: chunks.append(current)
    return chunks

# ══════════════════════════════════════════════════════════
#  50 SECOND THROTTLE
# ══════════════════════════════════════════════════════════
def throttle_countdown():
    holder = st.empty()
    for i in range(THROTTLE_SECONDS, 0, -1):
        holder.info(f"⏳ پاراستنی سێرڤەر: {i} چرکە پشوو دەدەین پێش دەستپێکردنی پارچەی داهاتوو...")
        time.sleep(1)
    holder.empty()

# ══════════════════════════════════════════════════════════
#  ⚙️ بەڕێوەبەری سەرەکی پڕۆژەکە (ORCHESTRATOR)
# ══════════════════════════════════════════════════════════
def process_full_video(api_key, video_path):
    audio_path = video_path.replace(".mp4", ".wav")
    
    with st.spinner("🎵 خەریکی دەرهێنانی دەنگی ڤیدیۆکەیە..."):
        extract_audio(video_path, audio_path)
        
    with st.spinner("📝 خەریکی نووسینەوەی دەنگەکەیە بە وردی (Faster-Whisper)..."):
        cues = transcribe_audio(audio_path)
        if not cues:
            st.error("❌ هیچ دیالۆگێک لە ڤیدیۆکەدا نەدۆزرایەوە.")
            return ""
            
    with st.spinner("🧠 خەریکی وەرگێڕانە بۆ کوردی سۆرانی سینەمایی..."):
        chunks = build_translation_chunks(cues, chunk_minutes=TRANSLATION_PASS_MINUTES)
        all_cues = []
        total = len(chunks)
        progress = st.progress(0)
        
        local_client = genai.Client(api_key=api_key)
        
        for index, chunk in enumerate(chunks):
            translated = gemini_translate(local_client, chunk, pass_number=index + 1)
            all_cues.extend(translated)
            progress.progress((index + 1) / total)
            if index < total - 1:
                throttle_countdown()
                
        all_cues.sort(key=lambda x: x["start"])
        validated = validate_cues(all_cues)
        
    raw_lines = []
    for c in validated:
        s = float_to_ass_time(c["start"])
        e = float_to_ass_time(c["end"])
        raw_lines.append(f"{s} --> {e} | {c['text']}")
    return "\n".join(raw_lines)

# ══════════════════════════════════════════════════════════
#  ASS & SRT BUILDERS
# ══════════════════════════════════════════════════════════
def hex_to_ass(h: str) -> str:
    # گۆڕینی ڕەنگی هێکس بۆ فۆرماتی ASS (کە پێچەوانەیە: BGR)
    h = h.lstrip("#").upper().ljust(6, "0")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"

def build_ass_file(cues, font_size, wm_text, wm_color, wm_font_size, wm_alignment):
    font_name = find_kurdish_font()
    wm_ass = hex_to_ass(wm_color)
    
    ass = [
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\nScaledBorderAndShadow: yes\n",
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        f"Style: CornerStyle,{font_name},30,&H00E0E0E0,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,9,20,20,20,1",
        f"Style: WatermarkStyle,{font_name},{wm_font_size},{wm_ass},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,7,15,20,20,1\n",
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    
    if wm_text:
        ass.append(f"Dialogue: 0,0:00:00.00,9:59:59.99,WatermarkStyle,,0,0,0,,{{\\an{wm_alignment}}}{wm_text}")
        
    for c in cues:
        txt = clean_punctuation(c['text'])
        ass.append(f"Dialogue: 0,{c['start']},{c['end']},{c.get('style','Default')},,0,0,0,,{c.get('alignment_tag','{\\an2}')}{txt}")
    return "\n".join(ass)

def build_srt_file(cues):
    lines = []
    for idx, c in enumerate(cues, start=1):
        s = sec_to_srt(secs(c["start"]))
        e = sec_to_srt(secs(c["end"]))
        # سڕینەوەی تاگەکانی ڕەنگ لە SRT چونکە SRT پشتگیری ڕەنگ ناکات وەک ASS
        clean_txt = re.sub(r'\{\\[^}]*\}', '', c['text'])
        lines.append(f"{idx}\n{s} --> {e}\n{clean_punctuation(clean_txt)}\n")
    return "\n".join(lines)

def burn_subtitles(video_path, ass_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vf", f"ass={ass_path}:fontsdir=/tmp", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-c:a", "copy", output_path], capture_output=True, check=True)

def convert_video(video_path, output_path, codec_args):
    subprocess.run(["ffmpeg", "-y", "-i", video_path] + codec_args + [output_path], capture_output=True, check=True)

def auto_dl(data: bytes, name: str, mime: str):
    b64 = base64.b64encode(data).decode()
    components.html(f'<a id="xdl" href="data:{mime};base64,{b64}" download="{name}"></a><script>setTimeout(function(){{document.getElementById("xdl").click();}},800);</script>', height=0)

# ══════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════
def main():
    st.set_page_config(page_title="Sorani Subtitle Studio", layout="wide")
    st.title("🎬 Kurdish Sorani Subtitle Generator")

    tab_sub, tab_conv = st.tabs(["🎬 ژێرنووس", "🔄 گۆڕینی فۆرمات"])

    with tab_sub:
        api_key = st.text_input("🔑 Gemini API Key", type="password")
        video_file = st.file_uploader("📁 ڤیدیۆ بار بکە (MP4/MOV)", type=["mp4", "mov"])

        st.markdown("---")
        font_size = st.slider("📐 قەبارەی فۆنتی ژێرنووس", 20, 80, 52)

        st.markdown("---")
        st.subheader("ℹ️ زانیاری ناساندنی دەستپێک")
        c1, c2 = st.columns(2)
        with c1:
            anime_name = st.text_input("🎬 ناوی فیلم / زنجیرە (بۆ گۆشەی سەرەوە)")
            translator_name = st.text_input("✍️ ناوی وەرگێڕ")
            translator_color = st.color_picker("🎨 ڕەنگی ناوی وەرگێڕ", "#00FF00") # ڕەنگی سەوز بە بنەڕەتی
        with c2:
            season_ep = st.text_input("📺 سیزن / ئەڵقە")
            tech_name = st.text_input("💻 ناوی تەکنیک")
            tech_color = st.color_picker("🎨 ڕەنگی ناوی تەکنیک", "#00FFFF") # ڕەنگی شین باو بە بنەڕەتی
            
        intro_duration = st.number_input("⏱️ کاتی مانەوەی ناوەکانی دەستپێک (بە چرکە)", min_value=1.0, max_value=15.0, value=3.0, step=0.5)

        st.markdown("---")
        st.subheader("🎨 واتەرمارکی نووسین (لۆگۆ)")
        wc1, wc2, wc3, wc4 = st.columns(4)
        with wc1: wm_text = st.text_input("📝 نووسینی واتەرمارک")
        with wc2: wm_color = st.color_picker("🎨 ڕەنگی لۆگۆ", "#FFFFFF")
        with wc3: wm_font_size = st.slider("📏 قەبارە", 10, 150, 30)
        with wc4: 
            wm_pos = st.selectbox("📍 شوێن", ["چەپ", "ڕاست"])
            wm_alignment = 7 if wm_pos == "چەپ" else 9

        if "sub_raw" not in st.session_state:
            st.session_state.sub_raw = None
            st.session_state.sub_input_path = None
            st.session_state.sub_temp_dir = None

        st.markdown("---")
        if st.button("🧠 ١. دەرهێنان و وەرگێڕان (دەستپێکردن)", type="primary", use_container_width=True):
            if not api_key: st.error("❌ کلیلی Gemini بنووسە."); return
            if not video_file: st.error("❌ ڤیدیۆ بار بکە."); return
            
            temp_dir = tempfile.mkdtemp()
            in_p = os.path.join(temp_dir, "input.mp4")
            with open(in_p, "wb") as f: f.write(video_file.read())
            
            st.session_state.sub_temp_dir = temp_dir
            st.session_state.sub_input_path = in_p
            
            raw_text = process_full_video(api_key.strip(), in_p)
            if raw_text:
                st.session_state.sub_raw = raw_text
                st.rerun()

        if st.session_state.sub_raw:
            st.success("✅ وەرگێڕان تەواو بوو! دەتوانیت پێداچوونەوەی بۆ بکەیت.")
            
            edited_raw = st.text_area("📝 ستەرەکان — پێش لکاندن دەسکاریان بکە", value=st.session_state.sub_raw, height=400)

            if st.button("🔥 ٢. ژێرنووس بخەرە سەر ڤیدیۆ", type="primary", use_container_width=True):
                cues = parse_raw_text(edited_raw)
                if not cues: st.error("❌ ستەرەکان ناناسرێنەوە."); return

                tmp = st.session_state.sub_temp_dir
                in_p = st.session_state.sub_input_path
                ass_p = os.path.join(tmp, "subs.ass")
                srt_p = os.path.join(tmp, "subs.srt")
                out_p = os.path.join(tmp, "output.mp4")

                intro = []
                if anime_name: 
                    text_val = anime_name
                    if season_ep: text_val += f"\\N({season_ep})"
                    intro.append({"start": "0:00:00.00", "end": "0:00:15.00", "style": "CornerStyle", "text": text_val})
                    
                current_intro_time = 0.0
                if translator_name: 
                    end_time = current_intro_time + intro_duration
                    c_tag = f"{{\\c{hex_to_ass(translator_color)}}}"
                    intro.append({
                        "start": float_to_ass_time(current_intro_time), 
                        "end": float_to_ass_time(end_time), 
                        "alignment_tag": "{\\an2}", 
                        "text": f"{c_tag}وەرگێڕان\\N{translator_name}"
                    })
                    current_intro_time = end_time
                    
                if tech_name: 
                    end_time = current_intro_time + intro_duration
                    c_tag = f"{{\\c{hex_to_ass(tech_color)}}}"
                    intro.append({
                        "start": float_to_ass_time(current_intro_time), 
                        "end": float_to_ass_time(end_time), 
                        "alignment_tag": "{\\an2}", 
                        "text": f"{c_tag}تەکنیک\\N{tech_name}"
                    })
                    current_intro_time = end_time

                has_bottom_intro = bool(translator_name or tech_name)

                for c in cues:
                    if has_bottom_intro and secs(c["start"]) < current_intro_time: 
                        c["alignment_tag"] = "{\\an8}"
                    else:
                        if "alignment_tag" not in c:
                            c["alignment_tag"] = "{\\an2}"

                full_cues = intro + cues
                
                ass_txt = build_ass_file(full_cues, font_size, wm_text, wm_color, wm_font_size, wm_alignment)
                srt_txt = build_srt_file(cues)
                
                with open(ass_p, "w", encoding="utf-8") as f: f.write(ass_txt)
                with open(srt_p, "w", encoding="utf-8") as f: f.write(srt_txt)

                with st.spinner("🔥 خەریکی لکاندنی ژێرنووسە بە ڤیدیۆکەوە (FFmpeg)..."):
                    try:
                        burn_subtitles(in_p, ass_p, out_p)
                    except Exception as e:
                        st.error(f"❌ هەڵە لە FFmpeg:\n`{e}`")
                        return

                st.success("🎉 بە سەرکەوتوویی تەواو بوو!")
                
                with open(out_p, "rb") as f: vb = f.read()
                auto_dl(vb, "subtitled.mp4", "video/mp4")
                
                c1, c2, c3 = st.columns(3)
                c1.download_button("⬇️ دابەزاندنی ڤیدیۆ", vb, "subtitled.mp4", "video/mp4", use_container_width=True)
                c2.download_button("⬇️ دابەزاندنی SRT", srt_txt, "subtitle.srt", "text/plain", use_container_width=True)
                c3.download_button("⬇️ دابەزاندنی ASS", ass_txt, "subtitle.ass", "text/plain", use_container_width=True)
                
                st.video(vb)

    with tab_conv:
        st.markdown("### 🔄 گۆڕینی فۆرمات")
        cf = st.file_uploader("📁 ڤیدیۆ بار بکە", type=["mp4","mov","mkv","avi","webm","m4v","flv"], key="cv")
        fmt = st.selectbox("🎯 فۆرمات هەڵبژێرە", list(FORMAT_MAP.keys()))
        if st.button("⚡ گۆڕین", type="primary"):
            if not cf: st.error("ڤیدیۆ بار بکە."); return
            codec_args, mime, ext = FORMAT_MAP[fmt]
            with tempfile.TemporaryDirectory() as tmp:
                in_p = os.path.join(tmp, f"input{os.path.splitext(cf.name)[-1] or '.mp4'}")
                out_p = os.path.join(tmp, f"output{ext}")
                with open(in_p, "wb") as f: f.write(cf.read())
                with st.spinner("⚙️ خەریکی گۆڕینە..."):
                    try:
                        convert_video(in_p, out_p, codec_args)
                        with open(out_p, "rb") as f: ob = f.read()
                        st.success("✅ تەواو!")
                        st.download_button(f"⬇️ دابەزێنە {ext}", ob, f"converted{ext}", mime, use_container_width=True)
                    except Exception as e:
                        st.error(f"❌ هەڵە لە FFmpeg:\n`{e}`")

if __name__ == "__main__":
    main()
