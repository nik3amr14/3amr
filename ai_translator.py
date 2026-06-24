import json
from typing import List, Dict, Any
from google import genai
from google.genai import types

def _clean_json(text: str) -> str:
    """Robust JSON extractor to prevent 503/Decode errors from Gemini."""
    try:
        # Find the first '[' and last ']'
        start = text.find('[')
        end = text.rfind(']')
        if start != -1 and end != -1:
            return text[start:end+1]
        
        # Fallback to '{' and '}' if the model returned an object instead of an array
        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1:
            return text[start:end+1]
            
        return text
    except Exception as e:
        return text

def translate_chunk(client: genai.Client, chunk: List[Dict[str, Any]], model_name: str) -> List[Dict[str, Any]]:
    """Translates a chunk of subtitles using Gemini with strict row count enforcement."""
    
    # Prepare input JSON
    input_data = [{"id": item["id"], "text": item["text"]} for item in chunk]
    input_json_str = json.dumps(input_data, ensure_ascii=False, indent=2)
    expected_rows = len(chunk)
    
    system_prompt = f"""You are an expert Kurdish Sorani (Sulaimani dialect) cinematic subtitle translator.
    
CRITICAL RULES:
1. STYLE: Natural, street-level, cinematic Sulaimani Sorani vocabulary. NEVER translate word-for-word.
2. NO CENSORSHIP: Swearing and insults must be translated with full intensity (e.g., خەبیس, گەدە, مادەرقاوە, گاو, ئەحمەق, کیر, کوس).
3. FORMATTING: Remove ALL punctuation marks (؛ : ! . ، ؟ ' " - _) and ignore honorifics (-san, -kun) in casual speech.
4. EMOTION: Preserve the exact emotional energy of the characters (angry, sad, sarcastic, shocked).
5. EXACT ROW COUNT: You MUST return exactly {expected_rows} rows. Do not merge, split, or skip any lines.
6. OUTPUT: Return ONLY a valid JSON array of objects. Each object must have "id" (matching the input) and "kurdish_text". Do not include markdown formatting like ```json.
"""

    prompt = f"{system_prompt}\n\nInput Subtitles:\n{input_json_str}"
    
    try:
        # Using response_mime_type to force JSON without strict schema to avoid 503 crashes
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.4
        )
        
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config
        )
        
        raw_text = response.text
        cleaned_json_str = _clean_json(raw_text)
        
        try:
            translated_data = json.loads(cleaned_json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Failed to parse JSON from Gemini. Raw output:\n{raw_text}\nError: {str(e)}")
            
        # Ensure it's a list
        if isinstance(translated_data, dict) and "subtitles" in translated_data:
            translated_data = translated_data["subtitles"]
        elif isinstance(translated_data, dict):
            translated_data = [translated_data]
            
        # EXACT ROW COUNT VALIDATION
        if len(translated_data) != expected_rows:
            raise ValueError(f"Row Mismatch Error: Expected {expected_rows} rows, but Gemini returned {len(translated_data)} rows.")
            
        # Map translations back to the chunk
        for i, item in enumerate(chunk):
            # Find matching ID or fallback to index
            match = next((x for x in translated_data if str(x.get("id")) == str(item["id"])), None)
            if match and "kurdish_text" in match:
                item["kurdish_text"] = match["kurdish_text"]
            else:
                item["kurdish_text"] = translated_data[i].get("kurdish_text", item["text"])
                
        return chunk

    except Exception as e:
        raise e
