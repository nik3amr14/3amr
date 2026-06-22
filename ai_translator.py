import google.generativeai as genai
import re
import time


def translate_to_kurdish_sorani(
    subtitles: list[dict],
    api_key: str,
    model_name: str = "gemini-1.5-flash",
    thinking_level: str = "standard",
) -> list[dict]:
    """
    وەرگێڕانی ژێرنووسەکان بۆ کوردی سۆرانی بە Gemini API.
    subtitles: لیستێک لە dict { index, start, end, text }
    Returns: هەمان لیست لەگەڵ وەرگێڕان لە 'translated' key.
    """
    genai.configure(api_key=api_key)

    generation_config = genai.types.GenerationConfig(
        temperature=0.2 if thinking_level == "standard" else 0.7,
    )

    model = genai.GenerativeModel(
        model_name=model_name,
        generation_config=generation_config,
    )

    SYSTEM_PROMPT = (
        "تۆ وەرگێڕێکی پسپۆڕی ئەنیمە و فیلمی کوردی سۆرانییت کە سالیانی زۆری تەجروبەت هەیە. "
        "ئەرکت ئەوەیە کە ژێرنووسەکان بگەیەنیت بۆ کوردی سۆرانی بەو شێوەیەی کە "
        "کەسێکی کوردزمانی ئاسایی خۆی بەم شێوەیە قسەی بکات — سادە، ڕوون، و سروشتی. "
        "\n\n"
        "دەبێت ئەم ڕێسانە بەتەواوی بیپارێزیت:\n"
        "١. واتا و هەستی قسەکان بگەیەنە — نەک وشە بە وشە. "
        "ئەگەر پێکەنین هەیە پێکەنین بگەیەنە، ئەگەر تووڕەیی هەیە تووڕەیی بگەیەنە، "
        "ئەگەر ئازای هەیە ئازای بگەیەنە.\n"
        "٢. وشەکان دەبێت سادە و ڕوون بێت — وشەی قورس و کتێبانە بەکار مەیەنێت. "
        "بە ئەو شێوەیە بنووسە کە منداڵ و گەنج و پیرەکیش تێبگات.\n"
        "٣. ئەگەر دەقەکە 'God' یان 'Lord' یان 'خدا' یان 'الله' یان 'رب' بوو، "
        "دەبێت بنووسیت 'فەرمانڕەوا'.\n"
        "٤. ناوی کەس و شوێن وەک خۆیان بێهێشتەوە، تەنها وەرگێڕی دەوروبەریان بکە.\n"
        "٥. گۆرانی: نیشانەی ♪ بێهێشتەوە و واتاکەی بگەیەنە بە شێوەیەکی شیعرانە.\n"
        "٦. هیچ ڕیزێک بەبێ وەرگێڕان مەیەڵێت — هەموو ڕیزێک دەبێت وەرگێڕان بکرێت.\n"
        "٧. بە هیچ شێوەیەک بە عەرەبی، فارسی، یان ئینگلیزی وەڵام مەدەرەوە.\n"
        "٨. هیچ خاڵ (.) یان بەند (،) یان نیشانەی خستەسەرەوە (! ؟ : ؛ - …) بەکار مەیەنێت.\n"
        "تەنها وەرگێڕانەکە بنووسە بەبێ هیچ ڕوونکردنەوە یان زیادەکاری."
    )

    results = []
    batch_size = 50   # زیادکرا لە 30 بۆ 50

    for i in range(0, len(subtitles), batch_size):
        batch = subtitles[i : i + batch_size]
        batch_text = "\n".join(
            [f"{item['index']}|{item['text']}" for item in batch]
        )

        prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            "تەنها وەرگێڕانەکە بنووسە بە فۆرماتی:\n"
            "ژمارە|وەرگێڕان\n\n"
            f"تێکستەکان:\n{batch_text}"
        )

        translated_batch = _translate_with_retry(model, prompt, batch)
        results.extend(translated_batch)

        if i + batch_size < len(subtitles):
            time.sleep(0.2)   # کەمکرا لە 0.5 بۆ 0.2

    return results


def _translate_with_retry(model, prompt: str, batch: list[dict], max_retries: int = 3) -> list[dict]:
    """
    Split-Retry لۆژیک: هەوڵدەدات وەرگێڕان بکات، ئەگەر هەڵە هات دووبارە هەوڵ دەداتەوە.
    """
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            raw = response.text.strip()
            parsed = _parse_translation_response(raw, batch)

            # دابینکردنی هەموو ڕیزەکان
            if len(parsed) == len(batch):
                return parsed

            # Split-Retry: ئەگەر ژمارەی ڕیزەکان کەمتر بوو
            if attempt < max_retries - 1:
                time.sleep(1)
                continue

            # کۆتایی: پڕکردنەوەی ڕیزە کەمبووەکان بە تێکستی ئەسڵی
            return _fill_missing(parsed, batch)

        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            # کۆتاییەکەی هەڵە: بەکارهێنانی تێکستی ئەسڵی
            return [dict(item, translated=item["text"]) for item in batch]

    return [dict(item, translated=item["text"]) for item in batch]


def _remove_punctuation(text: str) -> str:
    """
    سڕینەوەی هەموو خاڵ و بەند و نیشانەی خستەسەرەوە لە تێکستی وەرگێڕاودا.
    ♪ و ♫ بەجێدەمێنێت چونکە بۆ گۆرانی پێویستن.
    """
    return re.sub(r"[.،,!؟?:؛;\-–—…\"\'\"\"'']", "", text).strip()


def _parse_translation_response(raw: str, batch: list[dict]) -> list[dict]:
    """
    پارسکردنی وەڵامی Gemini بۆ دەرخستنی ژمارە و وەرگێڕان.
    """
    lines = raw.strip().splitlines()
    translation_map = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # فۆرمات: ژمارە|وەرگێڕان
        match = re.match(r"^(\d+)\|(.+)$", line)
        if match:
            idx = int(match.group(1))
            text = _remove_punctuation(match.group(2).strip())
            translation_map[idx] = text

    result = []
    for item in batch:
        idx = item["index"]
        translated = translation_map.get(idx, item["text"])
        result.append(dict(item, translated=translated))

    return result


def _fill_missing(parsed: list[dict], batch: list[dict]) -> list[dict]:
    """
    پڕکردنەوەی ڕیزە کەمبووەکان بە تێکستی ئەسڵی.
    """
    parsed_map = {item["index"]: item for item in parsed}
    result = []
    for item in batch:
        if item["index"] in parsed_map:
            result.append(parsed_map[item["index"]])
        else:
            result.append(dict(item, translated=item["text"]))
    return result
