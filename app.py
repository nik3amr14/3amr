"""
app.py  v5.0  — Kurdish Sorani Cinematic Subtitle Generator
Two independent apps in one | Gemini 6 Models | Bahij Janna Bold
bashdar77 / nik3amr14
"""
import os,re,sys,json,uuid,time,shutil,tempfile,subprocess
from pathlib import Path
from typing import Optional
import streamlit as st

_HERE=Path(__file__).parent.resolve()
if str(_HERE) not in sys.path: sys.path.insert(0,str(_HERE))
from faster_whisper import WhisperModel
from ai_translator import ai_translate,GEMINI_MODELS,THINKING_PRESETS

st.set_page_config(page_title="🎬 Kurdish Subtitle Generator",page_icon="🎬",layout="wide")

# ── Font detection ────────────────────────────────────────────────────────────
_FC=[_HERE/"Bahij Janna-Bold.ttf",_HERE/"Bahij_Janna-Bold_.ttf",
     _HERE.parent/"Bahij Janna-Bold.ttf",_HERE.parent/"Bahij_Janna-Bold_.ttf",
     Path("/app/Bahij Janna-Bold.ttf"),Path("/home/user/app/Bahij Janna-Bold.ttf")]
FONT_PATH:Optional[Path]=next((p for p in _FC if p.exists()),None)

TEMP_DIR=Path(tempfile.gettempdir())/"kurdish_subs"
FONTS_DIR=TEMP_DIR/"fonts"
TEMP_DIR.mkdir(parents=True,exist_ok=True)
FONTS_DIR.mkdir(parents=True,exist_ok=True)
_FDEST=FONTS_DIR/"BahijJanna-Bold.ttf"
if FONT_PATH and FONT_PATH.exists() and not _FDEST.exists():
    shutil.copy2(FONT_PATH,_FDEST)

