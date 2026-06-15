import streamlit as st
from google import genai
from google.genai import types
import subprocess, tempfile, os, time, re, urllib.request, base64, shutil
import ssl
import streamlit.components.v1 as components

# ══════════════════════════════════════════════════════════
#  PATHS & FONT CONSTANTS
# ══════════════════════════════════════════════════════════
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)

KU_FONT_FILE = "Bahij Janna-Bold.ttf"
KU_FONT_PATH = os.path.join("/tmp", KU_FONT_FILE)
KU_FONT_NAME = "Bahij Janna"
KU_FONT_URLS = [
    "https://raw.githubusercontent.com/rawandahmad698/KurdishCaller/master/fonts/NRT-Bd.ttf",
    "https://github.com/rawandahmad698/KurdishCaller/raw/refs/heads/master/fonts/NRT-Bd.ttf",
    "https://github.com/Moxammad/Kurdish-Fonts-kurd-fonts--Unicode-More-than-900/raw/refs/heads/master/NRT-Bold.ttf",
    "https://fonts.gstatic.com/s/notonaskharabic/v33/ItmEoRMNNqpX1RA5pj-sbZv1SfzZkKoF9sCz7Q.ttf",
]

EN_FONT_FILE = "English-Font.ttf"
EN_FONT_PATH = os.path.join("/tmp", EN_FONT_FILE)
EN_FONT_NAME = "Lora"
EN_FONT_URLS = [
    "https://raw.githubusercontent.com/bazhdarsarwar89-crypto/3mar/main/Lora-Italic-VariableFont_wght.ttf",
    "https://fonts.gstatic.com/s/lora/v35/0QI8MX1D_JOxE7fSWYbHnDSe.ttf",
    "https://fonts.gstatic.com/s/lora/v35/0QI6MX1D_JOxE7fSaiTaEU.ttf",
]

FORMAT_MAP = {
    "MP4  — H.264  (ئەڵتەرین)":     (["-c:v","libx264","-crf","22","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/mp4",        ".mp4"),
    "MOV  — H.264  (Apple/iPhone)":  (["-c:v","libx264","-crf","22","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/quicktime",  ".mov"),
    "MKV  — H.264  (کوالیتی بەرز)": (["-c:v","libx264","-crf","18","-preset","fast",   "-c:a","aac","-b:a","192k","-threads","2"],    "video/x-matroska", ".mkv"),
    "WebM — VP9    (وێب)":           (["-c:v","libvpx-vp9","-crf","30","-b:v","0","-c:a","libopus","-b:a","128k","-threads","2"],      "video/webm",       ".webm"),
    "MP4  — H.265  (بچووکتر)":       (["-c:v","libx265","-crf","24","-preset","ultrafast","-c:a","aac","-b:a","192k","-threads","2"],  "video/mp4",        ".mp4"),
    "MP3  — تەنها دەنگ":             (["-vn","-c:a","libmp3lame","-b:a","320k"],                                                       "audio/mpeg",       ".mp3"),
}

# ══════════════════════════════════════════════════════════
#  FONT MANAGEMENT
# ══════════════════════════════════════════════════════════
def _dl_font(urls: list, save_path: str) -> bool:
    ctx = ssl._create_unverified_context()
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                data = r.read()
            if len(data) < 10_000:
                continue
            with open(save_path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            continue
    return False

def ensure_kurdish_font() -> tuple[str, str]:
    if os.path.exists(KU_FONT_PATH) and os.path.getsize(KU_FONT_PATH) > 10_000:
        return KU_FONT_PATH, KU_FONT_NAME

    local_search_paths = [
        os.path.join(ROOT_DIR, KU_FONT_FILE),
        os.path.join(APP_DIR, KU_FONT_FILE),
        KU_FONT_FILE
    ]
    
    for path in local_search_paths:
        if os.path.exists(path) and os.path.getsize(path) > 10_000:
            shutil.copy(path, KU_FONT_PATH)
            return KU_FONT_PATH, KU_FONT_NAME

    st.info("⬇️ فۆنتی کوردی لە ناوخۆدا نەدۆزرایەوە، خەریکی دابەزاندنە…")
    if _dl_font(KU_FONT_URLS, KU_FONT_PATH):
        return KU_FONT_PATH, KU_FONT_NAME

    st.error("❌ کێشە لە دۆزینەوەی فۆنتی کوردی هەیە. تکایە دڵنیابە فایلی Bahij Janna-Bold.ttf لە پڕۆژەکەتدایە.")
    st.stop()

def ensure_english_font() -> tuple[str, str]:
    if os.path.exists(EN_FONT_PATH) and os.path.getsize(EN_FONT_PATH) > 10_000:
        return EN_FONT_PATH, EN_FONT_NAME
    if _dl_font(EN_FONT_URLS, EN_FONT_PATH):
        return EN_FONT_PATH, EN_FONT_NAME
    return "", "Arial"

def ensure_font(direction: str) -> tuple[str, str]:
    return ensure_english_font() if direction == "ku→en" else ensure_kurdish_font()

# ══════════════════════════════════════════════════════════
#  TEXT UTILS
# ══════════════════════════════════════════════════════════
def normalize_kurdish(t: str) -> str:
    return (t.replace("\u064A", "\u06CC")
             .replace("\u0643", "\u06A9")
             .replace("\u0629", "\u06D5"))

def reshape(t: str) -> str:
    return normalize_kurdish(t)

def hex_to_ass(h: str) -> str:
    h = h.lstrip("#").upper().ljust(6, "0")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}"

# لێرەدا هەموو جۆرە هێما و خاڵبەندییەک سڕاوەتەوە بۆ دەقێکی زۆر پاک
def clean_punctuation(t: str) -> str:
    bad_chars = "؟.:!ـ؛”’?,;\"'!-_()[]{}،,+=*#$@^&|~`"
    for char in bad_chars:
        t = t.replace(char, "")
    return " ".join(t.split())

# ══════════════════════════════════════════════════════════
#  TIMESTAMP HELPERS
# ══════════════════════════════════════════════════════════
_CUE_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})"
    r"\s*-->\s*"
    r"(\d{1,2}:\d{2}:\d{2}[.,]\d{1,3})"
    r"\s*[|\t]\s*(.+)"
)

