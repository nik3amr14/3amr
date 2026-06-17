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

FORMAT_MAP = {
    "MP4  — H.264  (ئەڵتەرین)":     (["-c:v","libx264","-crf","22","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/mp4",        ".mp4"),
    "MOV  — H.264  (Apple/iPhone)":  (["-c:v","libx264","-crf","22","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/quicktime",  ".mov"),
    "MKV  — H.264  (کوالیتی بەرز)": (["-c:v","libx264","-crf","18","-preset","fast",   "-c:a","aac","-b:a","192k","-threads","2"],    "video/x-matroska", ".mkv"),
    "WebM — VP9    (وێب)":           (["-c:v","libvpx-vp9","-crf","30","-b:v","0","-c:a","libopus","-b:a","128k","-threads","2"],      "video/webm",       ".webm"),
    "MP3  — تەنها دەنگ":             (["-vn","-c:a","libmp3lame","-b:a","320k"],                                                       "audio/mpeg",       ".mp3"),
}

def ensure_streamlit_config():
    """سنووری بارکردن بۆ 700MB زیاد دەکات بۆ ڤیدیۆی گەورە"""
    try:
        cfg_dir = os.path.join(APP_DIR, ".streamlit")
        cfg_path = os.path.join(cfg_dir, "config.toml")
        os.makedirs(cfg_dir, exist_ok=True)
        if not os.path.exists(cfg_path):
            with open(cfg_path, "w", encoding="utf-8") as f:
                f.write("[server]\nmaxUploadSize = 700\n")
    except Exception:
        pass

def find_kurdish_font():
    possible_paths = [
        os.path.join(APP_DIR, KU_FONT_FILE),
        os.path.join(ROOT_DIR, KU_FONT_FILE),
        KU_FONT_FILE,
        os.path.join(os.path.dirname(APP_DIR), KU_FONT_FILE)
    ]
    ku_font_src = None
    for path in possible_paths:
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            ku_font_src = path
            break
    if ku_font_src:
        try:
            os.makedirs("/tmp", exist_ok=True)
            shutil.copy(ku_font_src, KU_FONT_PATH)
            return KU_FONT_NAME
        except Exception:
            pass
    return "Arial"

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
    """لابردنی تەواوی خاڵبەندییەکان بە توندی بەپێی داواکاری بەکارهێنەر"""
    bad_chars = "؟.:!ـ؛”’?,;\"'!-_()[]{}،,+=*#$@^&|~`"
    for char in bad_chars:
        t = t.replace(char, "")
    return " ".join(t.split())

def split_song_tag(text: str):
    """جیاکردنەوەی هێمای گۆرانی بۆ گۆڕینی ڕەنگەکەی بۆ زەرد"""
    text = text.strip()
    if text.startswith("🎵"):
        return True, text.replace("🎵", "", 1).strip()
    return False, text

_CUE_RE = re.compile(r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})\s*[|\t]\s*(.+)")

def parse_raw_text(raw: str):
    out = []
    if not raw: return out
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m: 
            out.append({
                "start": m.group(1).replace(",", "."), 
                "end": m.group(2).replace(",", "."), 
                "text": m.group(3).strip()
            })
    return out

def parse_existing_raw_to_last_time(raw: str) -> float:
    cues = parse_raw_text(raw)
    if not cues: return 0.0
    last_end = 0.0
    for c in cues:
        e = secs(c["end"])
        if e > last_end:
            last_end = e
    return last_end

def float_to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{ms:02d}"

def shift_transcript(raw_text: str, delay_seconds: float) -> str:
    if delay_seconds == 0.0: return raw_text
    lines = []
    for line in raw_text.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            start_sec = max(0.0, secs(m.group(1)) + delay_seconds)
            end_sec = max(0.0, secs(m.group(2)) + delay_seconds)
            lines.append(f"{float_to_ass_time(start_sec)} --> {float_to_ass_time(end_sec)} | {m.group(3)}")
        else:
            lines.append(line)
    return "\n".join(lines)

def extract_json(text: str):
    text = text.strip()
    if text.startswith("```json"): text = text.replace("```json", "", 1)
    if text.startswith("```"): text = text.replace("```", "", 1)
    if text.endswith("```"): text = text[:-3]
    try: return json.loads(text.strip())
    except Exception: pass
    match = re.search(r"(\[.*\])", text, re.DOTALL)
    if not match: raise ValueError("JSON parse failed")
    return json.loads(match.group(1))

def validate_cues(cues):
    validated = []
    current_start = 0.0
    current_end = 0.0
    for cue in cues:
        try:
            new_start = float(cue["start"])
            new_end = float(cue["end"])
            new_text = str(cue["text"]).strip()
        except Exception: continue
        if not new_text or new_end <= new_start: continue
        if new_start < current_start: new_start = current_end
        if new_end <= new_start: continue
        validated.append({"start": round(new_start, 3), "end": round(new_end, 3), "text": new_text})
        current_start = new_start
        current_end = new_end
    return validated

# ══════════════════════════════════════════════════════════
#  GEMINI TRANSLATION (Dynamic Thinking & Poetic Meaning)
# ══════════════════════════════════════════════════════════
def gemini_translate(api_keys, current_key_index, transcript_chunk, songs_mode=False):
    # یاساکان زۆر توندتر کراون بۆ ئەوەی گەرمی 0.70 کێشە لە شێوازی فۆرمات دروست نەکات
    system_prompt = """
تۆ باشترین، شاعیرانەترین و لێهاتووترین وەرگێڕی دیالۆگی فیلم و گۆرانی سینەماییت لە کوردستان. ئەرکەکەت وەرگێڕانی ئەم ژێرنووسەیە بۆ کوردی سۆرانییەکی زۆر پاراو و هەستیار.

یاساکانی مێشکت (زۆر توند و بێ هیچ جێگرەوەیەک):
١. مانا و سۆز (Poetic & Natural Context): بە هیچ شێوەیەک وەرگێڕانی وشە بە وشە مەکە! مانای تەواو و هەستی ڕاستەقینەی دیالۆگەکان بگرە و بیانکە بە کوردییەکی زۆر جوان، نەرم، شاعیرانە و پڕ لە هەست.
٢. دەستکاریکردنی کاتەکان بە توندی قەدەغەیە: کلیلەکانی "start" و "end" دەبێت بە دروستی و بەبێ یەک چرکە دەستکاری وەک خۆیان لەناو کۆدی JSON بنووسرێنەوە.
٣. نەپەڕاندنی دێڕەکان: دەبێت سەرجەم دێڕەکانی ناو لیستەکە دێڕ بە دێڕ وەربگێڕیت. ژمارەی ڕستەکان لە وەڵامی کۆتاییدا دەبێت بە تەواوی هاوتای ڕستەکانی ناوچەک بێت (بێ کورتکردنەوە یان لادان).
٤. قەدەغەکردنی تەواوی خاڵبەندییەکان: لە دەقی وەرگێڕدراوی کوردی بە هیچ شێوەیەک هێمای خاڵبەندی وەک (؟ . : ! ، ، " ' - _ ? !) بەکارمەهێنه.
٥. فۆرماتی کۆد: تەنها و تەنها پێکهاتەی ڕاست و دروستی JSON دەربکە، هیچ نووسین و ڕوونکردنەوەیەکی تر لە دەرەوەی کەوانەکان مەنووسە.
"""
    if songs_mode:
        system_prompt += "\n٦. ئەگەر دێڕەکە گۆرانی بوو، هێمای 🎵 بخەرە سەرەتای دێڕەکە."

    system_prompt += """
Output format (ALWAYS return a JSON array of the EXACT SAME LENGTH as input):
[
  {
    "start": 0.00,
    "end": 1.50,
    "text": "وەرگێڕانەکە لێرە دەبێت"
  }
]
"""
    user_prompt = f"Translate ALL cues exactly:\n{json.dumps(transcript_chunk, ensure_ascii=False)}"
    status_msg = st.empty()
    
    # لۆژیکی مژینی کلیلەکان (Cascade Key Exhaustion)
    while True:
        try:
            current_api_key = api_keys[current_key_index]
            client = genai.Client(api_key=current_api_key)
            
            resp = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=[user_prompt],
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt, 
                    temperature=0.70,  # گەرمی بۆ وەرگێڕانێکی سروشتی و هەستیار
                    max_output_tokens=65536,
                    response_mime_type="application/json",
                    # بەکارهێنانی سیستەمی بیرکردنەوەی قووڵی داینامیکی گووگڵ بۆ بەرزکردنەوەی کوالیتی
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=-1  # بەکارهێنانی داینامیکی لیمیتی بیرکردنەوە
                    )
                )
            )
            data = extract_json(resp.text)
            status_msg.empty()
            if data: return data, current_key_index
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg or "Quota" in error_msg:
                # کلیلەکە هێزی نەما، دەچێتە سەر کلیلی داهاتوو
                next_index = (current_key_index + 1) % len(api_keys)
                if next_index == current_key_index:
                    status_msg.error("❌ هەموو کلیلەکان لیمیتیان تەواو بووە! تکایە کەمێک پشوو بدە یان کلیلی نوێ دابنێ.")
                    time.sleep(10)
                else:
                    current_key_index = next_index
                    status_msg.warning(f"⚠️ کلیلەکە ماندوو بوو! ڕاستەوخۆ گۆڕدرا بۆ کلیلی ژمارە {current_key_index + 1}...")
                    time.sleep(2) # پشوویەکی بچووک بۆ گۆڕینی کلیل
            else:
                status_msg.info("خەریکی وەرگرتنی وەڵامە...")
                time.sleep(2)

