import re
from typing import Iterable


def dominant_script(text: str) -> str:
    sample = (text or "").strip()
    if not sample:
        return "unknown"
    counts = {
        "latin": len(re.findall(r"[A-Za-zÀ-ỹ]", sample)),
        "cjk": len(re.findall(r"[\u3400-\u9fff]", sample)),
        "hangul": len(re.findall(r"[\uac00-\ud7af]", sample)),
        "kana": len(re.findall(r"[\u3040-\u30ff]", sample)),
        "thai": len(re.findall(r"[\u0E00-\u0E7F]", sample)),
        "cyrillic": len(re.findall(r"[\u0400-\u04FF]", sample)),
        "arabic": len(re.findall(r"[\u0600-\u06FF]", sample)),
    }
    script, score = max(counts.items(), key=lambda item: item[1])
    return script if score > 0 else "unknown"


def detect_language_code(text: str) -> str:
    return dominant_script(text)


def detect_language_from_parts(parts: Iterable[str]) -> str:
    return dominant_script(" ".join(part for part in parts if part))
