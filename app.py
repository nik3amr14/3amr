import os
import re
import glob
import tempfile
import subprocess
import streamlit as st
from pathlib import Path
from faster_whisper import WhisperModel
from ai_translator import translate_to_kurdish_sorani

# ═══════════════════════════════════════════════
#  تەنظیمکردنی ڕووکار
# ═══════════════════════════════════════════════
st.set_page_config(
    page_title="Kurdish Sorani Subtitle Generator",
    page_icon="🎬",
    layout="wide",
)
st.title("🎬 Kurdish Sorani Cinematic Subtitle Generator")
st.caption("v7.0 — Powered by Faster-Whisper & Google Gemini")

# ═══════════════════════════════════════════════
#  دیاریکردنی فۆنتەکان
# ═══════════════════════════════════════════════
BASE_DIR = Path(__file__).parent
font_files = sorted(glob.glob(str(BASE_DIR / "*.ttf")))
font_names = [Path(f).stem for f in font_files]
font_map = dict(zip(font_names, font_files))

# ═══════════════════════════════════════════════
#  Sidebar
# ═══════════════════════════════════════════════
with st.sidebar:
    st.header("⚙️ ڕێکخستنەکان")

    # --- API Key ---
    api_key = st.text_input("🔑 Gemini API Key", type="password")

    # --- فۆنت ---
    st.subheader("🔤 فۆنت")
    if font_names:
        selected_font_name = st.selectbox("هەڵبژاردنی فۆنت", font_names)
        selected_font_path = font_map[selected_font_name]
    else:
        st.warning("هیچ فایلی .ttf نەدۆزرایەوە!")
        selected_font_name = ""
        selected_font_path = ""

    font_size = st.slider("قەبارەی فۆنتی ژێرنووس", 14, 40, 22)

    # --- مۆدێل و ئاستی بیرکردنەوە ---
    st.subheader("🤖 مۆدێل")
    gemini_model = st.selectbox(
        "مۆدێلی Gemini",
        ["gemini-1.5-flash", "gemini-1.5-pro", "gemini-2.0-flash"],
        index=0,
    )
    thinking_level = st.radio(
        "ئاستی بیرکردنەوە",
        ["standard", "deep"],
        horizontal=True,
    )

    # --- ڕەنگەکان ---
    st.subheader("🎨 ڕەنگەکان")
    color_normal  = st.color_picker("ڕەنگی ژێرنووسی ئاسایی", "#FFFFFF")
    color_song    = st.color_picker("ڕەنگی گۆرانی",          "#FFD700")
    color_translator = st.color_picker("ڕەنگی ناوی وەرگێڕ", "#00FFFF")
    color_tech    = st.color_picker("ڕەنگی ناوی تەکنیک",    "#FF69B4")
    color_logo    = st.color_picker("ڕەنگی لۆگۆ/واتەرمارک", "#CCCCCC")

    # --- ناوی ئەنیمە ---
    st.subheader("📺 ناوی ئەنیمە / فیلم")
    anime_name  = st.text_input("ناوی ئەنیمە/فیلم", "")
    anime_start = st.text_input("کاتی دەستپێک (ئەنیمە)", "0:00:00.00")
    anime_end   = st.text_input("کاتی کۆتایی (ئەنیمە)",  "0:00:05.00")
    anime_color = st.color_picker("ڕەنگی ناوی ئەنیمە",   "#FFFFFF")

    # --- ناوی وەرگێڕ ---
    st.subheader("✍️ ناوی وەرگێڕ")
    translator_name = st.text_input("ناوی وەرگێڕ", "")
    trans_start     = st.text_input("کاتی دەستپێک (وەرگێڕ)", "0:00:00.00")
    trans_end       = st.text_input("کاتی کۆتایی (وەرگێڕ)",  "0:00:05.00")

    # --- ناوی تەکنیک ---
    st.subheader("🛠️ ناوی تەکنیک / پێشکەشکار")
    tech_name  = st.text_input("ناوی تەکنیک", "")
    tech_start = st.text_input("کاتی دەستپێک (تەکنیک)", "0:00:00.00")
    tech_end   = st.text_input("کاتی کۆتایی (تەکنیک)",  "0:00:05.00")

    # --- لۆگۆ/واتەرمارک ---
    st.subheader("💧 لۆگۆ / واتەرمارک")
    logo_text = st.text_input("تێکستی لۆگۆ", "")
    logo_pos  = st.selectbox("شوێنی لۆگۆ", ["چەپ", "ناوەڕاست", "ڕاست"])
    logo_size = st.slider("قەبارەی لۆگۆ", 10, 30, 14)

    # --- Whisper مۆدێل ---
    st.subheader("🎙️ Whisper")
    whisper_model_size = st.selectbox(
        "مۆدێلی Whisper",
        ["large-v3-turbo", "large-v3", "medium", "small"],
        index=0,
    )


