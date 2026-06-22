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
    color_normal = st.color_picker("ڕەنگی ژێرنووسی ئاسایی", "#FFFFFF")
    color_song = st.color_picker("ڕەنگی گۆرانی", "#FFD700")
    color_translator = st.color_picker("ڕەنگی ناوی وەرگێڕ", "#00FFFF")
    color_tech = st.color_picker("ڕەنگی ناوی تەکنیک", "#FF69B4")
    color_logo = st.color_picker("ڕەنگی لۆگۆ/واتەرمارک", "#AAAAAA")

    # --- ناوی ئەنیمە ---
    st.subheader("📺 ناوی ئەنیمە / فیلم")
    anime_name = st.text_input("ناوی ئەنیمە/فیلم", "")
    anime_start = st.text_input("کاتی دەستپێک (بۆ نموونە 0:00:00.00)", "0:00:00.00")
    anime_end = st.text_input("کاتی کۆتایی (بۆ نموونە 0:00:05.00)", "0:00:05.00")
    anime_color = st.color_picker("ڕەنگی ناوی ئەنیمە", "#FFFFFF")

    # --- ناوی وەرگێڕ ---
    st.subheader("✍️ ناوی وەرگێڕ")
    translator_name = st.text_input("ناوی وەرگێڕ", "")
    trans_start = st.text_input("کاتی دەستپێک (وەرگێڕ)", "0:00:00.00")
    trans_end = st.text_input("کاتی کۆتایی (وەرگێڕ)", "0:00:05.00")

    # --- ناوی تەکنیک ---
    st.subheader("🛠️ ناوی تەکنیک / پێشکەشکار")
    tech_name = st.text_input("ناوی تەکنیک", "")
    tech_start = st.text_input("کاتی دەستپێک (تەکنیک)", "0:00:00.00")
    tech_end = st.text_input("کاتی کۆتایی (تەکنیک)", "0:00:05.00")

    # --- لۆگۆ/واتەرمارک ---
    st.subheader("💧 لۆگۆ / واتەرمارک")
    logo_text = st.text_input("تێکستی لۆگۆ", "")
    logo_pos = st.selectbox("شوێنی لۆگۆ", ["چەپ", "ناوەڕاست", "ڕاست"])
    logo_size = st.slider("قەبارەی لۆگۆ", 10, 30, 14)

    # --- Whisper مۆدێل ---
    st.subheader("🎙️ Whisper")
    whisper_model_size = st.selectbox(
        "مۆدێلی Whisper",
        ["large-v3-turbo", "large-v3", "medium", "small"],
        index=0,
    )


# ═══════════════════════════════════════════════
#  یارمەتیدەرەکانی رەنگ
# ═══════════════════════════════════════════════
def hex_to_ass_color(hex_color: str) -> str:
    """وەرگێڕانی رەنگی HEX بۆ فۆرماتی ASS (&HAABBGGRR)"""
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"&H00{b:02X}{g:02X}{r:02X}"


def logo_alignment(pos: str) -> int:
    mapping = {"چەپ": 1, "ناوەڕاست": 2, "ڕاست": 3}
    return mapping.get(pos, 2)


# ═══════════════════════════════════════════════
#  دروستکردنی فایلی ASS
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
    c_song = hex_to_ass_color(color_song)
    c_translator = hex_to_ass_color(color_translator)
    c_tech = hex_to_ass_color(color_tech)
    c_logo = hex_to_ass_color(color_logo)
    c_anime = hex_to_ass_color(anime_color)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Normal,{font_name},{font_size},{c_normal},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,2,30,30,50,1
Style: Song,{font_name},{font_size},{c_song},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,2,30,30,50,1
Style: TopTitle,{font_name},{font_size + 4},{c_anime},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,8,30,30,40,1
Style: TranslatorName,{font_name},{font_size - 2},{c_translator},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,2,30,30,90,1
Style: TechName,{font_name},{font_size - 2},{c_tech},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,2,1,2,30,30,120,1
Style: Logo,{font_name},{logo_size},{c_logo},&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,1,0,{logo_pos},20,20,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []

    # ناوی ئەنیمە (گۆشەی سەرەوە)
    if anime_name.strip():
        events.append(
            f"Dialogue: 0,{anime_start},{anime_end},TopTitle,,0,0,0,,{anime_name}"
        )

    # ناوی وەرگێڕ
    if translator_name.strip():
        events.append(
            f"Dialogue: 0,{trans_start},{trans_end},TranslatorName,,0,0,0,,وەرگێڕ: {translator_name}"
        )

    # ناوی تەکنیک
    if tech_name.strip():
        events.append(
            f"Dialogue: 0,{tech_start},{tech_end},TechName,,0,0,0,,{tech_name}"
        )

    # لۆگۆ (هەموو ماوەی ڤیدیۆ)
    if logo_text.strip() and subtitles:
        last_end = subtitles[-1]["end"]
        events.append(
            f"Dialogue: 0,0:00:00.00,{last_end},Logo,,0,0,0,,{logo_text}"
        )

    # ژێرنووسەکان
    for item in subtitles:
        text = item.get("translated", item["text"])
        start = item["start"]
        end = item["end"]

        # دیاریکردنی گۆرانی بە نیشانەی ♪
        if "♪" in text or "♫" in text:
            style = "Song"
        else:
            style = "Normal"

        # دڵنیابوون لە نەبوونی Newline نەخواستراو
        text = text.replace("\n", "\\N")
        events.append(f"Dialogue: 0,{start},{end},{style},,0,0,0,,{text}")

    return header + "\n".join(events) + "\n"


