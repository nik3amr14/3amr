"""
app.py v8.0 — Kurdish Sorani Subtitle Generator
Clean flat layout — no nested column widget issues
bashdar77 / nik3amr14
"""
import re, sys, json, uuid, time, shutil, tempfile, subprocess, threading
from pathlib import Path
from typing import Optional, Dict
import streamlit as st

_HERE = Path(__file__).parent.resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from faster_whisper import WhisperModel
from ai_translator import ai_translate, GEMINI_MODELS, THINKING_PRESETS

st.set_page_config(page_title="Kurdish Subtitle Generator", page_icon="🎬", layout="wide")

# ── Font discovery ─────────────────────────────────────────────────────────────
_FONT_CANDIDATES = {
    "Bahij Janna Bold": [
        _HERE / "Bahij Janna-Bold.ttf",
        _HERE / "Bahij_Janna-Bold.ttf",
        _HERE.parent / "Bahij Janna-Bold.ttf",
        Path("/app/Bahij Janna-Bold.ttf"),
        Path("/home/user/app/Bahij Janna-Bold.ttf"),
    ],
    "Xoshnus Abd Rojname": [
        _HERE / "Xoshnus - Abd Rojname Bold.ttf",
        _HERE / "Xoshnus_-_Abd_Rojname_Bold.ttf",
        _HERE.parent / "Xoshnus - Abd Rojname Bold.ttf",
        Path("/app/Xoshnus - Abd Rojname Bold.ttf"),
        Path("/home/user/app/Xoshnus - Abd Rojname Bold.ttf"),
    ],
}

AVAILABLE_FONTS: Dict[str, Path] = {}
for _fn, _paths in _FONT_CANDIDATES.items():
    _found = next((p for p in _paths if p.exists()), None)
    if _found:
        AVAILABLE_FONTS[_fn] = _found

_FONT_FAMILY_MAP = {
    "Bahij Janna Bold":    "Bahij Janna",
    "Xoshnus Abd Rojname": "Xoshnus - Abd Rojname",
}

# ── Temp dirs ──────────────────────────────────────────────────────────────────
TEMP_DIR  = Path(tempfile.gettempdir()) / "kurdish_subs"
FONTS_DIR = TEMP_DIR / "fonts"
TEMP_DIR.mkdir(parents=True, exist_ok=True)
FONTS_DIR.mkdir(parents=True, exist_ok=True)

for _fn, _fpath in AVAILABLE_FONTS.items():
    _dest = FONTS_DIR / _fpath.name
    if not _dest.exists():
        shutil.copy2(_fpath, _dest)

