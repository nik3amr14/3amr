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

def ensure_streamlit_config():
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
        os.path.join(APP_DIR,  KU_FONT_FILE),
        os.path.join(ROOT_DIR, KU_FONT_FILE),
        KU_FONT_FILE,
        os.path.join(os.path.dirname(APP_DIR), KU_FONT_FILE)
    ]
    for path in possible_paths:
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            try:
                os.makedirs("/tmp", exist_ok=True)
                shutil.copy(path, KU_FONT_PATH)
                return KU_FONT_NAME
            except:
                pass
    return "Arial"

def get_base64_bg_img():
    bg_files = ["bg.png", "bg.jpg", "bg.jpeg", "bg.webp"]
    for f in bg_files:
        p = os.path.join(APP_DIR, f)
        if os.path.exists(p):
            try:
                with open(p, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode()
                return f"data:image/png;base64,{encoded_string}"
            except:
                pass
    return ""

def sec_to_ass(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int((t - int(t)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def sec_to_srt(t: float) -> str:
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    ms = int((t - int(t)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def secs(ts: str) -> float:
    try:
        ts = ts.strip().replace(",", ".")
        h, m, sf = ts.split(":")
        s, frac = (sf.split(".", 1) + ["0"])[:2]
        return int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
    except:
        return 0.0

def clean_punctuation(t: str) -> str:
    bad_chars = "؟.:!ـ؛”’?,;\"'!-_()[]{}،,+=*#$@^&|~`"
    for char in bad_chars:
        t = t.replace(char, "")
    return " ".join(t.split())

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

def shift_transcript(raw_text: str, delay_seconds: float) -> str:
    if delay_seconds == 0.0: return raw_text
    lines = []
    for line in raw_text.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            ns = max(0.0, secs(m.group(1)) + delay_seconds)
            ne = max(0.0, secs(m.group(2)) + delay_seconds)
            lines.append(f"{sec_to_ass(ns)} --> {sec_to_ass(ne)} | {m.group(3)}")
        else:
            lines.append(line)
    return "\n".join(lines)

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
    except:
        pass
    m = re.search(r"(\[.*\])", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    raise ValueError("Failed to parse JSON.")

def validate_cues(cues: list) -> list:
    out, cs, ce = [], 0.0, 0.0
    for c in cues:
        try:
            ns = float(c["start"]); ne = float(c["end"]); nt = str(c.get("text","")).strip()
        except: continue
        if not nt or ne <= ns: continue
        if ns < cs: ns = ce
        if ne <= ns: continue
        out.append({"start": round(ns,3), "end": round(ne,3), "text": nt})
        cs, ce = ns, ne
    return out

# ══════════════════════════════════════════════════════════
#  GEMINI TRANSLATION (Pro Logic with Temp 0.8 & Cinematic Prompt)
# ══════════════════════════════════════════════════════════
def gemini_translate(api_keys, current_key_index, transcript_chunk, thinking_budget, selected_model):
    # پڕۆمپتی نوێ: یەکجار سینەمایی، پاراو، و دوور لە وەرگێڕانی وشک
    system_prompt = """
تۆ لێهاتووترین، زیرەکترین و هەستیارترین دەرهێنەری دۆبلاژ و وەرگێڕی سینەماییت لە زمانی ئینگلیزییەوە بۆ کوردی سۆرانی.
ئامانجی تۆ ئەوەیە وەرگێڕانێکی هێندە پاراو، زیندوو و پڕ هەست بکەیت کە بینەر هەست بکات کارەکتەرەکان خۆیان کوردن و بە زگماکی قسە دەکەن!

یاساکانی وەرگێڕان (زۆر توند و نەگۆڕ):
١. وەرگێڕانی ڕۆح و مانا: بە هیچ جۆرێک وەرگێڕانی حەرفی (وشە بە وشە) مەکە. مانا، هەست، و مەبەستی کارەکتەرەکە بگەیەنە.
٢. دەستەواژە و پەندی کوردی: لەبری قسەی وشک و ڕۆبۆتی، ئیدیۆم و دەستەواژەی جوانی کوردی بەکاربهێنە کە لەگەڵ دیمەنەکە بگونجێت. (نموونە: لەبری "تۆ شێتیت"، بنووسە "مێشکت لەدەست داوە" یان "ئاگات لە خۆتە").
٣. پاراستنی کاتەکان (Timestamps): دەبێت کلیلەکانی "start" و "end" بەبێ گۆڕینی یەک پۆینت وەک خۆیان بنووسرێنەوە. کاتەکان هێڵی سوورن!
٤. داماڵینی خاڵبەندی: هیچ جۆرە خاڵبەندییەک (؟ . ، ! : ؛ " ' - _) لە دەقە کوردییەکەدا مەنووسە.
٥. هاوتایی دێڕەکان: دەبێت ژمارەی دێڕەکان ڕێک یەکسان بێت بە پرسیارەکە. هیچ دێڕێک مەپەڕێنە (تەنانەت ئەگەر هەناسەدان یان چرپەش بێت).
٦. فۆرماتی وەڵام: تەنها و تەنها لیستی JSON بنێرە، بێ هیچ قسەیەکی زیادە.
"""

    user_prompt = f"Translate ALL cues perfectly to Kurdish Sorani:\n{json.dumps(transcript_chunk, ensure_ascii=False)}"
    status_msg = st.empty()
    
    valid_keys = [k.strip() for k in api_keys if k.strip()]
    if not valid_keys:
        return [], current_key_index

    # دروستکردنی لیستی مۆدێلەکان - سەرەتا مۆدێلەکەی خۆت، پاشان یەدەگ
    models_to_try = [selected_model]
    fallback_pool = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-3-flash-preview"]
    for m in fallback_pool:
        if m not in models_to_try and m != "gemini-3.1-flash-lite":
            models_to_try.append(m)

    max_attempts = len(valid_keys) * 2
    
    # هەوڵدان بۆ هەر مۆدێلێک بە نۆرە
    for m_name in models_to_try:
        attempt = 0
        while attempt < max_attempts:
            cur_key = valid_keys[current_key_index % len(valid_keys)]
            try:
                client = genai.Client(api_key=cur_key)
                
                if thinking_budget == 0:
                    status_msg.info(f"⚡ مۆدێلی [{m_name}] - خەریکی وەرگێڕانی خێران بە کلیلی {current_key_index + 1}...")
                    config_params = dict(
                        system_instruction=system_prompt, 
                        temperature=0.8,  # گەرمی کرایە 0.8 بۆ وەرگێڕانی شاز و سروشتی
                        max_output_tokens=65536,
                        response_mime_type="application/json"
                    )
                else:
                    status_msg.info(f"🧠 مۆدێلی [{m_name}] - خەریکی بیرکردنەوە و وەرگێڕانین بە کلیلی {current_key_index + 1}...")
                    config_params = dict(
                        system_instruction=system_prompt, 
                        temperature=0.8,  # گەرمی کرایە 0.8
                        max_output_tokens=65536,
                        response_mime_type="application/json",
                        thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget)
                    )
                
                resp = client.models.generate_content(
                    model=m_name,
                    contents=[user_prompt],
                    config=types.GenerateContentConfig(**config_params)
                )
                
                data = extract_json(resp.text)
                status_msg.empty()
                if data: 
                    return data, current_key_index
                
            except Exception as e:
                err_str = str(e)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "Quota" in err_str:
                    current_key_index = (current_key_index + 1) % len(valid_keys)
                    status_msg.warning(f"⚠️ کلیلەکە ماندوو بوو! گۆڕدرا بۆ کلیلی ژمارە {current_key_index + 1}...")
                    time.sleep(2)
                    attempt += 1
                elif "503" in err_str or "UNAVAILABLE" in err_str or "demand" in err_str.lower():
                    status_msg.warning(f"⚠️ مۆدێلی {m_name} قەرەباڵغە لای گووگڵ! دەچینە سەر مۆدێلی یەدەگ...")
                    time.sleep(2)
                    break # شکاندنی بازنەی ئەم مۆدێلە و چوونە سەر مۆدێلی داهاتوو لە models_to_try
                else:
                    status_msg.error(f"⏳ کێشەی پەیوەندی هەیە، دووبارە تاقیدەکەینەوە...")
                    time.sleep(3)
                    attempt += 1

    status_msg.error("❌ هەموو هەوڵەکان بۆ ئەم بڕگەیە شکستیان هێنا بەهۆی قەرەباڵغی سێرڤەرەکانی گووگڵەوە.")
    return [], current_key_index

# ══════════════════════════════════════════════════════════
#  FASTER WHISPER (Dynamic Audio Normalization Fix)
# ══════════════════════════════════════════════════════════
@st.cache_resource
def load_whisper():
    return WhisperModel("medium", device="cpu", compute_type="int8")

def extract_audio(video_path, audio_path):
    # بەکارهێنانی dynaudnorm بۆ ئەوەی دەنگی چرپە و خەیاڵ زۆر بە جوانی بەرز بکاتەوە بێ تێکدانی دەنگە بەرزەکان
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, 
        "-vn", "-ac", "1", "-ar", "16000", 
        "-af", "dynaudnorm=f=150:g=15", 
        audio_path
    ], capture_output=True, check=True)

def transcribe_audio(audio_path):
    model = load_whisper()
    # ڕێکخستنی زۆر هەستیار بۆ ئەوەی هیچ چرپەیەک و خەیاڵێک لەگەڵ مۆسیقادا ون نەبێت
    kwargs = dict(
        beam_size=5,
        word_timestamps=True,
        vad_filter=True,
        condition_on_previous_text=False, # ڕێگری دەکات لە گیرخواردن و دووبارەبوونەوە
        no_speech_threshold=0.3,          # زۆر هەستیار کراوە بۆ دەنگی کز
        compression_ratio_threshold=2.4,
        temperature=0.0,
        vad_parameters=dict(min_silence_duration_ms=300)
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
            if seg_text:
                cues.append({"start": round(float(seg.start), 2), "end": round(float(seg.end), 2), "text": seg_text})
            continue

        for w in seg.words:
            ws, we = float(w.start), float(w.end)
            wt = str(w.word).strip()
            if not wt: continue
            if t0 is None: t0 = ws
            if t1 is not None and (ws - t1) > 0.3:
                flush()
                t0 = ws
            buf.append(wt)
            t1 = we
            if (we - t0 >= MAX_SUB_DURATION) or wt[-1] in ".!?؟":
                flush()

    flush()
    return cues

def build_chunks(cues, chunk_minutes):
    # سیستەمی بڕگەکردنی پارێزراو - دڵنیایی دەدات کە هیچ دێڕێک ون نابێت
    max_seconds = chunk_minutes * 60
    chunks, current, chunk_start = [], [], None
    for item in cues:
        if chunk_start is None: 
            chunk_start = item["start"]
        if item["end"] - chunk_start > max_seconds and current:
            chunks.append(current)
            current = [item]
            chunk_start = item["start"]
        else:
            current.append(item)
    if current: 
        chunks.append(current)
    return chunks

# ══════════════════════════════════════════════════════════
#  ORCHESTRATOR
# ══════════════════════════════════════════════════════════
def process_full_video(api_keys, video_path, existing_raw="", chunk_minutes=5, thinking_budget=2048, selected_model="gemini-3.5-flash"):
    audio_path = video_path.replace(".mp4", ".wav")
    last_translated_sec = parse_existing_raw_to_last_time(existing_raw)
    
    with st.spinner("🎵 خەریکی دەرهێنانی دەنگ و سافکردنیەتی (Audio Normalization)..."):
        extract_audio(video_path, audio_path)
        
    with st.spinner("📝 خەریکی نووسینەوەی دەنگەکەیە بە وردی (Faster-Whisper)..."):
        cues = transcribe_audio(audio_path)
        try: os.remove(audio_path)
        except: pass
        if not cues:
            st.error("❌ هیچ دیالۆگێک لە ڤیدیۆکەدا نەدۆزرایەوە.")
            return existing_raw
            
    status_header = st.empty()
    percent_bar = st.progress(0)
    percent_text = st.empty()

    chunks = build_chunks(cues, chunk_minutes)
    all_cues = []
    if existing_raw: 
        all_cues.extend(parse_raw_text(existing_raw))
        
    total = len(chunks)
    current_key_index = 0
    
    for index, chunk in enumerate(chunks):
        chunk_last_end = chunk[-1]["end"] if chunk else 0.0
        
        # ڕێژەی سەدی ڕاستەقینە
        pct = int((index / total) * 100)
        percent_bar.progress(index / total)
        percent_text.markdown(f"**لە ٪{pct} ی ڤیدیۆکە تەواو بووە...**")
        
        if chunk_last_end <= last_translated_sec:
            continue
            
        active_items = [c for c in chunk if c["start"] >= last_translated_sec]
        if not active_items:
            continue
            
        start_min = int(active_items[0]["start"] // 60)
        end_min = int(active_items[-1]["end"] // 60)
        status_header.warning(f"🔄 خەریکی وەرگێڕانە بۆ خولەکی **{start_min}** تا **{end_min}**... (بڕگەی {index + 1} لە {total})")
            
        translated, current_key_index = gemini_translate(api_keys, current_key_index, active_items, thinking_budget, selected_model)
        
        if not translated:
            st.error(f"❌ پڕۆسەکە وەستا لە بڕگەی {index + 1} بەهۆی کێشەی سێرڤەر. تکایە دواتر 'بەردەوام بوون' دابگرە.")
            return "\n".join([f"{sec_to_ass(c['start'])} --> {sec_to_ass(c['end'])} | {c['text']}" for c in validate_cues(all_cues)])
            
        all_cues.extend(translated)
            
    percent_bar.progress(1.0)
    percent_text.success("🎉 لە ٪100 تەواو بوو!")
    status_header.empty()
    
    validated = validate_cues(all_cues)
    raw_lines = [f"{sec_to_ass(c['start'])} --> {sec_to_ass(c['end'])} | {c['text']}" for c in validated]
    return "\n".join(raw_lines)

# ══════════════════════════════════════════════════════════
#  ASS & SRT BUILDERS
# ══════════════════════════════════════════════════════════
def hex_to_ass(h: str) -> str:
    h = h.lstrip("#").upper().ljust(6, "0")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}&"

def get_video_resolution(video_path: str) -> tuple:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, check=True
        )
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except:
        return 1280, 720

def build_ass_file(cues, font_size, wm_text, wm_color, wm_font_size, wm_alignment, video_path=""):
    fn = find_kurdish_font()
    wma = hex_to_ass(wm_color)
    vw, vh = get_video_resolution(video_path) if video_path else (1280, 720)

    # چارەسەری کێشەی ناوە ئینگلیزییەکان لەسەر شاشەی مۆبایل و پلەیەرەکان
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {vw}",
        f"PlayResY: {vh}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{fn},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        f"Style: CornerStyle,{fn},30,&H00E0E0E0,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,9,20,20,20,1",
        f"Style: WatermarkStyle,Arial,{wm_font_size},{wma},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,1.5,0,7,15,20,20,1",
        f"Style: TranslatorStyle,{fn},40,&H0000FF00,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        f"Style: TechStyle,{fn},40,&H00FFFF00,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,1.5,0,2,30,30,20,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]

    events = []
    if wm_text:
        events.append(f"Dialogue: 0,0:00:00.00,9:59:59.99,WatermarkStyle,,0,0,0,,{{\\an{wm_alignment}}}{wm_text}")

    for c in cues:
        txt = c.get("text", "")
        # تەنها دەقەکان خاوێن دەکەینەوە، تاگەکان لادەبەین ئەگەر بمێنن
        if "{\\" not in txt:
            txt = clean_punctuation(txt)
            
        a_tag = c.get("alignment_tag", "{\\an2}")
        style = c.get("style", "Default")
        events.append(f"Dialogue: 0,{c['start']},{c['end']},{style},,0,0,0,,{a_tag}{txt}")

    return "\n".join(header + events)

def build_srt_file(cues):
    lines = []
    for idx, c in enumerate(cues, start=1):
        s = sec_to_srt(secs(c["start"]))
        e = sec_to_srt(secs(c["end"]))
        txt = clean_punctuation(re.sub(r'\{\\[^}]*\}', '', c.get("text", "")))
        lines.append(f"{idx}\n{s} --> {e}\n{txt}\n")
    return "\n".join(lines)

def burn_subtitles(video_path, ass_path, output_path):
    subprocess.run(["ffmpeg", "-y", "-i", video_path, "-vf", f"ass={ass_path}:fontsdir=/tmp", "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-c:a", "copy", output_path], capture_output=True, check=True)

def auto_dl(data: bytes, name: str, mime: str):
    b64 = base64.b64encode(data).decode()
    components.html(f'<a id="xdl" href="data:{mime};base64,{b64}" download="{name}"></a><script>setTimeout(function(){{document.getElementById("xdl").click();}},800);</script>', height=0)

def _cleanup_sub_session():
    temp_dir = st.session_state.get("sub_temp_dir")
    if temp_dir and os.path.isdir(temp_dir):
        try: shutil.rmtree(temp_dir, ignore_errors=True)
        except: pass
    st.session_state.sub_raw = None
    st.session_state.sub_input_path = None
    st.session_state.sub_temp_dir = None

# ══════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════
def main():
    ensure_streamlit_config()
    st.set_page_config(page_title="Sorani Subtitle Studio", layout="wide")
    
    bg_data = get_base64_bg_img()
    if bg_data:
        bg_style = f"""<style>[data-testid="stAppViewContainer"] {{ background-image: linear-gradient(rgba(18, 18, 18, 0.88), rgba(18, 18, 18, 0.88)), url("{bg_data}"); background-size: cover; background-position: center; background-attachment: fixed; }} [data-testid="stHeader"] {{ background-color: rgba(0,0,0,0) !important; }} </style>"""
    else:
        bg_style = """<style>[data-testid="stAppViewContainer"] { background-color: #121212 !important; } [data-testid="stHeader"] { background-color: rgba(0,0,0,0) !important; } </style>"""
    st.markdown(bg_style, unsafe_allow_html=True)
    
    st.title("🎬 Kurdish Sorani Subtitle Generator")

    st.subheader("🔑 کلیلەکانی Gemini")
    kc1, kc2 = st.columns(2)
    with kc1:
        key1 = st.text_input("🔑 کلیلی یەکەم", type="password")
        key2 = st.text_input("🔑 کلیلی دووەم", type="password")
    with kc2:
        key3 = st.text_input("🔑 کلیلی سێیەم", type="password")
        key4 = st.text_input("🔑 کلیلی چوارەم", type="password")
        
    api_keys = [k.strip() for k in [key1, key2, key3, key4] if k.strip()]

    video_file = st.file_uploader("📁 ڤیدیۆ بار بکە (MP4/MOV)", type=["mp4", "mov", "mkv", "avi", "webm"])

    st.markdown("---")
    
    st.subheader("🤖 هەڵبژاردنی مۆدێلی زیرەکی دەستکرد")
    selected_model_input = st.selectbox(
        "مۆدێلی دڵخوازی خۆت هەڵبژێرە:",
        [
            "gemini-3.5-flash (نوێترین و باشترین بۆ وەرگێڕانی سینەمایی)",
            "gemini-2.5-flash (سەقامگیرترین مۆدێل)",
            "gemini-3-flash-preview (تاقیکاری)",
            "gemini-3.1-flash-lite (خێرا و سووک - تەنها بە دەستی خۆت کاردەکات)"
        ],
        index=0
    )
    selected_model = selected_model_input.split(" ")[0].strip()

    st.markdown("---")
    
    st.subheader("⚡ شێوازی بیرکردنەوەی زیرەکی دەستکرد")
    speed_mode = st.radio(
        "ئاستی بیرکردنەوە و خێرایی هەڵبژێڕە:", 
        [
            "⚡ یەکجار خێرا (بەبێ بیرکردنەوە - وەرگێڕانی خێرا لە چەند چرکەیەکدا)", 
            "⚖️ ستاندارد و هاوسەنگ (وەرگێڕانی خێرا بە مانا و پاراستنی فۆرمات)",
            "🧠 زۆر قووڵ و ورد (وەرگێڕانی قووڵ بەڵام هێواشترە)"
        ], 
        index=1, horizontal=True, label_visibility="collapsed"
    )
    
    if "⚡ یەکجار خێرا" in speed_mode: thinking_budget = 0
    elif "⚖️ ستاندارد" in speed_mode: thinking_budget = 2048
    else: thinking_budget = -1

    st.markdown("---")
    
    st.subheader("⚙️ بڕی وەرگێڕان بە یەکجار")
    chunk_minutes = st.slider("چەند خولەک بەیەکجار بنێرێت؟", 3, 15, 6, help="پڕۆسەی بڕگەکردن ئێستا سەد لە سەد پارێزراوە و هیچ ناپەڕێنێت.")

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
    delay_seconds = st.slider("⏱️ کاتی ژێرنووس (+/- چرکە)", -10.0, 10.0, 0.0, 0.1)

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

    if start_clicked:
        if not api_keys: st.error("❌ لایەنی کەم یەک کلیلی Gemini لە بۆکسەکاندا بنووسە."); return
        if not video_file: st.error("❌ ڤیدیۆ بار بکە."); return
        
        _cleanup_sub_session()
        temp_dir = tempfile.mkdtemp()
        in_p = os.path.join(temp_dir, "input.mp4")
        with open(in_p, "wb") as f: f.write(video_file.read())
        
        st.session_state.sub_temp_dir = temp_dir
        st.session_state.sub_input_path = in_p
        
        raw_text = process_full_video(api_keys, in_p, existing_raw="", chunk_minutes=chunk_minutes, thinking_budget=thinking_budget, selected_model=selected_model)
        if raw_text:
            st.session_state.sub_raw = raw_text
            st.rerun()

    if resume_clicked:
        if not api_keys: st.error("❌ لایەنی کەم یەک کلیلی Gemini لە بۆکسەکاندا بنووسە."); return
        if not st.session_state.sub_input_path or not os.path.exists(st.session_state.sub_input_path):
            st.error("❌ ڤیدیۆی پێشوو نەدۆزرایەوە، تکایە لە سەرەتاوە دەست پێ بکەوە.")
            return
            
        in_p = st.session_state.sub_input_path
        existing_raw = st.session_state.get("edited_raw_text", st.session_state.sub_raw)
        
        raw_text = process_full_video(api_keys, in_p, existing_raw=existing_raw, chunk_minutes=chunk_minutes, thinking_budget=thinking_budget, selected_model=selected_model)
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
            t = 0.0
            if anime_name: 
                label = anime_name + (f"\\N({season_ep})" if season_ep else "")
                intro.append({"start": "0:00:00.00", "end": "0:00:15.00", "style": "CornerStyle", "alignment_tag": "{\\an9}", "text": label})
                
            if translator_name: 
                end = t + intro_duration
                intro.append({"start": sec_to_ass(t), "end": sec_to_ass(end), "style": "TranslatorStyle", "alignment_tag": "{\\an2}", "text": f"وەرگێڕان\\N{translator_name}"})
                t = end
                
            if tech_name: 
                end = t + intro_duration
                intro.append({"start": sec_to_ass(t), "end": sec_to_ass(end), "style": "TechStyle", "alignment_tag": "{\\an2}", "text": f"تەکنیک\\N{tech_name}"})
                t = end

            has_bottom = bool(translator_name or tech_name)
            for c in cues:
                if has_bottom and secs(c["start"]) < t: c["alignment_tag"] = "{\\an8}"
                else: c.setdefault("alignment_tag", "{\\an2}")

            full_cues = intro + cues
            ass_txt = build_ass_file(full_cues, font_size, wm_text, wm_color, wm_font_size, wm_alignment, video_path=in_p)
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
            c1.download_button("⬇️ ڤیدیۆ", vb, "subtitled.mp4", "video/mp4", use_container_width=True)
            c2.download_button("⬇️ SRT", srt_txt, "subtitle.srt", "text/plain", use_container_width=True)
            c3.download_button("⬇️ ASS", ass_txt, "subtitle.ass", "text/plain", use_container_width=True)
            st.video(vb)

if __name__ == "__main__":
    main()