def parse_transcript(raw: str) -> list[dict]:
    out = []
    for line in raw.splitlines():
        m = _CUE_RE.match(line.strip())
        if m:
            out.append({"start": m.group(1).replace(",", "."),
                        "end":   m.group(2).replace(",", "."),
                        "text":  m.group(3).strip()})
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

def secs(ts: str) -> float:
    try:
        ts = ts.strip().replace(",", ".")
        h, m, sf = ts.split(":")
        s, frac = (sf.split(".", 1) + ["0"])[:2]
        return int(h) * 3600 + int(m) * 60 + int(s) + float("0." + frac)
    except Exception:
        return 999.0

def ass_t(ts: str) -> str:
    ts = ts.replace(",", ".")
    h, m, sf = ts.split(":")
    s, frac = (sf.split(".", 1) + ["0"])[:2]
    return f"{int(h)}:{int(m):02d}:{int(s):02d}.{int((frac + '00')[:2]):02d}"

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
#  ASS BUILDER
# ══════════════════════════════════════════════════════════
def build_ass(cues: list[dict], sub_font: str, font_size: int = 52,
              wm_text: str = "", wm_color: str = "#FFFFFF",
              wm_font_size: int = 20, wm_alignment: int = 7) -> str:
    wm_ass = hex_to_ass(wm_color)
    hdr = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1280\nPlayResY: 720\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{sub_font},{font_size},"
        "&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "-1,0,0,0,100,100,0,0,1,1.5,0.5,2,30,30,40,1\n"
        f"Style: CornerStyle,{KU_FONT_NAME},30,"
        "&H00E0E0E0,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,1.5,0,9,20,20,20,1\n"
        f"Style: WatermarkStyle,{sub_font},{wm_font_size},"
        f"{wm_ass},&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,1.5,0,7,15,20,20,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, "
        "MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [hdr]
    if wm_text and wm_text.strip():
        lines.append(
            f"Dialogue: 0,{ass_t('0:00:00.00')},{ass_t('9:59:59.99')},"
            f"WatermarkStyle,,0,0,0,,{{\\an{wm_alignment}}}{reshape(wm_text.strip())}"
        )
    for c in cues:
        cleaned_text = clean_punctuation(c['text'])
        lines.append(
            f"Dialogue: 0,{ass_t(c['start'])},{ass_t(c['end'])},"
            f"{c.get('style','Default')},,0,0,0,,"
            f"{c.get('alignment_tag','{\\an2}')}{reshape(cleaned_text)}"
        )
    return "\n".join(lines)