# ── CSS ───────────────────────────────────────────────────────────────────────
_CSS='<style>\n:root{--void:#010007;--crim:#7a0000;--crim-h:#a81200;--sil:#7070a0;\n       --gc:rgba(110,0,0,.22);--gp:rgba(50,10,90,.18);}\n.stApp{background-color:var(--void)!important;\n    background-image:linear-gradient(to right,rgba(2,0,10,.50) 0%,rgba(4,0,14,.15) 48%,rgba(2,0,10,.48) 100%),\n        url("data:image/jpeg;base64,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");\n    background-size:cover;background-position:center top;background-attachment:fixed;}\n.stApp::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;\n    background-image:url("data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%20width%3D%22100%22%20height%3D%22130%22%3E%3Cpath%20d%3D%22M14%2C130%20L14%2C65%20Q14%2C14%2050%2C3%20Q86%2C14%2086%2C65%20L86%2C130%22%20fill%3D%22none%22%20stroke%3D%22rgba%28100%2C0%2C0%2C0.05%29%22%20stroke-width%3D%221.3%22%2F%3E%3Cline%20x1%3D%2250%22%20y1%3D%223%22%20x2%3D%2250%22%20y2%3D%22130%22%20stroke%3D%22rgba%2880%2C0%2C0%2C0.028%29%22%20stroke-width%3D%220.6%22%2F%3E%3Cline%20x1%3D%2214%22%20y1%3D%2297%22%20x2%3D%2286%22%20y2%3D%2297%22%20stroke%3D%22rgba%2880%2C0%2C0%2C0.028%29%22%20stroke-width%3D%220.6%22%2F%3E%3C%2Fsvg%3E");background-size:100px 130px;}\n@keyframes pulse{0%,100%{opacity:1}40%{opacity:.96}}\n.stApp::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;\n    background:radial-gradient(ellipse at 30% 100%,rgba(120,20,0,.08) 0%,transparent 55%);\n    animation:pulse 5s ease-in-out infinite;}\nsection[data-testid="stSidebar"]{\n    background:linear-gradient(180deg,rgba(3,0,15,.92) 0%,rgba(1,0,8,.92) 100%)!important;\n    border-right:1px solid #1a0830!important;backdrop-filter:blur(6px);}\nsection[data-testid="stSidebar"] *{color:#ccc8ec!important;}\n.main .block-container{padding-top:1.2rem!important;max-width:1200px!important;}\nh1{color:#f0eeff!important;font-weight:900!important;\n    text-shadow:0 0 28px rgba(130,0,0,.5),0 2px 6px rgba(0,0,0,.9)!important;}\nh2,h3{color:#b8b0dc!important;font-weight:700!important;}\n.stButton>button{background:linear-gradient(135deg,#0a0520,#12082c)!important;\n    color:#c0b8ec!important;border:1px solid #200840!important;border-radius:7px!important;\n    font-weight:700!important;transition:all .18s!important;}\n.stButton>button:hover{background:linear-gradient(135deg,#160830,#200a40)!important;\n    border-color:#5820a0!important;color:#fff!important;box-shadow:0 0 18px var(--gp)!important;}\nbutton[data-testid="stBaseButton-primary"]{\n    background:linear-gradient(135deg,#5c0000,#7a0000)!important;\n    border-color:#bb1200!important;color:#fff!important;}\nbutton[data-testid="stBaseButton-primary"]:hover{\n    background:linear-gradient(135deg,#7a0000,#9a0000)!important;box-shadow:0 0 28px var(--gc)!important;}\n.stTextInput input,.stTextArea textarea,.stNumberInput input{\n    background:rgba(7,3,24,.88)!important;color:#d4d0f6!important;\n    border:1px solid #1c0c38!important;border-radius:7px!important;}\n.stTextInput input:focus,.stTextArea textarea:focus,.stNumberInput input:focus{\n    border-color:#5820a0!important;box-shadow:0 0 8px var(--gp)!important;}\n.stSelectbox>div>div{background:rgba(7,3,24,.88)!important;border:1px solid #1c0c38!important;\n    border-radius:7px!important;color:#d4d0f6!important;}\n.stSlider .st-fy{background:var(--crim)!important;}\n.stProgress>div>div>div>div{background:linear-gradient(90deg,#5c0000,var(--crim-h))!important;border-radius:4px!important;}\n[data-testid="stFileUploaderDropzone"]{background:rgba(5,2,24,.82)!important;\n    border:2px dashed #1c0c38!important;border-radius:10px!important;}\n[data-testid="stAlert"]{background:rgba(7,3,24,.88)!important;border:1px solid #1c0c38!important;border-radius:8px!important;}\n[data-testid="stExpander"] summary{background:rgba(7,3,24,.88)!important;border:1px solid #1c0c38!important;\n    border-radius:8px!important;color:#c0b8e0!important;}\n[data-testid="stWidgetLabel"] p,.stTextInput label,.stSelectbox label,\n.stSlider label,.stNumberInput label,.stRadio label,.stTextArea label{color:#8878c0!important;font-weight:600!important;}\nsmall,.stCaption{color:var(--sil)!important;}\nhr{border-color:#120622!important;}\n.stTabs [data-baseweb="tab-list"]{background:rgba(5,2,18,.90)!important;border-radius:10px!important;}\n.stTabs [data-baseweb="tab"]{color:#9888d0!important;font-weight:700!important;}\n.stTabs [aria-selected="true"]{color:#fff!important;background:rgba(90,10,120,.40)!important;border-radius:8px!important;}\n.stTextArea textarea{direction:rtl;text-align:right;font-size:.94rem;}\n</style>'
st.markdown(_CSS,unsafe_allow_html=True)

# ── Language list ─────────────────────────────────────────────────────────────
LANGS={"Auto-Detect (خۆکار)":None,"Japanese (ژاپۆنی)":"ja",
       "English (ئینگلیزی)":"en","Persian (فارسی)":"fa","Arabic (عەرەبی)":"ar",
       "Spanish (ئیسپانیایی)":"es","Hindi (هیندی)":"hi","Russian (ڕووسی)":"ru",
       "Chinese (چینی)":"zh","German (ئەڵمانی)":"de","Italian (ئیتالی)":"it",
       "Korean (کۆریایی)":"ko","French (فرەنسی)":"fr","Turkish (تورکی)":"tr",
       "Portuguese (پورتوگالی)":"pt"}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _hex_to_ass(h):
    h=h.lstrip("#"); r,g,b=int(h[0:2],16),int(h[2:4],16),int(h[4:6],16)
    return f"&H00{b:02X}{g:02X}{r:02X}"

