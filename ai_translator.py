import json
import re
import time
import random
from pydantic import BaseModel

from google import genai
from google.genai import types
from groq import Groq

# ═══════════════════════════════════════════════════════════════════
#  ١. قوفڵکردنی شێوازی وەڵام بە قالبێکی بیرکاری (Pydantic Schema)
# ═══════════════════════════════════════════════════════════════════
class SubtitleItem(BaseModel):
    start: float
    end: float
    text: str

class SubtitleResponse(BaseModel):
    translations: list[SubtitleItem]

# ═══════════════════════════════════════════════════════════════════
#  ڕێکخستنە سەرەکییەکان
# ═══════════════════════════════════════════════════════════════════
MAX_ATTEMPTS_PER_MODEL = 10  
MAX_WAIT_TIME = 15.0        

GEMINI_FALLBACKS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-3-flash-preview"]
GROQ_FALLBACKS = ["llama-3.3-70b-specdec", "llama-4-scout", "qwen-3.6-27b"]

FORBIDDEN_PUNCTUATION = "؟.،!:؛\"'-_"

# ═══════════════════════════════════════════════════════════════════
#  مێشکی وەرگێڕان - گەنجانە، پاراو، بێ سانسۆر، و ڕێک وەک دەمی کارەکتەر
# ═══════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are an elite Kurdish Sorani cinematic subtitle translator.
Your goal is to translate the dialogue exactly as the character speaks it, making it sound natural, youthful, and relatable to a Kurdish audience.