# ══════════════════════════════════════════════════════════
#  FASTER WHISPER (Fallback Fix)
# ══════════════════════════════════════════════════════════
@st.cache_resource
def load_whisper():
    return WhisperModel("small", device="cpu", compute_type="int8")

def extract_audio(video_path, audio_path):
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000", audio_path], capture_output=True, check=True)

def transcribe_audio(audio_path, vad_filter=True):
    model = load_whisper()
    segments, info = model.transcribe(
        audio_path, beam_size=5, word_timestamps=True,
        vad_filter=vad_filter, vad_parameters=dict(min_silence_duration_ms=300) if vad_filter else None
    )
    
    cues = []
    current_text, start_time, last_end = [], None, None
    
    for seg in segments:
        # چارەسەری کێشەی وەستان لە کاتی شەڕ و مۆسیقا (Fallback)
        if not seg.words:
            if seg.text and seg.text.strip():
                cues.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": seg.text.strip()})
            continue

        for w in seg.words:
            word_start, word_end, word_text = float(w.start), float(w.end), str(w.word).strip()
            if not word_text: continue
            if start_time is None: start_time = word_start
                
            if last_end is not None and (word_start - last_end > 0.3):
                cues.append({"start": round(start_time, 2), "end": round(last_end, 2), "text": " ".join(current_text)})
                current_text, start_time = [word_text], word_start
            else:
                current_text.append(word_text)
                
            last_end = word_end
            if (last_end - start_time > MAX_SUB_DURATION) or word_text[-1] in ".!?؟":
                cues.append({"start": round(start_time, 2), "end": round(last_end, 2), "text": " ".join(current_text)})
                current_text, start_time, last_end = [], None, None
                
    if current_text and start_time is not None and last_end is not None:
        cues.append({"start": round(start_time, 2), "end": round(last_end, 2), "text": " ".join(current_text)})
    return cues

