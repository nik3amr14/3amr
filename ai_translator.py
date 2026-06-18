"""
ai_translator.py
=================

Standalone translation module for the Kurdish Sorani Cinematic Subtitle
Generator.

This module owns ALL Gemini API interaction for subtitle translation:
  - the cinematic Kurdish Sorani system prompt
  - calling the Gemini API with model + API-key fallback ("aggressive forcing")
  - building the correct ThinkingConfig per model family
  - robustly parsing the raw JSON array Gemini returns

No other concern (UI, FFmpeg, file I/O) lives in this file.
"""

import json
import re
import time

from google import genai
from google.genai import types


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

MAX_ATTEMPTS_PER_MODEL = 10
QUOTA_WAIT_SECONDS = 2
OVERLOAD_WAIT_SECONDS = 4
GENERIC_ERROR_WAIT_SECONDS = 1

# Fixed fallback chain. "gemini-3.1-flash-lite" is intentionally excluded
# here per spec -- it only ever appears in models_to_try if the caller
# explicitly passed it in as `selected_model`.
FALLBACK_MODELS = ["gemini-3.5-flash", "gemini-2.5-flash", "gemini-3-flash-preview"]

# Characters that must never appear in translated Kurdish Sorani text.
FORBIDDEN_PUNCTUATION = "؟.،!:؛\"'-_"


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are an elite professional Kurdish Sorani cinematic subtitle translator working on film and television content.

You will be given a JSON array of transcript rows. Each row has:
  - "start": a timestamp value, do not alter
  - "end": a timestamp value, do not alter
  - "text": the original spoken text to translate

Follow these rules with no exceptions:

1. CINEMATIC & NATURAL MEANING
   - Never translate word-for-word or literally.
   - Capture the true meaning, tone, and emotion of the character.
   - Write the line the way a native Kurdish Sorani speaker would naturally
     say it in a film, as if the character were originally Kurdish.
   - Prefer natural, spoken, cinematic phrasing over stiff or formal language.

2. TIMESTAMPS
   - Return "start" and "end" EXACTLY as given in the input, unchanged.
   - Never modify, round, reformat, or omit these keys.

3. NO PUNCTUATION
   - The translated "text" must NOT contain any of these characters:
     ؟ . ، ! : ؛ " ' - _
   - Strip all punctuation completely. Output clean words only.

4. ROW ALIGNMENT
   - The output array must contain EXACTLY the same number of objects as the
     input array.
   - Never skip, merge, or drop any row -- including whispers, breathing, or
     other non-verbal sounds.
   - Every input row must produce exactly one output row, in the same order.

5. OUTPUT FORMAT
   - Return ONLY a raw JSON array of objects.
   - Each object must contain exactly the keys: "start", "end", "text".
   - Do NOT include markdown code fences, explanations, or any text before or
     after the JSON array."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_json(text: str) -> list:
    """
    Robustly extract a JSON array from a raw Gemini response.

    Handles markdown code fences, leading/trailing commentary, and trailing
    commas -- the common ways model output deviates from strict JSON.
    """
    if not text or not text.strip():
        raise ValueError("Empty response text -- nothing to parse.")

    cleaned = text.strip()

    # Strip a single pair of markdown code fences if present, e.g. ```json ... ```
    fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    # Isolate the outermost JSON array boundaries.
    start = cleaned.find("[")
    end = cleaned.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON array found in response text.")
    cleaned = cleaned[start:end + 1]

    # Remove trailing commas before a closing bracket/brace (common model slip).
    cleaned = re.sub(r",\s*([\]}])", r"\1", cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON after cleaning: {exc}") from exc

    if not isinstance(data, list):
        raise ValueError("Parsed JSON is not an array.")

    return data


def _strip_forbidden_punctuation(text) -> str:
    """Force-strip forbidden punctuation as a defensive second layer."""
    if not isinstance(text, str):
        return text
    for ch in FORBIDDEN_PUNCTUATION:
        text = text.replace(ch, "")
    return text


def _log(status_msg, message: str) -> None:
    """Write a status update if status_msg supports it (e.g. an st.status() box)."""
    if status_msg is None:
        return
    if hasattr(status_msg, "write"):
        status_msg.write(message)
    elif callable(status_msg):
        status_msg(message)


def _build_model_list(selected_model: str) -> list:
    """selected_model first, then the fixed fallback chain, deduplicated."""
    models_to_try = [selected_model]
    for model_name in FALLBACK_MODELS:
        if model_name not in models_to_try:
            models_to_try.append(model_name)
    return models_to_try


def _build_generation_config(thinking_budget, model_name: str):
    """Build the GenerateContentConfig for a given model + thinking budget."""
    if thinking_budget == 0:
        return types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.8,
        )

    if "gemini-3" in model_name:
        # Gemini 3 family uses a qualitative thinking level, e.g. "low"/"medium"/"high".
        thinking_config = types.ThinkingConfig(thinking_level=thinking_budget)
    else:
        # Gemini 2 family uses a numeric thinking token budget, e.g. 2048.
        thinking_config = types.ThinkingConfig(thinking_budget=thinking_budget)

    return types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        temperature=0.8,
        thinking_config=thinking_config,
    )


