"""
ai_translator.py — Kurdish Sorani Cinematic Subtitle Translator
Engine: Google Gemini only (gemini-3.5-flash / gemini-2.5-flash / gemini-2.5-flash-lite)
Features: Multi-key rotation | Aggressive fallback | Dynamic thinking budget
"""

import json
import re
import time
import random
import logging
from pydantic import BaseModel
from google import genai
from google.genai import types

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# VALID GEMINI MODELS (Updated June 2026 — all GA, no 404)
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_MODELS = [
    "gemini-3.5-flash",      # GA — best quality (default)
    "gemini-2.5-flash",      # GA — fast, very capable
    "gemini-2.5-flash-lite", # GA — ultra-fast, cheapest
]

# Fallback chain (best-first)
GEMINI_FALLBACKS = GEMINI_MODELS[:]

# Thinking budget options
THINKING_PRESETS = {
    "Ultra Fast (minimal)": 512,
    "Balanced (medium)":    4096,
    "Deep (high)":          16384,
    "Dynamic (بێ لیمیت)": -1,   # -1 = unlimited, let model decide
}

# ─────────────────────────────────────────────────────────────────────────────
# PYDANTIC SCHEMA — Strict JSON validation for Gemini structured output
# ─────────────────────────────────────────────────────────────────────────────
class SubtitleItem(BaseModel):
    start: float
    end:   float
    text:  str

class SubtitleResponse(BaseModel):
    translations: list[SubtitleItem]

# ─────────────────────────────────────────────────────────────────────────────
# CORE SYSTEM PROMPT — Kurdish Sorani Cinematic Rules
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an elite professional Kurdish Sorani cinematic subtitle translator.
Your objective is to translate the spoken text into natural, cinematic Kurdish Sorani.
Follow these rules strictly:

1. EXACT TRANSLATION (No Censorship): Translate exactly what the character says with 100%
   honesty. Do not soften or censor. If they swear or speak vulgarly, translate it with
   equal intensity in Kurdish Sorani.

2. AVOID overusing honorifics: Do NOT translate Japanese honorifics ("-san", "-kun") or
   English titles ("Mr.") literally as "کاک" or "خاتوون" unless it is a highly formal
   setting. In casual conversations, ignore them and use the character's name directly.

3. NO LITERAL TRANSLATION: Capture the true spoken meaning. Maintain natural Kurdish Sorani
   grammar (Subject-Object-Verb). Make it sound cinematic and natural.

4. TIMESTAMPS: The "start" and "end" keys must remain EXACTLY as provided. Never alter them.

