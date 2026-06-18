import json
import re
import time
import math
import random
from pydantic import BaseModel

from google import genai
from google.genai import types

# ═══════════════════════════════════════════════════════════════════
#  ١. قوفڵکردنی شێوازی وەڵام بە قالبێکی بیرکاری (Pydantic Schema) [2.1]
# ═══════════════════════════════════════════════════════════════════
class SubtitleItem(BaseModel):
    start: float
    end: float
    text: str

class SubtitleResponse(BaseModel):
    translations: list[SubtitleItem]

# ═══════════════════════════════════════════════════════════════════
#  ڕێکخستنی کاتەکانی پشوودان
# ═══════════════════════════════════════════════════════════════════
MAX_ATTEMPTS_PER_MODEL = 6  
MAX_WAIT_TIME = 15.0        

FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-3-flash-preview"]
FORBIDDEN_PUNCTUATION = "؟.،!:؛\"'-_"

# ═══════════════════════════════════════════════════════════════════
#  مێشکی وەرگێڕان (System Prompt)
# ═══════════════════════════════════════════════════════════════════
SYSTEM_PROMPT = """You are an elite professional Kurdish Sorani cinematic subtitle translator.

Your objective is to translate the spoken text into natural, cinematic Kurdish Sorani.
Follow these rules strictly:

1. EXACT TRANSLATION OF SPOKEN WORDS (NO CENSORSHIP):
   - Translate exactly what the character says with 100% honesty and accuracy.
   - Do NOT be shy, do NOT censor, and do NOT soften the tone. If a character swears, uses raw slang, or speaks vulgarly, translate it exactly as they said it with equal intensity.
   - NEVER add unnecessary politeness, and do NOT alter the character's core personality.

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
    if not isinstance(text, str):
        return text
    for ch in FORBIDDEN_PUNCTUATION:
        text = text.replace(ch, "")
    return text

def _log(status_msg, message: str) -> None:
    if status_msg is None:
        return
    if hasattr(status_msg, "write"):
        status_msg.write(message)
    elif callable(status_msg):
        status_msg(message)

def _build_model_list(selected_model: str) -> list:
    models_to_try = [selected_model]
    for model_name in FALLBACK_MODELS:
        if model_name not in models_to_try:
            models_to_try.append(model_name)
    return models_to_try

def _build_generation_config(thinking_budget, model_name: str):
    budget_map = {"minimal": 0, "medium": 2048, "high": -1, 0: 0, 2048: 2048, -1: -1}
    resolved_budget = budget_map.get(thinking_budget, 2048)

    config_kwargs = {
        "system_instruction": SYSTEM_PROMPT,
        "temperature": 0.75, # گەرمی لەسەر باشترین هاوسەنگی جێگیرکرا
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
#  مۆتۆڕی سەرەکی وەرگێڕان (Main Engine with Exponential Backoff with Jitter)
# ═══════════════════════════════════════════════════════════════════
def gemini_translate(api_keys: list, current_key_index: int, transcript_chunk: list, thinking_budget, selected_model: str, status_msg) -> tuple[list, int]:
    if not api_keys:
        raise ValueError("api_keys is empty.")
    if not transcript_chunk:
        return [], current_key_index

    models_to_try = _build_model_list(selected_model)
    
    formatted_chunk = [{"start": c["start"], "end": c["end"], "text": c["text"]} for c in transcript_chunk]
    user_prompt = f"Translate the following transcript exactly. Input array:\n\n{json.dumps(formatted_chunk, ensure_ascii=False)}"

    last_error = None

    for model_name in models_to_try:
        generation_config = _build_generation_config(thinking_budget, model_name)
        attempt = 0

        while attempt < MAX_ATTEMPTS_PER_MODEL:
            api_key = api_keys[current_key_index % len(api_keys)]
            attempt += 1

            try:
                client = genai.Client(api_key=api_key)
                
                if thinking_budget == 0 or thinking_budget == "minimal":
                    _log(status_msg, f"⚡ [{model_name}] - وەرگێڕانی خێرا (هەوڵی {attempt}/{MAX_ATTEMPTS_PER_MODEL})...")
                else:
                    _log(status_msg, f"🧠 [{model_name}] - مێشکی زیرەک (هەوڵی {attempt}/{MAX_ATTEMPTS_PER_MODEL})...")

                response = client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=generation_config,
                )

                raw_data = json.loads(response.text)
                translated = raw_data.get("translations", [])

                if len(translated) != len(transcript_chunk):
                    raise ValueError("ژمارەی دێڕەکان هاوتا نییە.")

                for original, item in zip(transcript_chunk, translated):
                    item["start"] = original.get("start")
                    item["end"] = original.get("end")
                    item["text"] = _strip_forbidden_punctuation(item.get("text", ""))

                _log(status_msg, f"Success with {model_name}.")
                return translated, current_key_index

            except Exception as exc:
                last_error = exc
                error_text = str(exc).lower()

                if "429" in error_text or "resource_exhausted" in error_text or "quota" in error_text:
                    current_key_index = (current_key_index + 1) % len(api_keys)
                    _log(status_msg, f"⚠️ کلیلەکە ماندوو بوو. گۆڕدرا بۆ کلیلی ژمارە {current_key_index + 1}...")
                    time.sleep(1.5)
                    continue 

                if "503" in error_text or "unavailable" in error_text or "overloaded" in error_text:
                    # بەکارهێنانی فۆرمولەی داینامیکی بیرکاری (Exponential Backoff with Jitter)
                    wait_time = min(MAX_WAIT_TIME, (2 ** attempt) + random.uniform(0.5, 2.0))
                    _log(status_msg, f"🔥 سێرڤەری {model_name} قەرەباڵغە! چاوەڕێ دەکەین بۆ {wait_time:.1f} چرکە...")
                    time.sleep(wait_time)
                    continue

                _log(status_msg, f"⏳ کێشەیەک ڕوویدا، دووبارە تاقیدەکەینەوە... (هەوڵی {attempt})")
                time.sleep(2)
                continue

        _log(status_msg, f"⚠️ مۆدێلی {model_name} وەڵامی نەدایەوە. دەچینە سەر مۆدێلی یەدەگ...")
        time.sleep(1)

    raise RuntimeError(f"All models exhausted. Last error: {last_error}")