def build_translation_chunks(cues, chunk_minutes):
    max_seconds = chunk_minutes * 60
    chunks, current, chunk_start = [], [], None
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
#  ORCHESTRATOR
# ══════════════════════════════════════════════════════════
def process_full_video(api_keys, video_path, vad_filter=True, songs_mode=False, existing_raw="", chunk_minutes=5):
    audio_path = video_path.replace(".mp4", ".wav")
    last_translated_sec = parse_existing_raw_to_last_time(existing_raw)
    
    with st.spinner("🎵 خەریکی دەرهێنانی دەنگی ڤیدیۆکەیە..."):
        extract_audio(video_path, audio_path)
        
    with st.spinner("📝 خەریکی نووسینەوەی دەنگەکەیە بە وردی (Faster-Whisper)..."):
        cues = transcribe_audio(audio_path, vad_filter=vad_filter)
        if not cues:
            st.error("❌ هیچ دیالۆگێک لە ڤیدیۆکەدا نەدۆزرایەوە.")
            return existing_raw
            
    with st.spinner("🧠 خەریکی وەرگێڕانە بە خێرایی موشەک..."):
        chunks = build_translation_chunks(cues, chunk_minutes=chunk_minutes)
        all_cues = []
        if existing_raw: all_cues.extend(parse_raw_text(existing_raw))
            
        total = len(chunks)
        progress = st.progress(0)
        current_key_index = 0
        
        for index, chunk in enumerate(chunks):
            chunk_last_end = chunk[-1]["end"] if chunk else 0.0
            if chunk_last_end <= last_translated_sec:
                progress.progress((index + 1) / total)
                continue
                
            active_items = [c for c in chunk if c["start"] >= last_translated_sec]
            if not active_items:
                progress.progress((index + 1) / total)
                continue
                
            # ناردن بۆ وەرگێڕان بەبێ پشوودانی زۆرەملێ لە نێوان پارچەکاندا
            translated, current_key_index = gemini_translate(api_keys, current_key_index, active_items, songs_mode=songs_mode)
            all_cues.extend(translated)
            progress.progress((index + 1) / total)
                
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
    h = h.lstrip("#").upper().ljust(6, "0")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"

