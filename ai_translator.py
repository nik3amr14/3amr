import json
import re
import time
import random
from pydantic import BaseModel

from google import genai
from google.genai import types
from groq import Groq

# ═══════════════════════════════════════════════════════════════════
#  قوفڵکردنی شێوازی وەڵام بە قالبێکی بیرکاری (Pydantic Schema)
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
# مۆدێلی دیپ‌سیک بۆ گرۆق زیاد کرا
GROQ_FALLBACKS = ["deepseek-r1-distill-llama-70b", "llama-3.3-70b-versatile", "gemma2-9b-it", "llama-3.1-8b-instant"]

FORBIDDEN_PUNCTUATION = "؟.،!:؛\"'-_()[]{}،,+=*#$@^&|~`"

# ═══════════════════════════════════════════════════════════════════
#  مێشکی وەرگێڕان (گەنجانە، بێ سانسۆر، پاراو)
# ═══════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are an elite professional Kurdish Sorani cinematic subtitle translator.

Your objective is to translate the spoken text into natural, cinematic Kurdish Sorani.
Follow these rules strictly:

1. EXACT TRANSLATION OF SPOKEN WORDS (NO CENSORSHIP):
   - Translate exactly what the character says with 100% honesty and accuracy.
   - Do NOT be shy, do NOT censor, and do NOT soften the tone. If a character swears, uses raw slang, or speaks vulgarly, translate it exactly as they said it with equal intensity.
   - NEVER add unnecessary politeness.

2. AVOID OVERUSING HONORIFICS ("کاک" / "خاتوون"):
   - Do NOT translate Japanese honorifics (like "-san", "-kun", "-sama") or English titles (like "Mr.") literally as "کاک" or "خاتوون" unless it is a highly formal business or government setting.
   - In casual, friendly, or cool conversations, completely ignore these honorifics and use the character's name directly (e.g. translate "Sasaki-san" as "ساساکی", NOT "کاک ساساکی").

3. NO LITERAL TRANSLATION:
   - Capture the true spoken meaning.
   - Maintain natural Kurdish grammar (Subject-Object-Verb). For example, do NOT write "ئێستا بە دەستی تۆیە هەموو شتێک", instead write "ئێستا هەموو شتێک لە دەستی تۆدایە".

4. TIMESTAMPS:
   - Keep "start" and "end" timestamps exactly as provided. Do not alter them.