# ═══════════════════════════════════════════════
#  یارمەتیدەرەکانی ڕەنگ
# ═══════════════════════════════════════════════
def hex_to_ass_color(hex_color: str) -> str:
    """وەرگێڕانی ڕەنگی HEX بۆ فۆرماتی ASS (&H00BBGGRR)"""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def logo_alignment(pos: str) -> int:
    return {"چەپ": 1, "ناوەڕاست": 2, "ڕاست": 3}.get(pos, 2)


# ═══════════════════════════════════════════════
#  دروستکردنی فایلی ASS — ستایلی سینەماتیک
# ═══════════════════════════════════════════════
def build_ass_content(
    subtitles: list[dict],
    font_name: str,
    font_size: int,
    color_normal: str,
    color_song: str,
    color_translator: str,
    color_tech: str,
    color_logo: str,
    anime_name: str,
    anime_start: str,
    anime_end: str,
    anime_color: str,
    translator_name: str,
    trans_start: str,
    trans_end: str,
    tech_name: str,
    tech_start: str,
    tech_end: str,
    logo_text: str,
    logo_pos: int,
    logo_size: int,
) -> str:

    c_normal = hex_to_ass_color(color_normal)
    c_song   = hex_to_ass_color(color_song)
    c_trans  = hex_to_ass_color(color_translator)
    c_tech   = hex_to_ass_color(color_tech)
    c_logo   = hex_to_ass_color(color_logo)
    c_anime  = hex_to_ass_color(anime_color)

    # outline قوڵ + سێبەری نەرم + spacing بۆ کوردی
    # Outline=3  Shadow=1.5  BackColour شەفاف
    # MarginL/R=80 بۆ نەکەوتن لە کێنارەکان
    # MarginV=60 بۆ نەکەوتن لە خوارەوەی کادر
    fs   = font_size
    fs_s = max(fs - 5, 12)   # فۆنتی بچووک بۆ ناو و لۆگۆ

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Normal,{font_name},{fs},{c_normal},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0.8,0,1,3,1.5,2,80,80,60,1
Style: Song,{font_name},{fs},{c_song},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0.8,0,1,3,1.5,2,80,80,60,1
Style: TopTitle,{font_name},{fs + 2},{c_anime},&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,1,0,1,3,1.5,8,60,60,45,1
Style: TranslatorName,{font_name},{fs_s},{c_trans},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0.5,0,1,2,1,2,80,80,105,1
Style: TechName,{font_name},{fs_s},{c_tech},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0.5,0,1,2,1,2,80,80,135,1
Style: Logo,{font_name},{logo_size},{c_logo},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0.5,{logo_pos},30,30,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    if anime_name.strip():
        events.append(
            f"Dialogue: 0,{anime_start},{anime_end},TopTitle,,0,0,0,,{anime_name}"
        )

    if translator_name.strip():
        events.append(
            f"Dialogue: 0,{trans_start},{trans_end},TranslatorName,,0,0,0,,وەرگێڕ: {translator_name}"
        )

    if tech_name.strip():
        events.append(
            f"Dialogue: 0,{tech_start},{tech_end},TechName,,0,0,0,,{tech_name}"
        )

    if logo_text.strip() and subtitles:
        last_end = subtitles[-1]["end"]
        events.append(
            f"Dialogue: 0,0:00:00.00,{last_end},Logo,,0,0,0,,{logo_text}"
        )

    for item in subtitles:
        text  = item.get("translated", item["text"])
        start = item["start"]
        end   = item["end"]
        style = "Song" if ("♪" in text or "♫" in text) else "Normal"
        text  = text.replace("\n", "\\N")
        events.append(f"Dialogue: 0,{start},{end},{style},,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


# ═══════════════════════════════════════════════
#  کاتی Whisper بۆ فۆرماتی ASS
# ═══════════════════════════════════════════════
def seconds_to_ass_time(seconds: float) -> str:
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = seconds % 60
    cs = round((s - int(s)) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


# ═══════════════════════════════════════════════
#  دەرهێنانی ژێرنووس بە Whisper
# ═══════════════════════════════════════════════
def snap_timings(segments: list[dict], gap_threshold: float = 0.4) -> list[dict]:
    """
    کاتی کۆتایی هەر سێگمەنتێک دەچسپێت بە دەستپێکی ئەوی داهاتوو
    ئەگەر نێوانیان کەمتر لە gap_threshold چرکە بوو.
    ئەمەش دەکات ژێرنووسەکە هەتا دەستپێکی قسەی داهاتوو بمێنێتەوە.
    """
    if len(segments) < 2:
        return segments

    result = [dict(s) for s in segments]
    for i in range(len(result) - 1):
        curr_end   = result[i]["end_sec"]
        next_start = result[i + 1]["start_sec"]
        gap = next_start - curr_end
        if 0 < gap <= gap_threshold:
            # کاتی کۆتایی بکە هەمان کاتی دەستپێکی داهاتوو
            result[i]["end_sec"] = next_start
    return result


def transcribe_audio(audio_path: str, model_size: str) -> list[dict]:
    """هەموو ژێرنووسەکان بەبێ جێهێشتن — کاتەکان snap دەکرێن"""
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    segments_iter, _ = model.transcribe(
        audio_path,
        beam_size=3,
        vad_filter=True,
        vad_parameters=dict(
            min_silence_duration_ms=500,
            speech_pad_ms=400,
            threshold=0.3,
        ),
        condition_on_previous_text=True,
        temperature=0.0,
        word_timestamps=False,
        without_timestamps=False,
        max_new_tokens=128,
    )

    raw_segments = list(segments_iter)

    # مەرحەلەی یەکەم: کۆکردنەوە بە seconds خام
    segs = []
    for seg in raw_segments:
        text = seg.text.strip()
        if not text:
            continue
        segs.append({
            "start_sec": seg.start,
            "end_sec":   seg.end,
            "text":      text,
        })

    # مەرحەلەی دووەم: snap کردنی gap‌ەکان
    segs = snap_timings(segs, gap_threshold=0.4)

    # مەرحەلەی سێیەم: convert بۆ ASS string
    result = []
    for i, seg in enumerate(segs, start=1):
        result.append({
            "index": i,
            "start": seconds_to_ass_time(seg["start_sec"]),
            "end":   seconds_to_ass_time(seg["end_sec"]),
            "text":  seg["text"],
        })

    return result


# ═══════════════════════════════════════════════
#  داکیراندنی ژێرنووس بۆ ناو ڤیدیۆ — قەبارەی هەمان ئەسڵ
# ═══════════════════════════════════════════════
def burn_subtitles(video_path: str, ass_path: str, output_path: str, font_dir: str) -> bool:
    """
    -c:v copy ناتوانرێت بەکاربێت چونکە ASS burn-in پێویستی بە re-encode هەیە.
    بەڵام -crf 18 + -preset fast کواڵیتی نزیک بە ئەسڵ دەدات بەبێ زیادبوونی قەبارە.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"ass={ass_path}:fontsdir={font_dir}",
        "-c:v", "libx264",
        "-crf",    "18",       # کواڵیتی بەرز — نزیک بە ئەسڵ
        "-preset", "fast",
        "-tune",   "animation", # باشتر بۆ ئەنیمە
        "-c:a", "copy",         # دەنگ وەک خۆی — کاتیش کەم دەکات
        "-threads", "0",
        output_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        return proc.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════
#  بەشی سەرەکی ئەپ
# ═══════════════════════════════════════════════

uploaded_file = st.file_uploader(
    "📁 ڤیدیۆی خۆت بخە ناوەوە",
    type=["mp4", "mkv", "avi", "mov", "webm"],
)

# Auto-reset کاتی ڤیدیۆی نوێ
if uploaded_file is not None:
    prev_name = st.session_state.get("uploaded_filename", "")
    if prev_name != uploaded_file.name:
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.session_state["uploaded_filename"] = uploaded_file.name

if uploaded_file:
    st.video(uploaded_file)

    can_start = bool(api_key) and bool(selected_font_path)
    start_btn = st.button("🚀 دەستپێبکە", disabled=not bool(can_start))

    if start_btn:
        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = os.path.join(tmpdir, uploaded_file.name)
            with open(video_path, "wb") as f:
                f.write(uploaded_file.read())

            with st.status("🔄 پڕۆسەکە بەڕێوەدەچێت...", expanded=True) as status:

                # ١. دەرهێنانی دەنگ
                st.write("🎙️ دەرهێنانی دەنگ...")
                audio_path = os.path.join(tmpdir, "audio.wav")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_path,
                     "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
                     audio_path],
                    capture_output=True,
                )

                # ٢. Whisper
                st.write(f"📝 Whisper ({whisper_model_size}) کار دەکات...")
                subtitles = transcribe_audio(audio_path, whisper_model_size)
                st.session_state["subtitles_raw"] = subtitles
                st.write(f"✅ {len(subtitles)} ڕیزی ژێرنووس دۆزرایەوە.")

                # ٣. وەرگێڕان
                st.write("🌐 وەرگێڕان بۆ کوردی سۆرانی...")
                translated = translate_to_kurdish_sorani(
                    subtitles,
                    api_key=api_key,
                    model_name=gemini_model,
                    thinking_level=thinking_level,
                )
                st.session_state["subtitles_translated"] = translated
                st.write("✅ وەرگێڕان تەواو بوو.")

                # ٤. دروستکردنی ASS
                st.write("📄 دروستکردنی فایلی ژێرنووس...")
                ass_content = build_ass_content(
                    subtitles=translated,
                    font_name=selected_font_name,
                    font_size=font_size,
                    color_normal=color_normal,
                    color_song=color_song,
                    color_translator=color_translator,
                    color_tech=color_tech,
                    color_logo=color_logo,
                    anime_name=anime_name,
                    anime_start=anime_start,
                    anime_end=anime_end,
                    anime_color=anime_color,
                    translator_name=translator_name,
                    trans_start=trans_start,
                    trans_end=trans_end,
                    tech_name=tech_name,
                    tech_start=tech_start,
                    tech_end=tech_end,
                    logo_text=logo_text,
                    logo_pos=logo_alignment(logo_pos),
                    logo_size=logo_size,
                )
                st.session_state["ass_content"] = ass_content

                ass_path = os.path.join(tmpdir, "subtitles.ass")
                with open(ass_path, "w", encoding="utf-8") as f:
                    f.write(ass_content)

                # ٥. داکیراندنی ژێرنووس بۆ ڤیدیۆ
                st.write("🎬 داکیراندنی ژێرنووس بۆ ناو ڤیدیۆ...")
                output_path = os.path.join(tmpdir, "output_subtitled.mp4")
                success = burn_subtitles(video_path, ass_path, output_path, str(BASE_DIR))

                if success:
                    st.write("✅ ڤیدیۆکە ئامادەیە!")
                    with open(output_path, "rb") as f:
                        st.session_state["output_video"] = f.read()
                    with open(ass_path, "r", encoding="utf-8") as f:
                        st.session_state["ass_file"] = f.read()
                    status.update(label="✅ هەموو پڕۆسەکان تەواو بوون!", state="complete")
                else:
                    status.update(label="❌ هەڵەیەک ڕوویدا لە FFmpeg", state="error")
                    st.error("هەڵەیەک ڕوویدا کاتی داکیراندنی ژێرنووس بۆ ڤیدیۆ.")

# ═══════════════════════════════════════════════
#  دەستکاریکردنی ژێرنووس
# ═══════════════════════════════════════════════
if "subtitles_translated" in st.session_state:
    st.divider()
    st.subheader("✏️ دەستکاریکردنی ژێرنووس")

    editable_text = "\n".join([
        f"{item['index']}|{item['start']} --> {item['end']}|{item.get('translated', item['text'])}"
        for item in st.session_state["subtitles_translated"]
    ])

    edited = st.text_area(
        "ژێرنووسەکان دەتوانیت لێرەدا دەستکاری بکەیت",
        value=editable_text,
        height=400,
    )

    if st.button("💾 پاراستنی دەستکاریەکان"):
        new_subs = []
        for line in edited.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    idx   = int(parts[0])
                    times = parts[1].split(" --> ")
                    start = times[0].strip()
                    end   = times[1].strip()
                    text  = "|".join(parts[2:]).strip()
                    new_subs.append({"index": idx, "start": start, "end": end,
                                     "translated": text, "text": text})
                except Exception:
                    continue
        st.session_state["subtitles_translated"] = new_subs
        st.success("✅ ژێرنووسەکان پارێزراون!")

# ═══════════════════════════════════════════════
#  داونلۆد
# ═══════════════════════════════════════════════
if "output_video" in st.session_state:
    st.divider()
    st.subheader("📥 داونلۆد")
    col1, col2 = st.columns(2)
    with col1:
        st.download_button(
            "⬇️ داونلۆدی ڤیدیۆی ژێرنووسدار",
            data=st.session_state["output_video"],
            file_name="kurdish_subtitled.mp4",
            mime="video/mp4",
        )
    with col2:
        if "ass_file" in st.session_state:
            st.download_button(
                "⬇️ داونلۆدی فایلی ASS",
                data=st.session_state["ass_file"],
                file_name="subtitles.ass",
                mime="text/plain",
            )