_MUSIC=set("♪♫🎵🎶♩♬")
def _is_song(t): return any(c in t for c in _MUSIC)

_NL=chr(92)+"N"
_KU=str.maketrans("0123456789","٠١٢٣٤٥٦٧٨٩")
def _ku(n): return str(n).translate(_KU)

def _ts(sec):
    sec=max(0.0,sec)
    h,m,s=int(sec//3600),int((sec%3600)//60),int(sec%60)
    return f"{h}:{m:02}:{s:02}.{int(round((sec-int(sec))*100)):02}"

# ── FFmpeg ────────────────────────────────────────────────────────────────────
def _ffmpeg(cmd,label=""):
    try:
        r=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=7200)
        if r.returncode!=0:
            err=r.stderr.decode(errors="replace")[-900:]
            st.error(f"❌ FFmpeg ({label}):\n{err}")
            return False
        return True
    except subprocess.TimeoutExpired: st.error(f"⏱️ Timeout ({label})"); return False
    except FileNotFoundError: st.error("❌ FFmpeg نەدۆزرایەوە — packages.txt: ffmpeg"); return False
    except Exception as e: st.error(f"❌ {e}"); return False

# ── Audio ─────────────────────────────────────────────────────────────────────
def extract_audio(video,sid):
    out=TEMP_DIR/f"{sid}_audio.wav"
    if _ffmpeg(["ffmpeg","-y","-i",str(video),"-vn",
               "-af","dynaudnorm=f=150:g=15:r=0.9",
               "-acodec","pcm_s16le","-ar","16000","-ac","1",str(out)],"audio"):
        return out
    raise RuntimeError("دەرکردنی دەنگ سەرنەکەوت.")

# ── Whisper ───────────────────────────────────────────────────────────────────
@st.cache_resource(show_spinner="🔊 Whisper large-v3-turbo بارکردن...")
def _load_whisper(): return WhisperModel("large-v3-turbo",device="auto",compute_type="int8")

def _segs_to_rows(segs):
    rows=[]
    for seg in segs:
        txt=seg.text.strip()
        if not txt: continue
        if seg.words:
            s=round(seg.words[0].start,3)
            e=round(seg.words[-1].end,3)
        else:
            s=round(seg.start,3); e=round(seg.end,3)
        # trim trailing silence precisely
        e=round(e-0.10,3)
        if e-s<0.35: e=s+0.35
        rows.append({"start":s,"end":e,"text":txt})
    # remove overlaps
    for i in range(len(rows)-1):
        if rows[i]["end"]>=rows[i+1]["start"]-0.03:
            rows[i]["end"]=rows[i+1]["start"]-0.04
    return rows

def transcribe_audio(audio,forced_lang):
    model=_load_whisper(); lang=forced_lang
    if not forced_lang:
        segs,info=model.transcribe(str(audio),beam_size=1)
        list(segs); lang=info.language
        st.info(f"🌐 دۆزرایەوە: **{info.language}** ({info.language_probability:.1%})")
    segs,_=model.transcribe(str(audio),language=lang,vad_filter=True,
        vad_parameters={"min_silence_duration_ms":200},
        no_speech_threshold=0.18,word_timestamps=True,beam_size=5)
    rows=_segs_to_rows(segs)
    if not rows:
        st.warning("⚠️ VAD هیچ نەدۆزی — بەبێ فیلتەر هەوڵ دەدەم...")
        segs,_=model.transcribe(str(audio),language=lang,vad_filter=False,
            no_speech_threshold=0.55,word_timestamps=True,beam_size=5)
        rows=_segs_to_rows(segs)
    return rows