# ══════════════════════════════════════════════════════════
#  AUTO-DOWNLOAD
# ══════════════════════════════════════════════════════════
def auto_dl(data: bytes, name: str, mime: str):
    if len(data) <= 25 * 1024 * 1024:
        b64 = base64.b64encode(data).decode()
        components.html(
            f'<a id="xdl" href="data:{mime};base64,{b64}" download="{name}"></a>'
            "<script>"
            "  setTimeout(function(){"
            "    var a=document.getElementById('xdl');"
            "    if(a){a.click();}"
            "  },800);"
            "</script>",
            height=0,
        )

# ══════════════════════════════════════════════════════════
#  GEMINI PROMPTS (With Minimal Censorship & No Punctuation)
# ══════════════════════════════════════════════════════════
_KU_PROMPT = """\
تۆ دەرهێنەری دوبلاژی سینەمایی پسپۆڕیت. ئەرکەکەت: دانانی ژێرنووسی سینەماییانەی تەواو بۆ ئەم ڤیدیۆیە بە کوردی سۆرانی.

یاساکانی دیمەنی:
١. مانادار وەربگێڕە — بە کوردیی پەتی و ڕۆژانە.
٢. وشەی "خوا" بەپێی دیمەن: ئەنیمی/فانتازیا → "خواوەند"، فیلمی ڕاستەقینە → "خوا".
٣. ئاستی سانسۆر: سانسۆری زۆر کەم و سووک (بە گشتی قسە نەشیاو و نەفرەتییەکان وەک خۆی بهێڵەرەوە، بەڵام وشە سێکسییە زۆر ناپەسەندەکان بگۆڕە بۆ هاوتاییەکی کوردی کەمێک گونجاوتر).
٤. قسەی ئاشکرا، خەیاڵ، مونۆلۆگی ناوەخۆیی، و چرپە بە تەواوی وەربگێڕە.
٥. کاتەکان دەبێت ١٠٠٪ ڕێک بن لەگەڵ دەنگەکە.
٦. زۆر گرنگ: بە هیچ شێوەیەک نەوەستیت تا کۆتا چرکەی ڤیدیۆکە! دەبێت هەموو قسەکان وەربگێڕیت بێ ئەوەی هیچ جێبهێڵیت.
٧. تەنها یەک ستەرە لە یەک کاتدا.
٨. لابردنی تەواوی خاڵبەندی و هێماکان: بە هیچ شێوەیەک هێماکانی خاڵبەندی و ماردکداون وەک (؟ . : ! ، ، " ' - _ ? !) بەکارمەهێنە. تەنها دەقێکی کوردی یەکدەست بنووسە بێ هیچ هێمایەک.
٩. ناوی کارەکتەرەکان قەدەغەیە بنووسرێت.

ئەرکی ئێستات: تەنها لە کاتی {start_time} تا {end_time} وەربگێڕە. هیچ شتێک جێمەهێڵە لەم نێوانەدا!

فۆرمات:
H:MM:SS.cs --> H:MM:SS.cs | کوردی سۆرانی لێرە
"""

_EN_PROMPT = """\
You are a cinematic dubbing director and master subtitle translator.
YOUR MISSION: Craft a world-class English subtitle track for every second of this video.

CINEMATIC RULES:
1. MEANING, NOT WORDS.
2. MINIMAL CENSORSHIP: Keep general bad/vulgar words. Only translate highly offensive/explicit sexual terms into slightly milder equivalents.
3. INNER MONOLOGUES, THOUGHTS, & WHISPERS MUST BE TRANSLATED.
4. TRUE AUDIO SYNC.
5. NO CHARACTER NAMES.
6. STRIKE OUT ALL PUNCTUATION: Do not use punctuation marks like (?, . : ! ' - _). Only plain clean text.

YOUR TASK NOW: Translate ONLY from {start_time} to {end_time}. Do not skip any dialogue in this timeframe!

FORMAT:
H:MM:SS.cs --> H:MM:SS.cs | English text here
"""

SAFETY = [
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HARASSMENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
    types.SafetySetting(category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, threshold=types.HarmBlockThreshold.BLOCK_NONE),
]

# ══════════════════════════════════════════════════════════
#  FFMPEG PROXY CREATOR
# ══════════════════════════════════════════════════════════
def create_proxy_video(in_path: str, out_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-i", in_path,
        "-vf", "scale=-2:360",
        "-c:v", "libx264", "-crf", "30", "-preset", "ultrafast",
        "-c:a", "aac", "-b:a", "64k",
        "-threads", "0",
        out_path
    ], capture_output=True)