def _build_user_prompt(transcript_chunk: list) -> str:
    chunk_json = json.dumps(transcript_chunk, ensure_ascii=False, indent=2)
    return (
        "Translate every row below into Kurdish Sorani, following all system "
        "rules exactly. Input transcript rows (JSON array):\n\n"
        f"{chunk_json}"
    )


def _is_quota_exhausted(error_text: str) -> bool:
    return "429" in error_text or "RESOURCE_EXHAUSTED" in error_text.upper()


def _is_overloaded(error_text: str) -> bool:
    upper = error_text.upper()
    return "503" in error_text or "UNAVAILABLE" in upper or "overloaded" in error_text.lower()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def gemini_translate(api_keys: list, current_key_index: int, transcript_chunk: list, thinking_budget: int, selected_model: str, status_msg) -> tuple[list, int]:
    """
    Translate a chunk of transcript rows into Kurdish Sorani using Gemini,
    with aggressive model + API-key fallback.

    Returns:
        (translated_chunk, updated_key_index)
    """
    if not api_keys:
        raise ValueError("api_keys is empty -- at least one Gemini API key is required.")

    if not transcript_chunk:
        return [], current_key_index

    models_to_try = _build_model_list(selected_model)
    user_prompt = _build_user_prompt(transcript_chunk)

    last_error = None

    for model_name in models_to_try:
        generation_config = _build_generation_config(thinking_budget, model_name)

        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            api_key = api_keys[current_key_index % len(api_keys)]

            try:
                client = genai.Client(api_key=api_key)

                _log(
                    status_msg,
                    f"Translating with {model_name} (attempt {attempt}/{MAX_ATTEMPTS_PER_MODEL})...",
                )

                response = client.models.generate_content(
                    model=model_name,
                    contents=user_prompt,
                    config=generation_config,
                )

                translated = extract_json(response.text or "")

                if len(translated) != len(transcript_chunk):
                    raise ValueError(
                        f"Row mismatch: expected {len(transcript_chunk)} rows, "
                        f"got {len(translated)}."
                    )

                # Force-correct timestamps and punctuation as a final safety
                # net, regardless of what the model actually returned.
                for original, item in zip(transcript_chunk, translated):
                    item["start"] = original.get("start")
                    item["end"] = original.get("end")
                    item["text"] = _strip_forbidden_punctuation(item.get("text", ""))

                _log(status_msg, f"Success with {model_name}.")
                return translated, current_key_index

            except Exception as exc:  # noqa: BLE001 -- SDK error types vary by version
                last_error = exc
                error_text = str(exc)

                if _is_quota_exhausted(error_text):
                    current_key_index = (current_key_index + 1) % len(api_keys)
                    _log(
                        status_msg,
                        f"Quota hit on {model_name}. Rotated to key index "
                        f"{current_key_index}. Waiting {QUOTA_WAIT_SECONDS}s...",
                    )
                    time.sleep(QUOTA_WAIT_SECONDS)
                    continue

                if _is_overloaded(error_text):
                    _log(
                        status_msg,
                        f"{model_name} overloaded (attempt {attempt}/"
                        f"{MAX_ATTEMPTS_PER_MODEL}). Staying on model, waiting "
                        f"{OVERLOAD_WAIT_SECONDS}s...",
                    )
                    time.sleep(OVERLOAD_WAIT_SECONDS)
                    continue

                # Unrecognized error: brief backoff, still retry the same model.
                _log(
                    status_msg,
                    f"Error on {model_name} (attempt {attempt}/"
                    f"{MAX_ATTEMPTS_PER_MODEL}): {error_text[:200]}",
                )
                time.sleep(GENERIC_ERROR_WAIT_SECONDS)
                continue

        _log(status_msg, f"{model_name} failed {MAX_ATTEMPTS_PER_MODEL} times. Trying next model...")

    raise RuntimeError(f"All models exhausted: {models_to_try}. Last error: {last_error}")