5. NO PUNCTUATION: Completely strip all punctuation marks (؟ . ، ! : ؛ \" ' - _) from
   the Kurdish Sorani text output.

6. ROW ALIGNMENT: Translate EVERY single row. The output JSON array must have the EXACT
   same number of items as the input array. No rows may be added or removed.

Respond ONLY with a valid JSON object in this exact format:
{
  "translations": [
    {"start": <float>, "end": <float>, "text": "<Kurdish Sorani translation>"},
    ...
  ]
}
No markdown fences, no explanation, no preamble — pure JSON only."""


# ─────────────────────────────────────────────────────────────────────────────
# PRIVATE HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _clean_json(raw: str) -> str:
    """Strip markdown fences and extra whitespace from LLM output."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _validate(data: dict, expected: int) -> list:
    """Validate parsed translations and check row count."""
    rows = data.get("translations", [])
    if len(rows) != expected:
        raise ValueError(
            f"Row count mismatch: expected {expected}, got {len(rows)}"
        )
    return rows


def _user_prompt(chunk: list) -> str:
    """Build the user-facing translation prompt."""
    items = [
        {"start": seg["start"], "end": seg["end"], "text": seg["text"]}
        for seg in chunk
    ]
    return (
        f"Translate the following {len(items)} subtitle segments to Kurdish Sorani.\n\n"
        f"INPUT:\n{json.dumps(items, ensure_ascii=False, indent=2)}"
    )


def _jitter_backoff(attempt: int) -> float:
    """Exponential backoff with jitter, capped at 15 s."""
    return min(15.0, (2 ** attempt) + random.uniform(0.5, 2.0))


def _log(status_msg, msg: str):
    """Log to both Python logger and Streamlit status widget."""
    log.info(msg)
    if status_msg is not None:
        try:
            status_msg.info(msg)
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _call_gemini(
    gemini_keys: list,
    cur_idx: int,
    chunk: list,
    thinking_budget: int | None,
    selected_model: str,
    status_msg,
) -> tuple[list, int]:
    """
    Try every Gemini model in the fallback chain with up to 10 retries each.

    Returns:
        (translations_list, updated_key_index)

    Raises:
        RuntimeError if all models and retries are exhausted.
    """
    if not gemini_keys:
        raise RuntimeError("No Gemini API keys provided.")

    # Put user-selected model first, then fallbacks
    models = [selected_model] + [m for m in GEMINI_FALLBACKS if m != selected_model]
    prompt  = _user_prompt(chunk)
    full_p  = SYSTEM_PROMPT + "\n\n" + prompt
    expected = len(chunk)

    for model in models:
        _log(status_msg, f"[Gemini] Trying model: {model}")

        for attempt in range(10):
            api_key = gemini_keys[cur_idx % len(gemini_keys)]
            try:
                client = genai.Client(api_key=api_key)

                # Build generation config
                cfg: dict = {
                    "temperature":       0.75,
                    "response_mime_type": "application/json",
                    "response_schema":    SubtitleResponse,
                }

                # Thinking budget: -1 = dynamic (unlimited), 0 = off, N = token cap
                if thinking_budget is not None:
                    if thinking_budget == -1:
                        # Let model choose its own budget (dynamic)
                        cfg["thinking_config"] = types.ThinkingConfig(
                            thinking_budget=-1
                        )
                    elif thinking_budget > 0:
                        cfg["thinking_config"] = types.ThinkingConfig(
                            thinking_budget=thinking_budget
                        )
                    # thinking_budget == 0 → no thinking_config (ultra-fast)

                response = client.models.generate_content(
                    model=model,
                    contents=[
                        types.Content(
                            role="user",
                            parts=[types.Part(text=full_p)],
                        )
                    ],
                    config=types.GenerateContentConfig(**cfg),
                )

                raw    = _clean_json(response.text or "")
                data   = json.loads(raw)
                rows   = _validate(data, expected)
                _log(status_msg,
                     f"[Gemini] ✅ {model} — translated {expected} rows.")
                return rows, cur_idx

            except Exception as exc:
                s = str(exc)

                if "429" in s or "quota" in s.lower() or "rate" in s.lower():
                    # Quota hit → rotate key, short wait
                    cur_idx = (cur_idx + 1) % max(len(gemini_keys), 1)
                    _log(status_msg,
                         f"[Gemini] 429 on {model} (attempt {attempt+1}) "
                         f"— rotating key, wait 1.5 s")
                    time.sleep(1.5)

                elif "404" in s or "not found" in s.lower():
                    # Model doesn't exist → skip immediately
                    _log(status_msg,
                         f"[Gemini] 404 on {model} — model not found, skipping.")
                    break

                else:
                    # Generic / 503 → exponential backoff
                    wait = _jitter_backoff(attempt)
                    _log(status_msg,
                         f"[Gemini] Error on {model} (attempt {attempt+1}): "
                         f"{s[:120]} | waiting {wait:.1f} s")
                    time.sleep(wait)

    raise RuntimeError("All Gemini models and retries exhausted.")


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────
def ai_translate(
    gemini_keys: list,
    cur_gem_idx: int,
    transcript_chunk: list,
    thinking_budget: int | None,
    selected_model: str,
    status_msg,
) -> tuple[list, int]:
    """
    Translate a chunk of subtitle segments to Kurdish Sorani using Google Gemini.

    Args:
        gemini_keys:      List of Gemini API key strings (at least one required)
        cur_gem_idx:      Current key rotation index
        transcript_chunk: List of dicts: [{start, end, text}, ...]
        thinking_budget:  -1 = dynamic, 0 = off, N = token cap, None = off
        selected_model:   Model string chosen in the UI
        status_msg:       Streamlit status widget or None

    Returns:
        (translations_list, updated_gem_idx)
    """
    if not transcript_chunk:
        return [], cur_gem_idx

    translations, new_idx = _call_gemini(
        gemini_keys, cur_gem_idx, transcript_chunk,
        thinking_budget, selected_model, status_msg,
    )
    return translations, new_idx