# ── CSS ────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
:root { --void:#010007; --crim:#7a0000; --crim-h:#a81200; }
.stApp { background: var(--void); }
h1,h2,h3,h4 { color: #d0c8ff !important; }
.stButton>button[kind="primary"] {
    background: linear-gradient(135deg,var(--crim),var(--crim-h));
    border:none; color:#fff; font-weight:bold; border-radius:8px;
}
</style>
""", unsafe_allow_html=True)

# ── Whisper singleton ──────────────────────────────────────────────────────────
_wm_lock = threading.Lock()
_wm: Optional[WhisperModel] = None

def _get_wm() -> WhisperModel:
    global _wm
    if _wm is None:
        with _wm_lock:
            if _wm is None:
                _wm = WhisperModel("large-v3-turbo", device="auto", compute_type="int8")
    return _wm

_workers: Dict[str, dict] = {}

LANGS = {
    "Auto-Detect (خۆکار)": None,
    "Japanese (ژاپۆنی)":   "ja",
    "English (ئینگلیزی)":  "en",
    "Persian (فارسی)":     "fa",
    "Arabic (عەرەبی)":     "ar",
    "Spanish (ئیسپانیایی)":"es",
    "Hindi (هیندی)":       "hi",
    "Russian (ڕووسی)":     "ru",
    "Chinese (چینی)":      "zh",
    "German (ئەلمانی)":    "de",
    "Italian (ئیتالی)":    "it",
    "Korean (کۆریایی)":    "ko",
    "French (فرەنسی)":     "fr",
    "Turkish (تورکی)":     "tr",
    "Portuguese (پورتوگالی)":"pt",
}

_MUSIC = set("♪♫♩♬")
def _is_song(t): return any(c in t for c in _MUSIC)

def _hex_to_ass(h):
    h = h.lstrip("#"); r,g,b = int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
    return f"&H00{b:02X}{g:02X}{r:02X}"

_NL = chr(92)+"N"

def _ts(sec):
    sec = max(0.0,sec)
    h,m,s = int(sec//3600),int((sec%3600)//60),int(sec%60)
    return f"{h}:{m:02}:{s:02}.{int(round((sec-int(sec))*100)):02}"

def _ffmpeg(cmd, err_fn=None, label=""):
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=7200)
        if r.returncode != 0:
            err = r.stderr.decode(errors="replace")[-800:]
            if err_fn: err_fn(f"❌ FFmpeg ({label}): {err}")
            return False
        return True
    except subprocess.TimeoutExpired:
        if err_fn: err_fn(f"⏰ Timeout ({label})")
        return False
    except FileNotFoundError:
        if err_fn: err_fn("❌ FFmpeg نەدۆزرایەوە")
        return False
    except Exception as e:
        if err_fn: err_fn(f"❌ {e}")
        return False

def _extract_audio(video_path, sid, err_fn=None):
    out = TEMP_DIR / f"{sid}_audio.wav"
    ok = _ffmpeg(["ffmpeg","-y","-i",str(video_path),"-vn",
                  "-af","dynaudnorm=f=150:g=15:r=0.9",
                  "-acodec","pcm_s16le","-ar","16000","-ac","1",str(out)],
                 err_fn, "audio")
    if ok: return out
    raise RuntimeError("دەرکردنی دەنگ سەرنەکەوت.")

def _segs_to_rows(segs):
    rows = []
    for seg in segs:
        txt = seg.text.strip()
        if not txt: continue
        s = round((seg.words[0].start if seg.words else seg.start), 3)
        e = round((seg.words[-1].end  if seg.words else seg.end) - 0.10, 3)
        if e-s < 0.35: e = s+0.35
        rows.append({"start":s,"end":e,"text":txt})
    for i in range(len(rows)-1):
        if rows[i]["end"] >= rows[i+1]["start"]-0.03:
            rows[i]["end"] = rows[i+1]["start"]-0.04
    return rows

def _transcribe(audio_path, forced_lang, progress_fn=None):
    model = _get_wm()
    lang  = forced_lang
    if not forced_lang:
        segs,info = model.transcribe(str(audio_path), beam_size=1)
        list(segs); lang = info.language
        if progress_fn: progress_fn(f"🌐 زمان: {info.language}")
    segs,_ = model.transcribe(str(audio_path), language=lang, vad_filter=True,
                               vad_parameters={"min_silence_duration_ms":200},
                               no_speech_threshold=0.18, word_timestamps=True, beam_size=5)
    rows = _segs_to_rows(segs)
    if not rows:
        if progress_fn: progress_fn("⚠️ VAD بەبێ فیلتەر...")
        segs,_ = model.transcribe(str(audio_path), language=lang, vad_filter=False,
                                   no_speech_threshold=0.55, word_timestamps=True, beam_size=5)
        rows = _segs_to_rows(segs)
    return rows

def _mark_opening_ending(rows):
    if not rows: return rows
    i = 0
    while i < len(rows) and _is_song(rows[i]["text"]):
        rows[i]["skip_oe"] = True; i += 1
    j = len(rows)-1
    while j >= 0 and _is_song(rows[j]["text"]):
        rows[j]["skip_oe"] = True; j -= 1
    return rows

def _build_chunks(segs, max_secs):
    if not segs: return []
    chunks,cur,t0 = [],[],segs[0]["start"]
    for i,seg in enumerate(segs):
        cur.append(seg)
        if seg["end"]-t0 >= max_secs:
            chunks.append(cur); cur = []
            if i+1 < len(segs): t0 = segs[i+1]["start"]
    if cur: chunks.append(cur)
    return chunks

def _build_ass(trs, delay, font_sz, font_name,
               cr_font, cr_col_hex, anime, xltr, xltr_col_hex,
               season, tech, tech_col_hex,
               cr_secs, wm_text, wm_sz, wm_col_hex, wm_al, song_col_hex):
    fn = font_name or "Noto Naskh Arabic"
    sc = _hex_to_ass(song_col_hex); wc = _hex_to_ass(wm_col_hex)
    cc = _hex_to_ass(cr_col_hex);   xc = _hex_to_ass(xltr_col_hex)
    tc = _hex_to_ass(tech_col_hex)
    h  = "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
    h += "ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
    h += "[V4+ Styles]\n"
    h += "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    h += f"Style: Kurdish,{fn},{font_sz},&H00FFFFFF,&H000000FF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,2.5,1.2,2,30,30,28,1\n"
    h += f"Style: Song,{fn},{font_sz},{sc},&H000000FF,&H00000000,&H50000000,-1,1,0,0,100,100,0,0,1,2.5,1.2,2,30,30,28,1\n"
    h += f"Style: Credit,{fn},{cr_font},{cc},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,1.0,7,30,30,28,1\n"
    h += f"Style: Xltr,{fn},{cr_font},{xc},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,1.0,7,30,30,28,1\n"
    h += f"Style: Tech,{fn},{cr_font},{tc},&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.0,1.0,7,30,30,28,1\n"
    h += f"Style: Watermark,{fn},{wm_sz},{wc},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,{wm_al},30,30,28,1\n"
    h += "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    evts = []
    ce = _ts(1.5+cr_secs)
    if anime:  evts.append(f"Dialogue: 1,0:00:01.50,{ce},Credit,,0,0,0,,{anime}")
    if xltr:   evts.append(f"Dialogue: 1,0:00:01.50,{ce},Xltr,,0,0,0,,وەرگێر: {xltr}")
    if season: evts.append(f"Dialogue: 1,0:00:01.50,{ce},Credit,,0,0,0,,{season}")
    if tech:   evts.append(f"Dialogue: 1,0:00:01.50,{ce},Tech,,0,0,0,,{tech}")
    if wm_text.strip():
        last = (trs[-1].get("end",0)+delay) if trs else 3600.0
        evts.append(f"Dialogue: 0,0:00:00.00,{_ts(last)},Watermark,,0,0,0,,{wm_text}")
    for row in trs:
        s = row.get("start",0.0)+delay; e = row.get("end",0.0)+delay
        t = row.get("text","").strip()
        if not t or s >= e: continue
        style = "Song" if _is_song(t) else "Kurdish"
        evts.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},{style},,0,0,0,,{t}")
    return h+"\n".join(evts)

def _burn(video, ass, out, err_fn=None):
    ass_str = str(ass).replace("\\","/").replace(":","\\:")
    af = f"ass={ass_str}"
    if FONTS_DIR.exists():
        fd = str(FONTS_DIR).replace("\\","/").replace(":","\\:")
        af += f":fontsdir={fd}"
    return _ffmpeg(["ffmpeg","-y","-i",str(video),"-vf",af,
                    "-c:v","libx264","-preset","veryfast","-crf","23",
                    "-c:a","aac","-b:a","192k","-movflags","+faststart",str(out)],
                   err_fn, "burn")

# ══ BACKGROUND WORKER ════════════════════════════════════════════════════════
def _bg_worker(p, video_bytes, cfg):
    w = _workers[p]; sid = cfg["sid"]
    def _prog(msg): w["status"] = msg
    try:
        vid = TEMP_DIR / f"{sid}_input.mp4"
        with open(vid,"wb") as f: f.write(video_bytes)
        _prog("🎵 دەرهێنانی دەنگ...")
        audio = _extract_audio(vid, sid)
        _prog("🎙️ Whisper large-v3-turbo...")
        rows = _transcribe(audio, cfg["lang"], _prog)
        if not rows:
            w["error"] = "⚠️ هیچ دەنگێک نەدۆزرایەوە."
            w["done"] = True; return
        rows = _mark_opening_ending(rows)
        rows_to_translate = [r for r in rows if not r.get("skip_oe")]
        chunks = _build_chunks(rows_to_translate, cfg["chunk_secs"])
        total = len(chunks); trans = []; key_idx = 0
        for i,chunk in enumerate(chunks):
            _prog(f"🔄 گەیاندن {i+1} لە {total} ({int(i/total*100)}٪)")
            w["pct"] = int(i/total*100)
            to_ai = [s for s in chunk if not (_is_song(s["text"]) and not cfg["do_songs"])]
            pt    = [{"start":s["start"],"end":s["end"],"text":s["text"]}
                     for s in chunk if _is_song(s["text"]) and not cfg["do_songs"]]
            ai_rows = []
            if to_ai:
                ai_rows,key_idx = ai_translate(
                    gemini_keys=cfg["keys"], cur_gem_idx=key_idx,
                    transcript_chunk=to_ai, thinking_budget=cfg["budget"],
                    selected_model=cfg["model"], status_msg=None)
            trans.extend(sorted(ai_rows+pt, key=lambda x: x["start"]))
        w["translations"] = trans; w["translation_done"] = True
        w["pct"] = 100
        w["status"] = "✅ تەواو بوو — ژێرنووسەکان دەستکاری بکە پاشان داگیران بکە"
        w["done"] = True
    except Exception as e:
        w["error"] = str(e); w["done"] = True

# ══ RENDER ONE APP INSTANCE ═══════════════════════════════════════════════════
def render_app(p: str):
    # ── Session state defaults ────────────────────────────────────────────────
    st.session_state.setdefault(f"{p}_sid",         str(uuid.uuid4())[:8])
    st.session_state.setdefault(f"{p}_burned",       False)
    st.session_state.setdefault(f"{p}_out_path",     None)
    st.session_state.setdefault(f"{p}_edits",        None)
    st.session_state.setdefault(f"{p}_video_bytes",  None)

    sid = st.session_state[f"{p}_sid"]
    w   = _workers.get(p, {})

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 1 — FILE UPLOAD  (top, full width, always visible)
    # ════════════════════════════════════════════════════════════════════════
    uploaded = st.file_uploader(
        "📁 ڤیدیۆی خۆت بخەرە",
        type=["mp4","mkv","avi","mov","webm","m4v","flv","ts","wmv"],
        key=f"{p}_file"
    )
    if uploaded is not None:
        st.session_state[f"{p}_video_bytes"] = uploaded.read()
    video_bytes = st.session_state[f"{p}_video_bytes"]
    if video_bytes:
        sz = len(video_bytes)/1024/1024
        st.caption(f"✅ ڤیدیۆ بارکراوە — {sz:.1f} MB")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 2 — API KEYS  (top, full width, important)
    # ════════════════════════════════════════════════════════════════════════
    st.markdown("#### 🔑 کلیلەکانی Gemini API")
    k_cols = st.columns(4)
    gkeys = []
    for i,col in enumerate(k_cols):
        k = col.text_input(f"کلیل {i+1}", type="password", key=f"{p}_k{i+1}", label_visibility="collapsed", placeholder=f"کلیل {i+1}")
        if k.strip(): gkeys.append(k)

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 3 — MODEL + LANGUAGE  (two columns)
    # ════════════════════════════════════════════════════════════════════════
    col_m, col_l, col_t = st.columns(3)
    with col_m:
        st.caption("مۆدێل")
        model = st.selectbox("", GEMINI_MODELS, key=f"{p}_model", label_visibility="collapsed")
    with col_l:
        st.caption("زمانی ڤیدیۆ")
        lc = st.selectbox("", list(LANGS.keys()), key=f"{p}_lang", label_visibility="collapsed")
        whisper_lang = LANGS[lc]
    with col_t:
        st.caption("Thinking")
        tl     = st.selectbox("", list(THINKING_PRESETS.keys()), index=1,
                               key=f"{p}_think", label_visibility="collapsed")
        budget = THINKING_PRESETS[tl]

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 4 — FONT + SIZES
    # ════════════════════════════════════════════════════════════════════════
    col_f, col_fs, col_ch = st.columns(3)
    with col_f:
        st.caption("فۆنت")
        font_opts = list(AVAILABLE_FONTS.keys()) if AVAILABLE_FONTS else ["Noto Naskh Arabic"]
        chosen_font = st.selectbox("", font_opts, key=f"{p}_font_choice", label_visibility="collapsed")
        ass_font = _FONT_FAMILY_MAP.get(chosen_font, chosen_font)
    with col_fs:
        st.caption("قەبارەی فۆنت")
        font_sz = st.slider("", 20, 80, 54, key=f"{p}_font", label_visibility="collapsed")
    with col_ch:
        st.caption("بەرگە (خولەک)")
        chunk_secs = st.slider("", 3, 15, 6, key=f"{p}_chunk", label_visibility="collapsed") * 60

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 5 — SONG + DELAY
    # ════════════════════════════════════════════════════════════════════════
    col_s1, col_s2, col_s3 = st.columns(3)
    with col_s1:
        st.caption("گۆرانی ناو ڤیدیۆ")
        do_songs   = st.toggle("وەرگێرانی گۆرانی", value=False, key=f"{p}_songs")
        song_color = st.color_picker("رەنگی گۆرانی", "#FFD700", key=f"{p}_scolor")
    with col_s2:
        st.caption("دواخستن (چرکە)")
        sub_delay = st.slider("", -15.0, 15.0, 0.0, 0.1, key=f"{p}_delay", label_visibility="collapsed")
    with col_s3:
        st.caption(f"v8.0 — {p.upper()}")
        if AVAILABLE_FONTS:
            for fn in AVAILABLE_FONTS: st.caption(f"✅ {fn}")
        else:
            st.caption("⚠️ Noto بەکار دەهێنرێت")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 6 — CREDITS (expander)
    # ════════════════════════════════════════════════════════════════════════
    with st.expander("ℹ️ کرێدیت", expanded=False):
        cr1, cr2 = st.columns(2)
        with cr1:
            anime_name = st.text_input("🎬 ناوی فیلم", "",          key=f"{p}_anime")
            xltr       = st.text_input("✍️ وەرگێر",   "",          key=f"{p}_xltr")
        with cr2:
            season_ep  = st.text_input("📺 سیزن/ئەڵقە", "",        key=f"{p}_season")
            tech_line  = st.text_input("⚙️ تەکنیک", "Kurdish AI",  key=f"{p}_tech")
        cc1, cc2, cc3 = st.columns(3)
        with cc1: cr_col   = st.color_picker("رەنگی کرێدیت", "#FFD700", key=f"{p}_cr_col")
        with cc2: xltr_col = st.color_picker("رەنگی وەرگێر", "#00CFFF", key=f"{p}_xltr_col")
        with cc3: tech_col = st.color_picker("رەنگی تەکنیک", "#AAFFAA", key=f"{p}_tech_col")
        crs1, crs2 = st.columns(2)
        with crs1: cr_secs = st.number_input("⏱️ کات", 0.5, 30.0, 4.0, 0.5, key=f"{p}_crsec")
        with crs2: cr_font = st.number_input("📐 قەبارەی کرێدیت", 14, 48, 24, 2, key=f"{p}_crfont")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 7 — WATERMARK (expander)
    # ════════════════════════════════════════════════════════════════════════
    with st.expander("🔴 لۆگۆ", expanded=False):
        wm_text = st.text_input("دەقی لۆگۆ", "", key=f"{p}_wm")
        wa1,wa2,wa3 = st.columns(3)
        with wa1: wm_sz  = st.number_input("قەبارە", 12, 72, 24, 2, key=f"{p}_wmsize")
        with wa2: wm_col = st.color_picker("رەنگ", "#FFFFFF", key=f"{p}_wmcol")
        with wa3: wm_pos = st.radio("ئەلا", ["چەپ","ناوەڕاست","ڕاست"], index=2,
                                     key=f"{p}_wmpos", horizontal=True)
        wm_al = {"چەپ":7,"ناوەڕاست":8,"ڕاست":9}[wm_pos]

    st.divider()

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 8 — ACTION BUTTONS
    # ════════════════════════════════════════════════════════════════════════
    running = bool(w and not w.get("done", True))
    b1,b2,b3 = st.columns(3)

    btn_start  = b1.button("▶️ دەست پێبکە",  key=f"{p}_start",
                            use_container_width=True, type="primary",
                            disabled=running)
    btn_resume = b2.button("⏭️ Resume",       key=f"{p}_resume",
                            use_container_width=True,
                            disabled=running or not bool(w.get("translation_done")))
    btn_reset  = b3.button("🔄 ڕیسێت",        key=f"{p}_reset",
                            use_container_width=True, disabled=running)

    if btn_reset:
        _workers.pop(p, None)
        for k in [f"{p}_sid",f"{p}_burned",f"{p}_out_path",
                  f"{p}_edits",f"{p}_video_bytes"]:
            st.session_state.pop(k, None)
        st.rerun()

    if btn_start:
        if not video_bytes:
            st.warning("⚠️ ڤیدیۆیەک بخەرە.")
            st.stop()
        if not gkeys:
            st.warning("⚠️ لانیکەم یەک کلیل Gemini بخەرە.")
            st.stop()
        _workers[p] = {"pct":0,"status":"دەستپێکردن...","done":False,
                       "error":None,"translations":[],"translation_done":False}
        cfg = {"keys":gkeys,"model":model,"budget":budget,"lang":whisper_lang,
               "chunk_secs":chunk_secs,"do_songs":do_songs,"sid":sid}
        t = threading.Thread(target=_bg_worker, args=(p,video_bytes,cfg), daemon=True)
        t.start()
        _workers[p]["thread"] = t
        st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 9 — PROGRESS
    # ════════════════════════════════════════════════════════════════════════
    if w:
        if w.get("error"):
            st.error(w["error"])
        elif not w.get("done"):
            st.progress(w.get("pct",0)/100)
            st.info(w.get("status","..."))
        elif w.get("done") and not w.get("error"):
            st.success(w.get("status","✅ تەواو بوو"))

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 10 — SUBTITLE EDITOR
    # ════════════════════════════════════════════════════════════════════════
    trs = w.get("translations") if w else None
    if trs:
        st.divider()
        st.subheader("✏️ دەستکاریکردنی ژێرنووس")
        if st.session_state[f"{p}_edits"] is None:
            st.session_state[f"{p}_edits"] = "\n".join(
                f'{r.get("start",0):.2f} --> {r.get("end",0):.2f} | {r.get("text","")}'
                for r in trs)
        edited = st.text_area("", value=st.session_state[f"{p}_edits"],
                              height=300, key=f"{p}_editor", label_visibility="collapsed")
        if st.button("💾 پاشەکەوت", key=f"{p}_save", use_container_width=True):
            upd = []
            for line in edited.splitlines():
                m = re.match(r"(\d+\.?\d*)\s*-->\s*(\d+\.?\d*)\s*\|(.*)", line.strip())
                if m:
                    upd.append({"start":float(m.group(1)),"end":float(m.group(2)),
                                "text":m.group(3).strip()})
            if upd:
                _workers[p]["translations"] = upd
                st.session_state[f"{p}_edits"] = edited
                st.success("✅ پاشەکەوتکرا.")
            else:
                st.error("❌ فۆرمات هەڵەیە.")

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 11 — BURN BUTTON
    # ════════════════════════════════════════════════════════════════════════
    if w and w.get("translation_done") and w.get("translations"):
        st.divider()
        if st.button("🔥 ڤیدیۆ ناو بۆ داگیراندن", key=f"{p}_burn",
                     use_container_width=True, type="primary"):
            if not video_bytes:
                st.warning("⚠️ ڤیدیۆکە دووبارە بخەرە.")
                st.stop()
            vid = TEMP_DIR / f"{sid}_input.mp4"
            if not vid.exists():
                with open(vid,"wb") as f: f.write(video_bytes)
            ass_str = _build_ass(
                trs=w["translations"], delay=sub_delay, font_sz=font_sz,
                font_name=ass_font, cr_font=cr_font, cr_col_hex=cr_col,
                anime=anime_name, xltr=xltr, xltr_col_hex=xltr_col,
                season=season_ep, tech=tech_line, tech_col_hex=tech_col,
                cr_secs=cr_secs, wm_text=wm_text, wm_sz=wm_sz,
                wm_col_hex=wm_col, wm_al=wm_al, song_col_hex=song_color)
            ass_p = TEMP_DIR / f"{sid}_subs.ass"
            ass_p.write_text(ass_str, encoding="utf-8")
            out_p = TEMP_DIR / f"{sid}_subtitled.mp4"
            burn_errors = []
            with st.spinner("🔥 داگیراندن — veryfast..."):
                ok = _burn(vid, ass_p, out_p, lambda e: burn_errors.append(e))
            for e in burn_errors: st.error(e)
            if ok:
                st.session_state[f"{p}_out_path"] = str(out_p)
                st.session_state[f"{p}_burned"]   = True
                st.success("🎉 ئامادەی دانلۆد!")
                st.rerun()

    # ════════════════════════════════════════════════════════════════════════
    # SECTION 12 — DOWNLOAD
    # ════════════════════════════════════════════════════════════════════════
    op = st.session_state.get(f"{p}_out_path")
    if op:
        out_p = Path(op)
        if out_p.exists():
            st.divider()
            mb = out_p.stat().st_size/1024/1024
            st.caption(f"📦 {mb:.1f} MB")
            with open(out_p,"rb") as fh:
                st.download_button("⬇️ دانلۆدی ڤیدیۆ (subtitled.mp4)",
                                   data=fh, file_name="subtitled.mp4",
                                   mime="video/mp4", use_container_width=True,
                                   type="primary", key=f"{p}_dl")
            if mb < 150: st.video(str(out_p))
            else: st.info("ℹ️ گەورەیە — دانلۆد بکە.")

# ══ MAIN ══════════════════════════════════════════════════════════════════════
if "wm_loaded" not in st.session_state:
    with st.spinner("🎙️ Whisper large-v3-turbo بارکردن..."):
        _get_wm()
    st.session_state["wm_loaded"] = True

st.title("🎬 دروستکردنی ژێرنووسی کوردی سۆرانی")
st.caption("وەک ئەوەی کات بوەستێت — جیهان هەمووی بچووک دەبێت — و تۆ دەبیتە هەموو جیهانم")
st.divider()

tab1, tab2 = st.tabs(["🎬 ڤیدیۆی یەکەم", "🎬 ڤیدیۆی دووەم"])
with tab1: render_app("t1")
with tab2: render_app("t2")

if any(not _workers.get(p,{}).get("done",True) for p in ["t1","t2"]):
    time.sleep(2)
    st.rerun()