# ── Chunking ──────────────────────────────────────────────────────────────────
def build_chunks(segs,max_secs):
    if not segs: return []
    chunks,cur,t0=[],[],segs[0]["start"]
    for i,seg in enumerate(segs):
        cur.append(seg)
        if seg["end"]-t0>=max_secs:
            chunks.append(cur); cur=[]
            if i+1<len(segs): t0=segs[i+1]["start"]
    if cur: chunks.append(cur)
    return chunks

# ── ASS Builder ───────────────────────────────────────────────────────────────
def build_ass(translations,delay,font_sz,cr_font_sz,
              anime,xltr,season,tech,cr_secs,
              wm_text,wm_size,wm_color_hex,wm_al,song_color_hex):
    fn="Bahij Janna" if FONT_PATH else "Noto Naskh Arabic"
    sc=_hex_to_ass(song_color_hex); wc=_hex_to_ass(wm_color_hex)
    hdr=""
    hdr+="[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n"
    hdr+="ScaledBorderAndShadow: yes\nYCbCr Matrix: TV.709\n\n"
    hdr+="[V4+ Styles]\n"
    hdr+="Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
    hdr+=f"Style: Kurdish,{fn},{font_sz},&H00FFFFFF,&H000000FF,&H00000000,&H50000000,-1,0,0,0,100,100,0,0,1,1.8,0.9,2,40,40,30,1\n"
    hdr+=f"Style: Song,{fn},{font_sz},{sc},&H000000FF,&H00000000,&H50000000,-1,1,0,0,100,100,0,0,1,1.6,0.8,2,40,40,30,1\n"
    hdr+=f"Style: Credit,{fn},{cr_font_sz},&H00FFD700,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1.5,1.0,2,30,30,15,1\n"
    hdr+=f"Style: Watermark,{fn},{wm_size},{wc},&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.2,0,{wm_al},15,15,15,1\n\n"
    hdr+="[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    evts=[]
    parts=[p for p in [anime,f"وەرگێر: {xltr}" if xltr else "",season,tech] if p]
    if parts:
        evts.append(f"Dialogue: 1,0:00:01.50,{_ts(1.5+cr_secs)},Credit,,0,0,0,,{_NL.join(parts)}")
    if wm_text.strip():
        last=(translations[-1].get("end",0)+delay) if translations else 3600.0
        evts.append(f"Dialogue: 0,0:00:00.00,{_ts(last)},Watermark,,0,0,0,,{wm_text}")
    for row in translations:
        s=row.get("start",0.0)+delay; e=row.get("end",0.0)+delay
        t=row.get("text","").strip()
        if not t or s>=e: continue
        style="Song" if _is_song(t) else "Kurdish"
        evts.append(f"Dialogue: 0,{_ts(s)},{_ts(e)},{style},,0,0,0,,{t}")
    return hdr+"\n".join(evts)

# ── Burn (ultrafast) ──────────────────────────────────────────────────────────
def burn_subtitles(video,ass,out):
    af=f"ass={str(ass)}"
    if _FDEST.exists(): af+=f":fontsdir={str(FONTS_DIR)}"
    return _ffmpeg(["ffmpeg","-y","-i",str(video),"-vf",af,
                   "-c:v","libx264","-preset","ultrafast","-crf","20",
                   "-c:a","aac","-b:a","192k","-movflags","+faststart",
                   str(out)],"burn")