SONG_COLOR_ASS = "&H0000FFFF&"
SONG_COLOR_SRT = "#FFFF00"

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
    if wm_text: ass.append(f"Dialogue: 0,0:00:00.00,9:59:59.99,WatermarkStyle,,0,0,0,,{{\\an{wm_alignment}}}{wm_text}")
    for c in cues:
        is_song, clean_txt = split_song_tag(c['text'])
        clean_txt = clean_punctuation(clean_txt)
        color_prefix = f"{{\\c{SONG_COLOR_ASS}}}" if is_song else ""
        align_tag = c.get('alignment_tag', '{\\an2}')
        ass.append(f"Dialogue: 0,{c['start']},{c['end']},{c.get('style','Default')},,0,0,0,,{align_tag}{color_prefix}{clean_txt}")
    return "\n".join(ass)

def build_srt_file(cues):
    lines = []
    for idx, c in enumerate(cues, start=1):
        s = sec_to_srt(secs(c["start"]))
        e = sec_to_srt(secs(c["end"]))
        is_song, clean_txt = split_song_tag(c['text'])
        clean_txt = clean_punctuation(clean_txt)
        clean_txt = re.sub(r'\{\\[^}]*\}', '', clean_txt)
        if is_song: clean_txt = f'<font color="{SONG_COLOR_SRT}">{clean_txt}</font>'
        lines.append(f"{idx}\n{s} --> {e}\n{clean_txt}\n")
    return "\n".join(lines)

def burn_subtitles(video_path, ass_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vf", f"ass={ass_path}:fontsdir=/tmp", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-c:a", "copy", output_path], capture_output=True, check=True)

def convert_video(video_path, output_path, codec_args):
    subprocess.run(["ffmpeg", "-y", "-i", video_path] + codec_args + [output_path], capture_output=True, check=True)