5. NO PUNCTUATION:
   - Completely strip all punctuation (؟ . ، ! : ؛ " ' - _) from the translated text. Output only clean words.

6. ROW ALIGNMENT:
   - Translate EVERY single row. The output JSON array must have the exact same number of items as the input.
"""

def _strip_forbidden_punctuation(text: str) -> str:
    if not isinstance(text, str): return text
    for ch in FORBIDDEN_PUNCTUATION: text = text.replace(ch, "")
    return text

def _log(status_msg, message: str) -> None:
    if status_msg is None: return
    if hasattr(status_msg, "write"): status_msg.write(message)
    elif callable(status_msg): status_msg(message)

def extract_json(text: str) -> dict:
    if not text: return {}
    cleaned = text.strip()
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    try:
        return json.loads(cleaned)
    except Exception as e:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end+1])
            except:
                pass
        raise ValueError(f"Failed to parse JSON: {e}")

def _validate_translations(data: dict, expected_count: int) -> list:
    translations = data.get("translations", [])
    if len(translations) != expected_count:
        raise ValueError(f"Row count mismatch: expected {expected_count}, got {len(translations)}")
    return translations

def _build_generation_config_gemini(thinking_budget, model_name: str):
    budget_map = {"minimal": 0, "medium": 8192, "high": 24576, "dynamic": -1, 0: 0, 8192: 8192, 24576: 24576, -1: -1}
    resolved_budget = budget_map.get(thinking_budget, 8192)

    config_kwargs = {
        "system_instruction": SYSTEM_PROMPT,
        "temperature": 0.75,
        "response_mime_type": "application/json",
        "response_schema": SubtitleResponse
    }

    if resolved_budget != 0:
        if "gemini-3" in model_name:
            level_map = {0: "minimal", 8192: "medium", 24576: "high", -1: "high", "minimal": "minimal", "medium": "medium", "high": "high", "dynamic": "high"}
            resolved_level = level_map.get(thinking_budget, "medium")
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level=resolved_level)
        else:
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=resolved_budget)

    return types.GenerateContentConfig(**config_kwargs)

def translate_with_gemini(api_keys, current_key_index, transcript_chunk, thinking_budget, selected_model, status_msg):
    if not api_keys: return [], current_key_index
    
    models_to_try = [selected_model]
    for m in GEMINI_FALLBACKS:
        if m not in models_to_try: models_to_try.append(m)

    user_prompt = f"Translate every row into natural Kurdish Sorani. Input array:\n\n{json.dumps(transcript_chunk, ensure_ascii=False)}"
    expected = len(transcript_chunk)

    for model_name in models_to_try:
        generation_config = _build_generation_config_gemini(thinking_budget, model_name)
        attempt = 0
        while attempt < MAX_ATTEMPTS_PER_MODEL:
            api_key = api_keys[current_key_index % len(api_keys)]
            attempt += 1
            try:
                client = genai.Client(api_key=api_key)
                if thinking_budget == 0 or thinking_budget == "minimal":
                    _log(status_msg, f"⚡ [Google: {model_name}] - وەرگێڕانی خێرا (هەوڵی {attempt}/{MAX_ATTEMPTS_PER_MODEL})...")
                else:
                    _log(status_msg, f"🧠 [Google: {model_name}] - مێشکی داینامیکی (هەوڵی {attempt}/{MAX_ATTEMPTS_PER_MODEL})...")
                
                response = client.models.generate_content(
                    model=model_name, contents=[user_prompt], config=generation_config
                )
                
                raw_text = response.text or ""
                raw_text = _clean_json_text(raw_text)
                data = extract_json(raw_text)
                translated = _validate_translations(data, expected)

                for original, item in zip(transcript_chunk, translated):
                    item["start"] = original.get("start")
                    item["end"] = original.get("end")
                    item["text"] = _strip_forbidden_punctuation(item.get("text", ""))

                return translated, current_key_index

            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "resource_exhausted" in err_str or "quota" in err_str:
                    current_key_index = (current_key_index + 1) % max(len(api_keys), 1)
                    time.sleep(1.5)
                elif "503" in err_str or "unavailable" in err_str or "overloaded" in err_str:
                    wait_time = min(MAX_WAIT_TIME, (2 ** attempt) + random.uniform(0.5, 2.0))
                    _log(status_msg, f"🔥 گووگڵ قەرەباڵغە! چاوەڕێین بۆ {wait_time:.1f} چرکە...")
                    time.sleep(wait_time)
                else:
                    _log(status_msg, f"⚠️ کێشەیەک ڕوویدا لە {model_name}: {str(e)[:100]}")
                    time.sleep(3)
    return [], current_key_index

def translate_with_groq(groq_keys, current_key_index, transcript_chunk, selected_model, status_msg):
    if not groq_keys: return [], current_key_index
    
    models_to_try = [selected_model]
    for m in GROQ_FALLBACKS:
        if m not in models_to_try: models_to_try.append(m)

    user_prompt = f"Translate this subtitle JSON exactly into clean, unpunctuated Kurdish Sorani. Do NOT add any markdown, return only the JSON:\n\n{json.dumps(transcript_chunk, ensure_ascii=False)}"
    expected = len(transcript_chunk)
    
    for model_name in models_to_try:
        attempt = 0
        while attempt < len(groq_keys) * 2:
            cur_key = groq_keys[current_key_index % len(groq_keys)]
            attempt += 1
            try:
                client = Groq(api_key=cur_key)
                _log(status_msg, f"⚡ [Groq: {model_name}] - وەرگێڕانی خێرا بە گرۆق... (هەوڵی {attempt})")
                
                resp = client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT + "\nOutput must be a JSON object with key 'translations': [{'start':float, 'end':float, 'text':string}]"},
                        {"role": "user", "content": user_prompt}
                    ],
                    model=model_name,
                    temperature=0.75,
                    response_format={"type": "json_object"}
                )
                
                raw_text = resp.choices[0].message.content or ""
                raw_text = _clean_json_text(raw_text)
                data = extract_json(raw_text)
                translated = _validate_translations(data, expected)

                for original, item in zip(transcript_chunk, translated):
                    item["start"] = original.get("start")
                    item["end"] = original.get("end")
                    item["text"] = _strip_forbidden_punctuation(item.get("text", ""))

                return translated, current_key_index
            except Exception as e:
                err_str = str(e).lower()
                if "429" in err_str or "rate_limit" in err_str or "quota" in err_str:
                    current_key_index = (current_key_index + 1) % max(len(groq_keys), 1)
                    time.sleep(1.5)
                elif "503" in err_str or "unavailable" in err_str or "overloaded" in err_str:
                    wait_time = min(MAX_WAIT_TIME, (2 ** attempt) + random.uniform(0.5, 2.0))
                    _log(status_msg, f"🔥 گرۆق قەرەباڵغە! چاوەڕێین بۆ {wait_time:.1f} چرکە...")
                    time.sleep(wait_time)
                else:
                    _log(status_msg, f"⚠️ هەڵە لە سێرڤەری Groq: {str(e)[:100]}")
                    time.sleep(3)
    return [], current_key_index

# ═══════════════════════════════════════════════════════════════════
#  دەروازەی سەرەکی وەرگێڕان (Multi-Cloud Orchestrator)
# ═══════════════════════════════════════════════════════════════════
def ai_translate(provider: str, gemini_keys: list, groq_keys: list, cur_gem_idx: int, cur_groq_idx: int, transcript_chunk: list, thinking_budget, gemini_model: str, groq_model: str, status_msg) -> tuple[list, int, int]:
    is_groq_primary = "Groq" in provider
    
    if is_groq_primary:
        if groq_keys:
            translated, cur_groq_idx = translate_with_groq(groq_keys, cur_groq_idx, transcript_chunk, groq_model, status_msg)
            if translated: return translated, cur_gem_idx, cur_groq_idx
        if gemini_keys:
            _log(status_msg, "🚨 سێرڤەری Groq بە تەواوی وەستا! گواستنەوەی خێرا بۆ سێرڤەری یەدەگی Google Gemini...")
            time.sleep(2)
            translated, cur_gem_idx = translate_with_gemini(gemini_keys, cur_gem_idx, transcript_chunk, thinking_budget, gemini_model, status_msg)
            if translated: return translated, cur_gem_idx, cur_groq_idx
    else:
        if gemini_keys:
            translated, cur_gem_idx = translate_with_gemini(gemini_keys, cur_gem_idx, transcript_chunk, thinking_budget, gemini_model, status_msg)
            if translated: return translated, cur_gem_idx, cur_groq_idx
        if groq_keys:
            _log(status_msg, "🚨 سێرڤەری Google بە تەواوی وەستا! گواستنەوەی خێرا بۆ سێرڤەری یەدەگی Groq...")
            time.sleep(2)
            translated, cur_groq_idx = translate_with_groq(groq_keys, cur_groq_idx, transcript_chunk, groq_model, status_msg)
            if translated: return translated, cur_gem_idx, cur_groq_idx

    return [], cur_gem_idx, cur_groq_idx