# ══════════════════════════════════════════════════════════
#  GEMINI  —  Smart Auto-Loop Translation
# ══════════════════════════════════════════════════════════
def gemini_translate(api_key: str, video_path: str, direction: str, existing_raw: str = "") -> str:
    client = genai.Client(api_key=api_key)
    dur = get_video_duration(video_path)
    st.info(f"📏 ماوەی ڤیدیۆ: {dur:.0f} چرکە  ({dur/60:.1f} خولەک)")

    proxy_path = video_path.replace(".mp4", "_proxy.mp4")
    
    if not os.path.exists(proxy_path):
        st.info("⚡ ئامادەکردنی فایلی سووکی ٣٦٠پیکسڵ بۆ پرۆسەیەکی سەلامەت و خێرا...")
        create_proxy_video(video_path, proxy_path)
    else:
        st.info("⚡ فایلی ٣٦٠پیکسڵ پێشتر ئامادەیە، ڕاستەوخۆ بەکاردەهێنرێتەوە...")

    st.info("⬆️ خەریکی بارکردنە بۆ سێرڤەری Gemini…")
    try:
        uploaded = client.files.upload(file=proxy_path)
    except Exception as e:
        st.error(f"❌ هەڵە لە بارکردن بۆ Gemini: {e}")
        return existing_raw

    st.info("⏳ خەریکی پڕۆسەکردنە…")
    for _ in range(120):
        info = client.files.get(name=uploaded.name)
        if info.state.name == "ACTIVE":
            break
        if info.state.name == "FAILED":
            st.error("❌ پڕۆسەی Gemini شکستی هێنا لە خوێندنەوەی ڤیدیۆکە.")
            return existing_raw
        time.sleep(2)
    st.success("✅ ڤیدیۆکە ئامادەیە بۆ وەرگێڕان.")

    base_prompt = _EN_PROMPT if direction == "ku→en" else _KU_PROMPT
    chunks = []
    current_start = 0.0

    if existing_raw.strip():
        chunks.append(existing_raw.strip())
        cues = parse_transcript(existing_raw)
        if cues:
            current_start = secs(cues[-1]["end"])
            st.info(f"🔄 دەستپێکردنەوە لە کاتی: {float_to_ass_time(current_start)}")

    chunk_duration = 300.0 
    bar = st.progress(min(current_start / dur, 1.0) if dur > 0 else 0.0, "🧠 خەریکی وەرگێڕانە پارچە بە پارچە…")

    while current_start < dur:
        current_end = min(current_start + chunk_duration, dur)
        start_str = float_to_ass_time(current_start)
        end_str = float_to_ass_time(current_end)
        
        prompt = base_prompt.format(start_time=start_str, end_time=end_str)
        
        chunk_text = ""
        max_retries = 5 
        
        for attempt in range(max_retries):
            try:
                resp = client.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=[uploaded, prompt],
                    config=types.GenerateContentConfig(
                        temperature=0.1,
                        max_output_tokens=65536, 
                        safety_settings=SAFETY
                    )
                )
                chunk_text = (resp.text or "").strip()
                if chunk_text:
                    break
            except Exception as e:
                error_msg = str(e)
                if "503" in error_msg or "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                    st.warning(f"⚠️ سێرڤەری گووگڵ قەرەباڵغە یان لیمیتی وشەکان تەواو بووە. ٣٠ چرکە چاوەڕێ دەکەین... (هەوڵی {attempt+1}/{max_retries})")
                    time.sleep(30)
                else:
                    st.warning(f"⚠️ هەڵەیەک ڕوویدا: {error_msg}")
                    time.sleep(10)

        if not chunk_text:
            st.error(f"❌ نەتوانرا پارچەی {start_str} بۆ {end_str} وەربگێڕدرێت. تکایە دواتر کلیک لە 'بەردەوام بوون' بکە.")
            break

        cues = parse_transcript(chunk_text)
        if cues:
            chunks.append(chunk_text)
            current_start = secs(cues[-1]["end"])
        else:
            current_start = current_end

        progress_val = min(current_start / dur, 1.0) if dur > 0 else 1.0
        bar.progress(progress_val, f"🧠 {current_start:.0f}s / {dur:.0f}s وەرگێڕدراو")

        if dur - current_start <= 2.0:
            break
            
        # ⏳ چاوەڕوانیی ٥٠ چرکەی تەواو لە نێوان پارچەکاندا بۆ ڕێگریکردن لە بلۆککردنی ئەکاونتی فری
        if current_start < dur:
            with st.empty():
                for i in range(50, 0, -1):
                    st.warning(f"⏳ پاراستنی سێرڤەر لە بلۆکبوون: {i} چرکە چاوەڕێ دەکەین تا لیمیتی وشەکانی گووگڵ (Tokens) نوێ دەبێتەوە...")
                    time.sleep(1)

    if current_start >= dur - 2.0:
        bar.progress(1.0, "✅ وەرگێڕان ١٠٠٪ تەواو!")

    try:
        client.files.delete(name=uploaded.name)
    except Exception:
        pass

    return _dedup("\n".join(chunks))

