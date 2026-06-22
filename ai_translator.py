import google.generativeai as genai
import re
import time

def translate_to_kurdish_sorani(
    subtitles: list[dict],
    api_keys: list,
    model_name: str = "gemini-1.5-flash",
    thinking_level: str = "standard",
) -> list[dict]:
    """
    وەرگێڕدانی ژێرنووسەکان بۆ کوردی سۆرانی بە 5 کلیل
    subtitles: dict { index, start, end, text }
    Returns: هەمان لیست لەگەڵ وەرگێڕدان لە "translated"
    """

    SYSTEM_PROMPT = (
        "ډېټۍ پېسپۍ دەنېمە و فيلمۍ کوردی سۆرانېيټ کە ساليانۍ زۆری تەجروبەت هەيە "
        "\n\n"
        "دەبێت ئەم ڕێسانە بەتەواوی بپارێزیت:\n"
        "1. واتا و هەستی قەسەکان بگەیەنە – نەک وشە بە وشە. ئەگەر پێکەنین هەیە پێکەنین بگەیەنە، ئەگەر تووڕەیی هەیە تووڕەیی بگەیەنە"
        "\n"
        "2. وشەکان دەبێت سادە و ڕوون بێت – وشەی قورس و کتێبانە بەکار مەینێت. بە ئەو شێوەیە بنووسە کە منداڵ و گەنج و پیرەکیش تێبگات"
        "\n"
        "3. ئەگەر دەقەکە 'Lord' یان 'God' بوو، دەبێت بنووسیت 'فەرمانڕەوا'"
        "\n"
        "4. ناوی کەس و شوێن وەک خۆیان بەهێشەوە، تەنها وەرگێڕی دەوروبەریان بکە"
        "\n"
        "5. گۆرانی: نیشانەی 🎵 بەهێشەوە و واتاکەی بگەیەنە بە شێوەیەکی شیعرانە"
        "\n"
        "6. هیچ ڕیزێک بەبێ وەرگێڕان مەیدەڵێت – هەموو ڕیزێک دەبێت وەرگێڕان بکرێت"
        "\n"
        "7. بە هیچ شێوەیەک بە عەرەبی، فارسی، یان ئینگلیزی وەڵام مەدەرەوە. خاڵ (.) یان بەند (،) یان نیشانەی خستەڕووە (؟ ! . - : ، ) بەکار مەهێنێت. تەنها وەرگێڕانەکە بنووسە بەبێ هیچ ڕوونکردنەوە یان زیادەکار."
    )

    results = []
    batch_size = 50

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

        translated_batch = _translate_batch_with_keys(
            prompt, batch, api_keys, model_name, thinking_level
        )
        results.extend(translated_batch)

        if i + batch_size < len(subtitles):
            time.sleep(0.2)

    return results


def _translate_batch_with_keys(prompt, batch, api_keys, model_name, thinking_level, max_retries: int = 3):
    """
    هەوڵداندن بە پێنج کلیل بۆ وەرگێڕانی بەشێک. 
    ئەگەر کلیلێک شکست هێنا، دەچێتە سەر کلیلی داهاتوو.
    """
    for attempt in range(max_retries):
        for api_key in api_keys:
            try:
                genai.configure(api_key=api_key)
                generation_config = genai.types.GenerationConfig(
                    temperature=0.2 if thinking_level == "standard" else 0.7,
                )
                model = genai.GenerativeModel(
                    model_name=model_name,
                    generation_config=generation_config,
                )
                response = model.generate_content(prompt)
                raw = response.text.strip()
                parsed = _parse_translation_response(raw, batch)
                
                # دەستنیشانکردنی هەموو ڕیزەکان
                if len(parsed) == len(batch):
                    return parsed

            except Exception:
                continue # ڕوو لە کلیلی داهاتوو بکە

        # ئەگەر هەر 5 کلیلەکە شکستیان هێنا، کەمێک چاوەڕوان بکە و هەوڵبدەرەوە
        time.sleep(2 * (attempt + 1))

    # کۆتایی: ئەگەر هەموو کلیلەکان شکستیان هێنا، دەقی ڕەسەن بەکاربهێنە
    return _fill_missing([], batch)


def _remove_punctuation(text: str) -> str:
    """
    سڕینەوەی هەموو خاڵ و بەند و نیشانەی خستەڕوو لە تێکستی وەرگێڕدراو!
    وە بەجێهێشتنی ڕێزەکان لە گۆرانی پێویستن.
    """
    return re.sub(r"[.,!?:;—-—\"''`()]", "", text).strip()


def _parse_translation_response(raw: str, batch: list[dict]) -> list[dict]:
    """
    پارسکردنی ژمارە و وەرگێڕان لە دەرئەنجامی Gemini بۆ ناو لیست.
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
    پڕکردنەوەی ڕیزە کەمبووەکان بە تێکستی ڕەسەلی.
    """
    parsed_map = {item["index"]: item for item in parsed}
    result = []
    for item in batch:
        if item["index"] in parsed_map:
            result.append(parsed_map[item["index"]])
        else:
            result.append(dict(item, translated=item["text"]))
    return result
