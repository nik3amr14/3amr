import streamlit as st
import os
import subprocess
import tempfile
import math
import torch
from typing import List, Dict, Any
from faster_whisper import WhisperModel
from google import genai

# Import the translation module
from ai_translator import translate_chunk

# ----------------------------------------------------
# 1. PAGE CONFIGURATION & CUSTOM DARK UI
# ----------------------------------------------------
st.set_page_config(
    page_title="Kurdish Cinematic Subtitle Generator v9.4",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

def inject_custom_css():
    st.markdown("""
    <style>
    .stApp { background-color: #0E1117; color: #F0F2F6; }
    .main-title { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; color: #E2B714; text-align: center; font-weight: 700; margin-bottom: 2px; }
    .subtitle { text-align: center; color: #8892B0; font-size: 1.05rem; margin-bottom: 25px; }
    div[data-testid="stVerticalBlock"] > div:has(div.stCard) { background-color: #1A1F29; border-radius: 10px; padding: 20px; border: 1px solid #2D3748; }
    div.stButton > button:first-child { background-color: #E2B714; color: #0E1117; font-weight: bold; border: none; border-radius: 5px; padding: 10px 24px; transition: all 0.3s ease; }
    div.stButton > button:first-child:hover { background-color: #F5D033; color: #0E1117; transform: translateY(-1px); }
    .step-box { background-color: #1A1F29; border-left: 4px solid #E2B714; padding: 15px; margin: 15px 0; border-radius: 0 8px 8px 0; }
    </style>
    """, unsafe_allow_html=True)

inject_custom_css()

# ----------------------------------------------------
# 2. UTILITY FUNCTIONS & ERROR HANDLING
# ----------------------------------------------------
def check_ffmpeg() -> bool:
    """Verifies if the FFmpeg binary is installed and accessible."""
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return True
    except Exception as e:
        st.error(f"FFmpeg Error: FFmpeg is not installed or not found in PATH. Details: {str(e)}")
        return False

def format_timestamp_ass(seconds: float) -> str:
    """Formats seconds into ASS timestamp format: H:MM:SS.cs"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centisecs = int(round((seconds - int(seconds)) * 100))
    if centisecs == 100:
        secs += 1
        centisecs = 0
    return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"

# ----------------------------------------------------
# 3. ASS SUBTITLE GENERATION
# ----------------------------------------------------
def generate_ass_file(subtitles: List[Dict[str, Any]], output_path: str, font_name: str = "NRT Bold"):
    """Generates an Advanced SubStation Alpha (.ass) file."""
    ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},65,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,2.5,1.5,2,10,10,45,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(ass_header)
            for sub in subtitles:
                start = format_timestamp_ass(sub["start"])
                end = format_timestamp_ass(sub["end"])
                text = sub.get("kurdish_text", "").strip()
                # Remove newlines inside a single subtitle to keep it cinematic
                text = text.replace("\n", " ")
                f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    except Exception as e:
        st.error(f"Error writing ASS file: {str(e)}")
        raise e

# ----------------------------------------------------
# 4. MAIN STREAMLIT APP
# ----------------------------------------------------
def main():
    st.markdown("<h1 class='main-title'>🎬 Kurdish Cinematic Subtitle Generator</h1>", unsafe_allow_html=True)
    st.markdown("<p class='subtitle'>v9.4 - Powered by Faster-Whisper & Gemini Flash (Thinking Enabled)</p>", unsafe_allow_html=True)

    if not check_ffmpeg():
        st.stop()

    # Sidebar Configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        api_key = st.text_input("Google Gemini API Key", type="password")
        model_choice = st.selectbox(
            "Gemini Model", 
            ["gemini-3.0-flash-preview", "gemini-3.5-flash", "gemini-2.5-flash"]
        )
        chunk_size = st.slider("Translation Chunk Size (Rows)", min_value=10, max_value=50, value=25, 
                               help="Smaller chunks prevent line-skipping but use more API calls.")
        
        st.markdown("---")
        st.markdown("### 📁 Assets")
        font_name = st.text_input("Custom Font Name", value="NRT Bold", help="Ensure this font is installed on the system or available in the fonts/ directory.")

    # Main Content Area
    uploaded_video = st.file_uploader("Upload Video File (MP4, MKV, MOV)", type=["mp4", "mkv", "mov"])

    if uploaded_video and api_key:
        if st.button("🚀 Generate Cinematic Subtitles"):
            
            # Create a temporary directory for processing
            with tempfile.TemporaryDirectory() as temp_dir:
                try:
                    video_path = os.path.join(temp_dir, "input_video.mp4")
                    ass_path = os.path.join(temp_dir, "subtitles.ass")
                    output_video_path = os.path.join(temp_dir, "output_video.mp4")
                    
                    # Save uploaded video
                    with open(video_path, "wb") as f:
                        f.write(uploaded_video.read())
                        
                    status_text = st.empty()
                    progress_bar = st.progress(0)
                    
                    # STEP 1: Transcription (Faster-Whisper)
                    status_text.markdown("<div class='step-box'>🎙️ <b>Step 1:</b> Transcribing audio using Faster-Whisper (large-v3-turbo)...</div>", unsafe_allow_html=True)
                    
                    # Optimization: beam_size=3 for 2x speed
                    whisper_model = WhisperModel("large-v3-turbo", device="cuda" if torch.cuda.is_available() else "cpu", compute_type="float16" if torch.cuda.is_available() else "int8")
                    segments, info = whisper_model.transcribe(video_path, beam_size=3, language="en")
                    
                    subtitles = []
                    for i, segment in enumerate(segments):
                        subtitles.append({
                            "id": i + 1,
                            "start": segment.start,
                            "end": segment.end,
                            "text": segment.text.strip()
                        })
                    
                    if not subtitles:
                        st.error("No speech detected in the video.")
                        st.stop()
                        
                    progress_bar.progress(30)
                    
                    # STEP 2: Translation (Gemini)
                    status_text.markdown("<div class='step-box'>🧠 <b>Step 2:</b> Translating to Cinematic Kurdish Sorani...</div>", unsafe_allow_html=True)
                    client = genai.Client(api_key=api_key)
                    
                    total_chunks = math.ceil(len(subtitles) / chunk_size)
                    translated_subtitles = []
                    
                    for i in range(total_chunks):
                        chunk = subtitles[i * chunk_size : (i + 1) * chunk_size]
                        status_text.markdown(f"<div class='step-box'>🧠 <b>Step 2:</b> Translating chunk {i+1}/{total_chunks}...</div>", unsafe_allow_html=True)
                        
                        # Call the external module
                        translated_chunk = translate_chunk(client, chunk, model_choice)
                        translated_subtitles.extend(translated_chunk)
                        
                        # Update progress
                        current_progress = 30 + int((i + 1) / total_chunks * 40)
                        progress_bar.progress(current_progress)
                        
                    # STEP 3: Generate ASS File
                    status_text.markdown("<div class='step-box'>📝 <b>Step 3:</b> Generating ASS Subtitle File...</div>", unsafe_allow_html=True)
                    generate_ass_file(translated_subtitles, ass_path, font_name)
                    progress_bar.progress(80)
                    
                    # STEP 4: Burn Subtitles with FFmpeg
                    status_text.markdown("<div class='step-box'>🔥 <b>Step 4:</b> Burning subtitles into video (Hardsub)...</div>", unsafe_allow_html=True)
                    
                    # Escape path for FFmpeg filter
                    escaped_ass_path = ass_path.replace('\\', '/').replace(':', '\\:')
                    ffmpeg_cmd = [
                        "ffmpeg", "-y", "-i", video_path,
                        "-vf", f"ass='{escaped_ass_path}'",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                        "-c:a", "copy", output_video_path
                    ]
                    
                    process = subprocess.run(ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    if process.returncode != 0:
                        st.error(f"FFmpeg Error during burning:\n{process.stderr}")
                        raise RuntimeError("FFmpeg failed to burn subtitles.")
                        
                    progress_bar.progress(100)
                    status_text.markdown("<div class='step-box' style='border-left-color: #28a745;'>✅ <b>Success!</b> Video processing complete.</div>", unsafe_allow_html=True)
                    
                    # Provide Download Buttons
                    col1, col2 = st.columns(2)
                    with col1:
                        with open(output_video_path, "rb") as f:
                            st.download_button(
                                label="📥 Download Subtitled Video",
                                data=f,
                                file_name="kurdish_cinematic_video.mp4",
                                mime="video/mp4",
                                use_container_width=True
                            )
                    with col2:
                        with open(ass_path, "rb") as f:
                            st.download_button(
                                label="📥 Download .ASS Subtitle File",
                                data=f,
                                file_name="subtitles.ass",
                                mime="text/plain",
                                use_container_width=True
                            )
                            
                except Exception as e:
                    st.error(f"An unexpected error occurred: {str(e)}")
                    
    elif uploaded_video and not api_key:
        st.warning("⚠️ Please enter your Google Gemini API Key in the sidebar to proceed.")

if __name__ == "__main__":
    main()