# ══════════════════════════════════════════════════════════════════════════════
# render_app: one complete independent subtitle app instance
# ══════════════════════════════════════════════════════════════════════════════
def render_app(p:str):
    """p = unique prefix ('t1' or 't2') for independent session state."""

    # ── Init state ────────────────────────────────────────────────────────────
    defs={f"{p}_transcript":None,f"{p}_translations":[],f"{p}_chunks":[],
          f"{p}_cur_chunk":0,f"{p}_cur_gem_idx":0,f"{p}_final_video_path":None,
          f"{p}_translation_done":False,f"{p}_sid":str(uuid.uuid4())[:8]}
    for k,v in defs.items(): st.session_state.setdefault(k,v)
    sid=st.session_state[f"{p}_sid"]

    # ── Layout: settings col | main col ──────────────────────────────────────
    sc,mc=st.columns([1,2],gap="large")

    with sc:
        st.markdown("#### 🔑 کلیلەکانی API")
        gkeys=[]
        for i in range(1,5):
            k=st.text_input(f"کلیل {i}",type="password",key=f"{p}_k{i}",label_visibility="visible")
            if k.strip(): gkeys.append(k)

        st.markdown("#### ☯️ مۆدێل")
        model=st.selectbox("",GEMINI_MODELS,key=f"{p}_model",label_visibility="collapsed")

        st.markdown("#### 🧠 Thinking")
        tl=st.selectbox("",list(THINKING_PRESETS.keys()),index=3,
                        key=f"{p}_think",label_visibility="collapsed")
        budget=THINKING_PRESETS[tl]

        st.markdown("#### 🌐 زمانی ڤیدیۆ")
        lc=st.selectbox("",list(LANGS.keys()),key=f"{p}_lang",label_visibility="collapsed")
        whisper_lang=LANGS[lc]

        st.divider()
        st.markdown("#### 🎵 گۆرانی")
        do_songs=st.toggle("وەرگێرانی گۆرانی",value=False,key=f"{p}_songs")
        song_color=st.color_picker("رەنگ","#FFD700",key=f"{p}_scolor")

        st.divider()
        font_sz=st.slider("🔤 فۆنتی ژێرنووس",20,80,54,key=f"{p}_font")
        chunk_secs=st.slider("⏱️ بڕگە (خولەک)",3,15,6,key=f"{p}_chunk")*60

        st.divider()
        st.caption("✅ "+FONT_PATH.name if FONT_PATH else "⚠️ Bahij Janna نەدۆزرایەوە")
        st.caption(f"🌹 v5.0 — {p.upper()}")

    with mc:
        # File uploader
        uploaded=st.file_uploader("📁 ڤیدیۆی خۆت بخەرە",
            type=["mp4","mkv","avi","mov","webm","m4v","flv","ts","wmv"],
            key=f"{p}_file")

        # Credits
        with st.expander("ℹ️ کرێدیت",expanded=False):
            c1,c2=st.columns(2)
            with c1:
                anime_name=st.text_input("🎬 ناوی فیلم",""   ,key=f"{p}_anime")
                xltr      =st.text_input("✍️ وەرگێر",""      ,key=f"{p}_xltr")
            with c2:
                season_ep =st.text_input("📺 سیزن/ئەڵقە",""  ,key=f"{p}_season")
                tech_line =st.text_input("💻 تەکنیک","Kurdish AI",key=f"{p}_tech")
            rc1,rc2=st.columns(2)
            with rc1: cr_secs=st.number_input("⏱️ کات (چرکە)",1.0,30.0,4.0,0.5,key=f"{p}_crsec")
            with rc2: cr_font=st.number_input("🔡 قەبارەی کرێدیت",14,48,24,2,key=f"{p}_crfont")

        # Watermark / Logo
        with st.expander("🔖 لۆگۆ",expanded=False):
            wm_text=st.text_input("دەقی لۆگۆ",""   ,key=f"{p}_wm")
            wa1,wa2,wa3=st.columns(3)
            with wa1: wm_sz =st.number_input("قەبارە",12,72,24,2 ,key=f"{p}_wmsize")
            with wa2: wm_col=st.color_picker("رەنگ","#FFFFFF"     ,key=f"{p}_wmcol")
            with wa3: wm_pos=st.radio("ئەلا",["چەپ","ناوەڕاست","ڕاست"],
                                      index=2,key=f"{p}_wmpos",horizontal=False)
            wm_al={"چەپ":7,"ناوەڕاست":8,"ڕاست":9}[wm_pos]

        sub_delay=st.slider("⏰ دواخستن (چرکە)",-15.0,15.0,0.0,0.1,key=f"{p}_delay")
        st.divider()

        # Action buttons
        b1,b2,b3=st.columns(3)
        btn_start =b1.button("▶️ دەست پێبکە",key=f"{p}_start",use_container_width=True,type="primary")
        btn_resume=b2.button("⏭️ Resume",     key=f"{p}_resume",use_container_width=True)
        btn_reset =b3.button("🔄 ڕیسێت",      key=f"{p}_reset", use_container_width=True)

        if btn_reset:
            for k in list(defs.keys()): st.session_state.pop(k,None)
            st.rerun()

        # ── PIPELINE ─────────────────────────────────────────────────────────
        def run_pipeline(resume=False):
            if not uploaded:  st.warning("⚠️ ڤیدیۆیەک بخەرە."); return
            if not gkeys:     st.warning("⚠️ لانیکەم یەک کلیل بخەرە."); return

            vid=TEMP_DIR/f"{sid}_input.mp4"
            with open(vid,"wb") as f: f.write(uploaded.read())

            status=st.empty(); pbar=st.progress(0)

            tr_key=f"{p}_transcript"
            if not resume or st.session_state[tr_key] is None:
                status.info("🎵 دەرهێنانی دەنگ...")
                try: audio=extract_audio(vid,sid)
                except RuntimeError as e: st.error(str(e)); return

                status.info("🔊 Whisper large-v3-turbo...")
                try: transcript=transcribe_audio(audio,whisper_lang)
                except Exception as e: st.error(f"❌ Whisper: {e}"); return
                if not transcript: st.warning("⚠️ هیچ دەنگێک نەدۆزرایەوە."); return

                st.session_state[tr_key]=transcript
                st.session_state[f"{p}_chunks"]=build_chunks(transcript,chunk_secs)
                st.session_state[f"{p}_cur_chunk"]=0
                st.session_state[f"{p}_translations"]=[]
                st.session_state[f"{p}_translation_done"]=False

            chunks=st.session_state[f"{p}_chunks"]
            total=len(chunks)
            start_i=st.session_state[f"{p}_cur_chunk"] if resume else 0
            trans=list(st.session_state[f"{p}_translations"]) if resume else []

            for i in range(start_i,total):
                pct=int(i/total*100)
                status.info(f"🔄 ٪{_ku(pct)} — بڕگەی {_ku(i+1)} لە {_ku(total)}")
                pbar.progress(pct)

                chunk=chunks[i]
                # filter songs if needed
                to_ai=[s for s in chunk if not(_is_song(s["text"]) and not do_songs)]
                passthrough=[s for s in chunk if _is_song(s["text"]) and not do_songs]

                ai_rows=[]
                if to_ai:
                    try:
                        ai_rows,st.session_state[f"{p}_cur_gem_idx"]=ai_translate(
                            gemini_keys=gkeys,
                            cur_gem_idx=st.session_state[f"{p}_cur_gem_idx"],
                            transcript_chunk=to_ai,
                            thinking_budget=budget,
                            selected_model=model,
                            status_msg=status)
                    except RuntimeError as e:
                        st.error(f"❌ بڕگەی {i+1}: {e}"); return

                pt=[{"start":s["start"],"end":s["end"],"text":s["text"]} for s in passthrough]
                trans.extend(sorted(ai_rows+pt,key=lambda x:x["start"]))
                st.session_state[f"{p}_translations"]=trans
                st.session_state[f"{p}_cur_chunk"]=i+1

            pbar.progress(100)
            st.session_state[f"{p}_translation_done"]=True
            status.success("✅ وەرگێڕان تەواو بوو! دەستکاری بکە پاشان داگیران بکە")

        if btn_start:  run_pipeline(resume=False)
        if btn_resume:
            if st.session_state[f"{p}_transcript"] is None: st.warning("⚠️ لە سەرەوە دەست پێبکە.")
            else: run_pipeline(resume=True)

        # ── Subtitle Editor ───────────────────────────────────────────────────
        if st.session_state[f"{p}_translations"]:
            st.divider()
            st.subheader("✏️ دەستکاریکردنی ژێرنووس")
            plain="\n".join(f'{r.get("start",0):.2f} --> {r.get("end",0):.2f} | {r.get("text","")}'
                              for r in st.session_state[f"{p}_translations"])
            edited=st.text_area("",value=plain,height=280,key=f"{p}_editor",label_visibility="collapsed")
            if st.button("💾 پاشەکەوت",key=f"{p}_save",use_container_width=True):
                upd=[]
                for line in edited.splitlines():
                    m=re.match(r"(\d+\.?\d*)\s*-->\s*(\d+\.?\d*)\s*\|(.*)",line.strip())
                    if m: upd.append({"start":float(m.group(1)),"end":float(m.group(2)),"text":m.group(3).strip()})
                if upd: st.session_state[f"{p}_translations"]=upd; st.success("✅ پاشەکەوتکرا.")
                else: st.error("❌ فۆرمات هەڵەیە.")

        # ── Burn Button ───────────────────────────────────────────────────────
        if st.session_state[f"{p}_translation_done"] and st.session_state[f"{p}_translations"]:
            st.divider()
            st.info("🎬 ژێرنووس ئامادەیە — دەستکاری بکە پاشان داگیران بکە")
            if st.button("🔥 داگیراندن بۆ ناو ڤیدیۆ",key=f"{p}_burn",
                         use_container_width=True,type="primary"):
                if not uploaded: st.warning("⚠️ ڤیدیۆکە دووبارە بخەرە."); st.stop()
                vid=TEMP_DIR/f"{sid}_input.mp4"
                if not vid.exists():
                    with open(vid,"wb") as f: f.write(uploaded.read())
                ass_str=build_ass(
                    translations=st.session_state[f"{p}_translations"],
                    delay=sub_delay,font_sz=font_sz,cr_font_sz=cr_font,
                    anime=anime_name,xltr=xltr,season=season_ep,tech=tech_line,
                    cr_secs=cr_secs,wm_text=wm_text,wm_size=wm_sz,
                    wm_color_hex=wm_col,wm_al=wm_al,song_color_hex=song_color)
                ass_p=TEMP_DIR/f"{sid}_subs.ass"
                ass_p.write_text(ass_str,encoding="utf-8")
                out_p=TEMP_DIR/f"{sid}_subtitled.mp4"
                with st.spinner("🔥 داگیراندن..."):
                    ok=burn_subtitles(vid,ass_p,out_p)
                if ok:
                    st.session_state[f"{p}_final_video_path"]=str(out_p)
                    st.success("🎉 ئامادەی دانلۆد!")
                    st.rerun()

        # ── Download ──────────────────────────────────────────────────────────
        vp=st.session_state.get(f"{p}_final_video_path")
        if vp:
            out_p=Path(vp)
            if out_p.exists():
                st.divider()
                mb=out_p.stat().st_size/1024/1024
                st.caption(f"📦 {mb:.1f} MB")
                with open(out_p,"rb") as fh:
                    st.download_button("📥 دانلۆدی ڤیدیۆ (subtitled.mp4)",
                        data=fh,file_name="subtitled.mp4",mime="video/mp4",
                        use_container_width=True,type="primary",key=f"{p}_dl")
                if mb<150: st.video(str(out_p))
                else: st.info("ℹ️ گەورەیە — دانلۆد بکە بۆ سەیرکردن.")

# ══════════════════════════════════════════════════════════════════════════════
# MAIN — Title + Two Tabs
# ══════════════════════════════════════════════════════════════════════════════
st.title("🎬 دروستکردنی ژێرنووسی کینەماتۆگرافیکی کوردی سۆرانی")
st.caption("وەک ئەوەی کات بوەستێت — جیهان هەمووی بچووک دەبێت — و تۆ دەبیتە هەموو جیهانم")
st.divider()

tab1,tab2=st.tabs(["🎬  ئەپی یەکەم — ڤیدیۆی یەکەم","🎬  ئەپی دووەم — ڤیدیۆی دووەم"])
with tab1: render_app("t1")
with tab2: render_app("t2")