# ══════════════════════════════════════════════════════════
#  FFMPEG
# ══════════════════════════════════════════════════════════
def _run(cmd: list):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-3000:])

def burn_subtitles(in_path: str, ass_path: str, out_path: str):
    vf = f"scale='min(1280,iw)':-2,ass={ass_path}:fontsdir=/tmp"
    _run([
        "ffmpeg", "-y",
        "-i", in_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "ultrafast",
        "-c:a", "aac",
        "-b:a", "128k",
        "-threads", "0",
        out_path,
    ])

def convert_video(in_path: str, out_path: str, codec_args: list):
    _run(["ffmpeg", "-y", "-i", in_path] + codec_args + [out_path])


# ══════════════════════════════════════════════════════════
#  STREAMLIT UI
# ══════════════════════════════════════════════════════════
def main():
    st.set_page_config(page_title="🎬 Subtitle Generator", page_icon="🎬", layout="centered")
    st.title("🎬 Kurdish ↔ English Subtitle Generator")

    tab_sub, tab_conv = st.tabs(["🎬 ژێرنووس", "🔄 گۆڕینی فۆرمات"])

    with tab_sub:
        api_key    = st.text_input("🔑 Gemini API Key", type="password", placeholder="AIza…")
        video_file = st.file_uploader("📁 ڤیدیۆ بار بکە (MP4/MOV)", type=["mp4", "mov"])

        st.markdown("---")
        direction_label = st.radio("🌐 ئاڕاستەی وەرگێڕان", ["بیانی / ئینگلیزی  →  کوردی سۆرانی", "کوردی  →  ئینگلیزی"], horizontal=True)
        dir_key = "ku→en" if "کوردی  →" in direction_label else "→ku"

        font_size = st.slider("📐 قەبارەی فۆنتی ژێرنووس", min_value=20, max_value=80, value=52)

        st.markdown("---")
        st.subheader("ℹ️ زانیاری ناساندنی دەستپێک")
        c1, c2 = st.columns(2)
        with c1:
            anime_name      = st.text_input("🎬 ناوی ئەنیمێ / زنجیرە", placeholder="Re:Zero")
            translator_name = st.text_input("✍️ ناوی وەرگێڕ",          placeholder="ئامر")
        with c2:
            season_ep  = st.text_input("📺 سیزن / ئەڵقە",   placeholder="وەرزی ١")
            tech_name  = st.text_input("💻 ناوی تەکنیک",     placeholder="ئامر")

        st.markdown("---")
        st.subheader("🎨 واتەرمارکی نووسین (لۆگۆ)")
        wc1, wc2, wc3, wc4 = st.columns(4)
        with wc1:
            wm_text = st.text_input("📝 نووسینی واتەرمارک", placeholder="وەرگێڕان: ئامر")
        with wc2:
            wm_color = st.color_picker("🎨 ڕەنگی نووسینەکە", value="#FFFFFF")
        with wc3:
            wm_font_size = st.slider("📏 قەبارە", min_value=10, max_value=150, value=30)
        with wc4:
            wm_pos = st.selectbox("📍 شوێن", ["چەپ", "ڕاست"])
            wm_alignment = 7 if wm_pos == "چەپ" else 9
        st.markdown("---")

        if "sub_temp_dir" not in st.session_state:
            st.session_state.sub_temp_dir   = None
            st.session_state.sub_input_path = None
            st.session_state.sub_raw        = None

        def _cleanup_sub_session():
            if st.session_state.sub_temp_dir and os.path.exists(st.session_state.sub_temp_dir):
                shutil.rmtree(st.session_state.sub_temp_dir, ignore_errors=True)
            st.session_state.sub_temp_dir   = None
            st.session_state.sub_input_path = None
            st.session_state.sub_raw        = None
            st.session_state.pop("sub_editor_box", None)

        col_run, col_resume, col_reset = st.columns([2, 2, 1])
        with col_run:
            start_clicked = st.button("🧠 ١. وەرگێڕانی نوێ", type="primary", use_container_width=True)
        with col_resume:
            resume_clicked = st.button("▶️ بەردەوام بوون", use_container_width=True, disabled=not st.session_state.sub_raw)
        with col_reset:
            reset_clicked = st.button("🔄 سفر", use_container_width=True, help="ڤیدیۆی بارکراو و وەرگێڕانی هەلگیراو پاک دەکاتەوە")

        if reset_clicked:
            _cleanup_sub_session()
            st.rerun()

        if start_clicked or resume_clicked:
            if not api_key.strip():
                st.error("❌ تکایە کلیلی Gemini بنووسە."); return
            
            if start_clicked:
                if not video_file:
                    st.error("❌ تکایە ڤیدیۆ بار بکە."); return
                _cleanup_sub_session()
                persist_dir = tempfile.mkdtemp(prefix="subgen_")
                in_p = os.path.join(persist_dir, "input.mp4")
                with open(in_p, "wb") as f:
                    f.write(video_file.read())
                st.session_state.sub_temp_dir = persist_dir
                st.session_state.sub_input_path = in_p
                existing_raw = ""
            else:
                in_p = st.session_state.sub_input_path
                existing_raw = st.session_state.get("sub_editor_box", st.session_state.sub_raw) or ""
                if not in_p or not os.path.exists(in_p):
                    st.error("❌ ڤیدیۆکە نەماوە، تکایە لە سەرەتاوە دەست پێ بکەوە.")
                    return

            raw = gemini_translate(api_key.strip(), in_p, dir_key, existing_raw)
            
            if raw and raw.strip():
                st.session_state.sub_raw = raw
            
            st.rerun()

        if st.session_state.sub_raw:
            preview_cues = parse_transcript(st.session_state.sub_raw)
            st.success(f"✅ {len(preview_cues)} ستەرە وەرگێڕدرا — پێداچوونەوەی بکە و ئەگەر پێویست بوو دەسکاری بکە.")
            
            st.markdown("### ⏰ ڕێکخستنی کاتی ژێرنووس (Subtitle Delay)")
            st.caption("ئەگەر نووسینەکە پێش دەنگ دەرکەوتووە، کاتەکە زیاد بکە (نموونە 1.5). ئەگەر درەنگ دەرکەوتووە، کەم بکەرەوە (نموونە -1.0).")
            
            col_delay_val, col_delay_btn = st.columns([2, 1])
            with col_delay_val:
                delay_val = st.number_input("⏱️ بڕی دواخستن یان پێشخستن (بە چرکە)", min_value=-30.0, max_value=30.0, value=0.0, step=0.5, format="%.1f", key="delay_val_input")
            with col_delay_btn:
                st.write("")
                apply_delay = st.button("🔄 گۆڕینی کاتەکان", use_container_width=True)
                
            if apply_delay and delay_val != 0.0:
                st.session_state.sub_raw = shift_transcript(st.session_state.sub_raw, delay_val)
                st.rerun()

            edited_raw = st.text_area("📝 ستەرەکان — پێش لکاندن دەسکاریان بکە", value=st.session_state.sub_raw, height=400, key="sub_editor_box")

            if st.button("🔥 ٢. ژێرنووس بخەرە سەر ڤیدیۆ", type="primary"):
                cues = parse_transcript(edited_raw)
                if not cues:
                    st.error("❌ ستەرەکان ناناسرێنەوە — فۆرماتی دەستکاریت بپشکنە."); return

                in_p = st.session_state.sub_input_path
                tmp  = st.session_state.sub_temp_dir
                if not in_p or not tmp or not os.path.exists(in_p):
                    st.error("❌ ڤیدیۆی ئۆریجیناڵ نەماوە — دووبارە بار بکە و وەرگێڕان دەستپێکە."); return

                ass_p = os.path.join(tmp, "subs.ass")
                out_p = os.path.join(tmp, "output.mp4")

                _, font_name = ensure_font(dir_key)

                intro = []
                if anime_name.strip():
                    txt = anime_name.strip()
                    if season_ep.strip():
                        txt += f"\\N({season_ep.strip()})"
                    intro.append({"start": "0:00:00.00", "end": "0:00:15.00", "style": "CornerStyle", "alignment_tag": "", "text": txt})
                if translator_name.strip():
                    intro.append({"start": "0:00:00.00", "end": "0:00:02.00", "style": "Default", "alignment_tag": "{\\an2}", "text": f"وەرگێڕان: {translator_name.strip()}"})
                if tech_name.strip():
                    intro.append({"start": "0:00:02.00", "end": "0:00:04.00", "style": "Default", "alignment_tag": "{\\an2}", "text": f"تەکنیک: {tech_name.strip()}"})

                for c in cues:
                    if secs(c["start"]) < 4.0:
                        c["alignment_tag"] = "{\\an8}"

                cues = intro + cues
                ass_txt = build_ass(cues, font_name, font_size, wm_text=wm_text.strip() if wm_text else "", wm_color=wm_color, wm_font_size=wm_font_size, wm_alignment=wm_alignment)
                
                with open(ass_p, "w", encoding="utf-8") as f:
                    f.write(ass_txt)

                st.info("🔥 خەریکی لکاندنی ژێرنووسە بە ڤیدیۆکەوە (FFmpeg)…")
                try:
                    burn_subtitles(in_p, ass_p, out_p)
                except RuntimeError as e:
                    st.error(f"❌ هەڵە لە FFmpeg:\n```\n{e}\n```"); return

                st.success("🎉 بە سەرکەوتوویی تەواو بوو!")
                with open(out_p, "rb") as f:
                    vb = f.read()

                auto_dl(vb, "subtitled.mp4", "video/mp4")
                st.download_button(label="⬇️ دابەزێنە — subtitled.mp4", data=vb, file_name="subtitled.mp4", mime="video/mp4", use_container_width=True)
                st.video(vb)

    with tab_conv:
        st.markdown("### 🔄 گۆڕینی فۆرماتی ڤیدیۆ")
        cf  = st.file_uploader("📁 ڤیدیۆ بار بکە", type=["mp4","mov","mkv","avi","webm","m4v","flv"], key="cv")
        fmt = st.selectbox("🎯 فۆرمات هەڵبژێرە", list(FORMAT_MAP.keys()))

        if st.button("⚡ گۆڕین", type="primary", key="cvbtn"):
            if not cf:
                st.error("تکایە ڤیدیۆ بار بکە."); return
            codec_args, mime, ext = FORMAT_MAP[fmt]
            with tempfile.TemporaryDirectory() as tmp:
                orig_ext = os.path.splitext(cf.name)[-1] or ".mp4"
                in_p  = os.path.join(tmp, f"input{orig_ext}")
                out_p = os.path.join(tmp, f"output{ext}")
                with open(in_p, "wb") as f:
                    f.write(cf.read())
                st.info("⚙️ خەریکی گۆڕینە…")
                try:
                    convert_video(in_p, out_p, codec_args)
                except RuntimeError as e:
                    st.error(f"❌ هەڵە لە FFmpeg:\n```\n{e}\n```"); return
                
                out_name = os.path.splitext(cf.name)[0] + ext
                with open(out_p, "rb") as f:
                    ob = f.read()
                st.success(f"✅ تەواو! ({len(ob)/1_048_576:.1f} MB)")
                auto_dl(ob, out_name, mime)
                st.download_button(label=f"⬇️ دابەزێنە — {out_name}", data=ob, file_name=out_name, mime=mime, use_container_width=True)

if __name__ == "__main__":
    main()

# ══════════════════════════════════════════════════════════
#  HOW TO UPLOAD THE FONT ON GITHUB MOBILE:
# ══════════════════════════════════════════════════════════
# ڕوونکردنەوەی بارکردنی فۆنتەکەت (Bahij Janna-Bold.ttf):
# دوای ئەوەی ئەم ٣ فایلەت دروستکرد، بگەڕێوە لاپەڕەی سەرەکی پڕۆژەکەت لە گیتھاب.
# لە سەرەوە کلیک لەسەر دوگمەی "Add file" بکە و پاشان "Upload files" هەڵبژێرە.
# فایلی فۆنتەکەت لە مۆبایلەکەتەوە بار بکە و سەیڤی بکە (Commit).