# ═══════════════════════════════════════════════
#  کاتی Whisper بۆ فۆرماتی ASS
# ═══════════════════════════════════════════════
def seconds_to_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    cs = round((s - int(s)) * 100)
    return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"


# ═══════════════════════════════════════════════
#  دەرهێنانی ژێرنووس بە Whisper
# ═══════════════════════════════════════════════
def transcribe_audio(audio_path: str, model_size: str) -> list[dict]:
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(audio_path, beam_size=5, vad_filter=True)
    result = []
    for i, seg in enumerate(segments, start=1):
        result.append(
            {
                "index": i,
                "start": seconds_to_ass_time(seg.start),
                "end": seconds_to_ass_time(seg.end),
                "text": seg.text.strip(),
            }
        )
    return result


# ═══════════════════════════════════════════════
#  داکیراندنی ژێرنووس بۆ ناو ڤیدیۆ بە FFmpeg
# ═══════════════════════════════════════════════
def burn_subtitles(video_path: str, ass_path: str, output_path: str, font_dir: str) -> bool:
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"ass={ass_path}:fontsdir={font_dir}",
        "-preset", "medium",
        "-crf", "28",
        output_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        return result.returncode == 0
    except Exception:
        return False


# ═══════════════════════════════════════════════
#  بەشی سەرەکی ئەپ
# ═══════════════════════════════════════════════

# Auto-reset: ئەگەر ڤیدیۆی نوێ هاتە ناوەوە پاکی بکەرەوە
uploaded_file = st.file_uploader(
    "📁 ڤیدیۆی خۆت بخە ناوەوە",
    type=["mp4", "mkv", "avi", "mov", "webm"],
)

if uploaded_file is not None:
    prev_name = st.session_state.get("uploaded_filename", "")
    if prev_name != uploaded_file.name:
        # پاکردنەوەی session state
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.session_state["uploaded_filename"] = uploaded_file.name

if uploaded_file:
    st.video(uploaded_file)

    # دکمەی دەستپێکردن
    can_start = bool(api_key) and bool(selected_font_path)
    start_btn = st.button(
        "🚀 دەستپێبکە",
        disabled=not bool(can_start),
    )

    if start_btn:
        with tempfile.TemporaryDirectory() as tmpdir:
            # پاراستنی ڤیدیۆ
            video_path = os.path.join(tmpdir, uploaded_file.name)
            with open(video_path, "wb") as f:
                f.write(uploaded_file.read())

            with st.status("🔄 پڕۆسەکە بەڕێوەدەچێت...", expanded=True) as status:

                # ١. دەرهێنانی دەنگ
                st.write("🎙️ دەرهێنانی دەنگ و دروستکردنی ژێرنووسی ئەسڵی...")
                audio_path = os.path.join(tmpdir, "audio.wav")
                subprocess.run(
                    ["ffmpeg", "-y", "-i", video_path, "-vn", "-acodec", "pcm_s16le",
                     "-ar", "16000", "-ac", "1", audio_path],
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
                st.write("📄 دروستکردنی فایلی ژێرنووس (ASS)...")
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
                st.write("🎬 داکیراندنی ژێرنووس بۆ ناو ڤیدیۆ (FFmpeg)...")
                output_path = os.path.join(tmpdir, "output_subtitled.mp4")
                font_dir = str(BASE_DIR)
                success = burn_subtitles(video_path, ass_path, output_path, font_dir)

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
#  نیشاندانی ئەنجام و دەستکاریکردن
# ═══════════════════════════════════════════════
if "subtitles_translated" in st.session_state:
    st.divider()
    st.subheader("✏️ دەستکاریکردنی ژێرنووس")

    # دروستکردنی تێکستی دەستکاریکردنی
    editable_text = "\n".join(
        [
            f"{item['index']}|{item['start']} --> {item['end']}|{item.get('translated', item['text'])}"
            for item in st.session_state["subtitles_translated"]
        ]
    )

    edited = st.text_area(
        "ژێرنووسەکان دەتوانیت لێرەدا دەستکاری بکەیت",
        value=editable_text,
        height=400,
    )

    save_btn = st.button("💾 پاراستنی دەستکاریەکان", disabled=False)

    if save_btn:
        new_subs = []
        for line in edited.strip().splitlines():
            parts = line.split("|")
            if len(parts) >= 3:
                try:
                    idx = int(parts[0])
                    times = parts[1].split(" --> ")
                    start = times[0].strip()
                    end = times[1].strip()
                    text = "|".join(parts[2:]).strip()
                    new_subs.append(
                        {"index": idx, "start": start, "end": end, "translated": text, "text": text}
                    )
                except Exception:
                    continue
        st.session_state["subtitles_translated"] = new_subs
        st.success("✅ ژێرنووسەکان پارێزراون!")

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