def auto_dl(data: bytes, name: str, mime: str):
    b64 = base64.b64encode(data).decode()
    components.html(f'<a id="xdl" href="data:{mime};base64,{b64}" download="{name}"></a><script>setTimeout(function(){{document.getElementById("xdl").click();}},800);</script>', height=0)

def _cleanup_sub_session():
    temp_dir = st.session_state.get("sub_temp_dir")
    if temp_dir and os.path.isdir(temp_dir):
        try: shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception: pass
    st.session_state.sub_raw = None
    st.session_state.sub_input_path = None
    st.session_state.sub_temp_dir = None

# ══════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════
def main():
    ensure_streamlit_config()
    st.set_page_config(page_title="Sorani Subtitle Studio", layout="wide")
    st.title("🎬 Kurdish Sorani Subtitle Generator")

    tab_sub, tab_conv = st.tabs(["🎬 ژێرنووس", "🔄 گۆڕینی فۆرمات"])

    with tab_sub:
        st.info("💡 دەتوانیت چەند کلیلێک لەسەر یەک دابنێیت. بەرنامەکە تەنها یەکەم کلیل بەکاردەهێنێت تا لیمیتی تەواو دەبێت، پاشان خۆکارانە دەچێتە سەر کلیلی دووەم!")
        api_keys_input = st.text_area("🔑 کلیلەکانی Gemini لێرە دابنێ (هەر کلیلەی لە دێڕێکدا)", type="password", height=100)
        video_file = st.file_uploader("📁 ڤیدیۆ بار بکە (MP4/MOV)", type=["mp4", "mov"])

        st.markdown("---")
        c_audio, c_chunk = st.columns(2)
        with c_audio:
            st.subheader("🎧 جۆری دەنگی ڤیدیۆکە")
            audio_mode = st.radio("هەڵبژاردن:", ["تەنها قسەکردن", "قسەکردن و گۆرانی"], horizontal=True, label_visibility="collapsed")
            vad_filter = (audio_mode == "تەنها قسەکردن")
            songs_mode = (audio_mode == "قسەکردن و گۆرانی")
        with c_chunk:
            st.subheader("⚙️ بڕی وەرگێڕان بە یەکجار")
            chunk_minutes = st.slider("چەند خولەک بەیەکجار بنێرێت؟", 3, 15, 6, help="ئەگەر ڤیدیۆکە قسەی زۆرە بیخەرە سەر ٥، ئەگەر کەمە بیخەرە سەر ١٠")

        st.markdown("---")
        font_size = st.slider("📐 قەبارەی فۆنتی ژێرنووس", 20, 80, 52)

        st.markdown("---")
        st.subheader("ℹ️ زانیاری ناساندنی دەستپێک")
        c1, c2 = st.columns(2)
        with c1:
            anime_name = st.text_input("🎬 ناوی فیلم / زنجیرە (بۆ گۆشەی سەرەوە)")
            translator_name = st.text_input("✍️ ناوی وەرگێڕ")
        with c2:
            season_ep = st.text_input("📺 سیزن / ئەڵقە")
            tech_name = st.text_input("💻 ناوی تەکنیک")
            
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

        st.markdown("---")
        delay_seconds = st.slider("⏱️ کاتی ژێرنووس (+/- چرکە)", -10.0, 10.0, 0.0, 0.1, help="بۆ پێشخستن یان دواخستنی کاتی ژێرنووسەکان")

        if "sub_raw" not in st.session_state:
            st.session_state.sub_raw = None
            st.session_state.sub_input_path = None
            st.session_state.sub_temp_dir = None

        st.markdown("---")
        col_start, col_resume = st.columns(2)
        
        with col_start:
            start_clicked = st.button("🧠 ١. دەرهێنان و وەرگێڕان (لە سەرەتاوە)", type="primary", use_container_width=True)
            
        with col_resume:
            resume_clicked = st.button("▶️ بەردەوام بوون (Resume)", use_container_width=True, disabled=not st.session_state.sub_raw)

        api_keys = [k.strip() for k in api_keys_input.split('\n') if k.strip()]

        if start_clicked:
            if not api_keys: st.error("❌ لایەنی کەم یەک کلیلی Gemini بنووسە."); return
            if not video_file: st.error("❌ ڤیدیۆ بار بکە."); return
            
            _cleanup_sub_session()
            temp_dir = tempfile.mkdtemp()
            in_p = os.path.join(temp_dir, "input.mp4")
            with open(in_p, "wb") as f: f.write(video_file.read())
            
            st.session_state.sub_temp_dir = temp_dir
            st.session_state.sub_input_path = in_p
            
            raw_text = process_full_video(api_keys, in_p, vad_filter=vad_filter, songs_mode=songs_mode, existing_raw="", chunk_minutes=chunk_minutes)
            if raw_text:
                st.session_state.sub_raw = raw_text
                st.rerun()

        if resume_clicked:
            if not api_keys: st.error("❌ لایەنی کەم یەک کلیلی Gemini بنووسە."); return
            if not st.session_state.sub_input_path or not os.path.exists(st.session_state.sub_input_path):
                st.error("❌ ڤیدیۆی پێشوو نەدۆزرایەوە، تکایە لە سەرەتاوە دەست پێ بکەوە.")
                return
                
            in_p = st.session_state.sub_input_path
            existing_raw = st.session_state.get("edited_raw_text", st.session_state.sub_raw)
            
            raw_text = process_full_video(api_keys, in_p, vad_filter=vad_filter, songs_mode=songs_mode, existing_raw=existing_raw, chunk_minutes=chunk_minutes)
            if raw_text:
                st.session_state.sub_raw = raw_text
                st.rerun()

        if st.session_state.sub_raw:
            st.success("✅ وەرگێڕان ئامادەیە! دەتوانیت پێداچوونەوەی بۆ بکەیت.")
            display_raw = shift_transcript(st.session_state.sub_raw, delay_seconds)
            edited_raw = st.text_area("📝 ستەرەکان — پێش لکاندن دەسکاریان بکە", value=display_raw, height=400, key="edited_raw_text")

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
                    intro.append({"start": float_to_ass_time(current_intro_time), "end": float_to_ass_time(end_time), "alignment_tag": "{\\an2}", "text": f"{{\\c&H0000FF00&}}وەرگێڕان\\N{translator_name}"})
                    current_intro_time = end_time
                    
                if tech_name: 
                    end_time = current_intro_time + intro_duration
                    intro.append({"start": float_to_ass_time(current_intro_time), "end": float_to_ass_time(end_time), "alignment_tag": "{\\an2}", "text": f"{{\\c&H00FFFF00&}}تەکنیک\\N{tech_name}"})
                    current_intro_time = end_time

                has_bottom_intro = bool(translator_name or tech_name)
                for c in cues:
                    if has_bottom_intro and secs(c["start"]) < current_intro_time: c["alignment_tag"] = "{\\an8}"
                    else:
                        if "alignment_tag" not in c: c["alignment_tag"] = "{\\an2}"

                full_cues = intro + cues
                ass_txt = build_ass_file(full_cues, font_size, wm_text, wm_color, wm_font_size, wm_alignment)
                srt_txt = build_srt_file(cues)
                
                with open(ass_p, "w", encoding="utf-8") as f: f.write(ass_txt)
                with open(srt_p, "w", encoding="utf-8") as f: f.write(srt_txt)

                with st.spinner("🔥 خەریکی لکاندنی ژێرنووسە بە ڤیدیۆکەوە (FFmpeg)..."):
                    try: burn_subtitles(in_p, ass_p, out_p)
                    except Exception as e: st.error(f"❌ هەڵە لە FFmpeg:\n`{e}`"); return

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