Strict Rules:
1. EXACT & UNCENSORED: Translate exactly what they mean. Do NOT censor swear words, slang, or raw emotions. If they speak like a cool youth, translate it into cool, modern Kurdish Sorani youth language.
2. NO ROBOTIC/LITERAL TRANSLATION: Do not translate word-for-word. Use natural Kurdish phrasing.
3. NO HONORIFICS IN CASUAL SPEECH: Do NOT use "کاک" or "خاتوون" for "-san", "-kun", or "Mr." unless it is a highly strict formal/business setting. Just use their names.
4. TIMESTAMPS: Keep "start" and "end" timestamps EXACTLY as provided. Do not change 0.01 seconds.
5. NO PUNCTUATION: Completely strip all punctuation (؟ . ، ! : ؛ " ' - _) from the translated text. Output only clean words.
6. ROW ALIGNMENT: You MUST translate EVERY single row. Do not skip whispers, sighs, or short words. The output array length must perfectly match the input array length.
"""

def _strip_forbidden_punctuation(text: str) -> str:
    if not isinstance(text, str): return text
    for ch in FORBIDDEN_PUNCTUATION: text = text.replace(ch, "")
    return text

def _log(status_msg, message: str) -> None:
    if status_msg is None: return
    if hasattr(status_msg, "write"): status_msg.write(message)
    elif callable(status_msg): status_msg(message)

def _build_generation_config_gemini(thinking_budget, model_name: str):
    budget_map = {"minimal": 0, "medium": 2048, "high": -1, 0: 0, 2048: 2048, -1: -1}
    resolved_budget = budget_map.get(thinking_budget, 2048)

    config_kwargs = {
        "system_instruction": SYSTEM_PROMPT,
        "temperature": 0.75,
        "response_mime_type": "application/json",
        "response_schema": SubtitleResponse
    }

    if resolved_budget != 0:
        if "gemini-3" in model_name:
            level_map = {0: "minimal", 2048: "medium", -1: "high", "minimal": "minimal", "medium": "medium", "high": "high"}
            resolved_level = level_map.get(thinking_budget, "medium")
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=resolved_level)
        else:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=resolved_budget)

    return types.GenerateContentConfig(**config_kwargs)

# ═══════════════════════════════════════════════════════════════════
#  مۆتۆڕی وەرگێڕانی جێمینای لای گووگڵ (Google Gemini Engine)
# ═══════════════════════════════════════════════════════════════════
def translate_with_gemini(api_keys, current_key_index, transcript_chunk, thinking_budget, selected_model, status_msg):
    models_to_try = [selected_model]
    for m in GEMINI_FALLBACKS:
        if m not in models_to_try: models_to_try.append(m)

    user_prompt = f"Translate every row into natural, youthful Kurdish Sorani. Input array:\n\n{json.dumps(transcript_chunk, ensure_ascii=False)}"

    for model_name in models_to_try:
        generation_config = _build_generation_config_gemini(thinking_budget, model_name)
        attempt = 0
        while attempt < MAX_ATTEMPTS_PER_MODEL:
            api_key = api_keys[current_key_index % len(api_keys)]
            attempt += 1
            try:
                client = genai.Client(api_key=api_key)
                _log(status_msg, f"🧠 [Google: {model_name}] - وەرگێڕان بە کلیل {current_key_index + 1}... (هەوڵی {attempt}/{MAX_ATTEMPTS_PER_MODEL})")
                
                resp = client.models.generate_content(
                    model=model_name, contents=[user_prompt], config=generation_config
                )
                raw_data = json.loads(resp.text)
                translated = raw_data.get("translations", [])

                if len(translated) != len(transcript_chunk):
                    raise ValueError("ژمارەی دێڕەکان هاوتا نییە.")

                for original, item in zip(transcript_chunk, translated):
                    item["start"] = original.get("start")
                    item["end"] = original.get("end")
                    item["text"] = _strip_forbidden_punctuation(item.get("text", ""))

                return translated, current_key_index

            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "resource_exhausted" in err_str:
                    current_key_index = (current_key_index + 1) % len(api_keys)
                    time.sleep(1.5)
                elif "503" in err_str or "unavailable" in err_str:
                    wait_time = min(MAX_WAIT_TIME, (2 ** attempt) + random.uniform(0.5, 2.0))
                    _log(status_msg, f"🔥 گووگڵ قەرەباڵغە! چاوەڕێین بۆ {wait_time:.1f} چرکە...")
                    time.sleep(wait_time)
                else:
                    time.sleep(2)
    return [], current_key_index

# ═══════════════════════════════════════════════════════════════════
#  مۆتۆڕی وەرگێڕانی گرۆق لای مێتا/ئەلیبابا (Groq Engine)
# ═══════════════════════════════════════════════════════════════════
def translate_with_groq(groq_keys, current_key_index, transcript_chunk, selected_model, status_msg):
    models_to_try = [selected_model]
    for m in GROQ_FALLBACKS:
        if m not in models_to_try: models_to_try.append(m)

    user_prompt = f"Translate this subtitle JSON exactly into clean, unpunctuated, natural Kurdish Sorani. Do NOT add any markdown, return ONLY the JSON:\n\n{json.dumps(transcript_chunk, ensure_ascii=False)}"
    
    for model_name in models_to_try:
        attempt = 0
        while attempt < MAX_ATTEMPTS_PER_MODEL:
            cur_key = groq_keys[current_key_index % len(groq_keys)]
            attempt += 1
            try:
                client = Groq(api_key=cur_key)
                _log(status_msg, f"⚡ [Groq: {model_name}] - وەرگێڕانی خێرا بە کلیل {current_key_index + 1}... (هەوڵی {attempt})")
                
                resp = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT + "\nOutput must be a JSON object with key 'translations': [{'start':float, 'end':float, 'text':string}]"},
                        {"role": "user", "content": user_prompt}
                    ],
                    model=model_name,
                    temperature=0.75,
                    response_format={"type": "json_object"} 
                )
                raw_data = json.loads(resp.choices[0].message.content)
                translated = raw_data.get("translations", [])

                if len(translated) != len(transcript_chunk):
                    raise ValueError("ژمارەی دێڕەکان هاوتا نییە.")

                for original, item in zip(transcript_chunk, translated):
                    item["start"] = original.get("start")
                    item["end"] = original.get("end")
                    item["text"] = _strip_forbidden_punctuation(item.get("text", ""))

                return translated, current_key_index

            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate_limit" in err_str:
                    current_key_index = (current_key_index + 1) % len(groq_keys)
                    time.sleep(1.5)
                else:
                    wait_time = min(MAX_WAIT_TIME, (2 ** attempt) + random.uniform(0.5, 2.0))
                    _log(status_msg, f"⚠️ سێرڤەری Groq قەرەباڵغە، چاوەڕێین بۆ {wait_time:.1f} چرکە...")
                    time.sleep(wait_time)
                    
    return [], current_key_index

# ═══════════════════════════════════════════════════════════════════
#  دەروازەی سەرەکی وەرگێڕان (Orchestrator Gateway)
# ═══════════════════════════════════════════════════════════════════
def ai_translate(provider: str, api_keys: list, current_key_index: int, transcript_chunk: list, thinking_budget, selected_model: str, status_msg) -> tuple[list, int]:
    if provider == "Groq (خێراترین - Llama & Qwen)":
        return translate_with_groq(api_keys, current_key_index, transcript_chunk, selected_model, status_msg)
    else:
        return translate_with_gemini(api_keys, current_key_index, transcript_chunk, thinking_budget, selected_model, status_msg)
