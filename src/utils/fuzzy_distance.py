import re
from rapidfuzz import fuzz

def clean_text(text: str) -> str:
    cleaned = re.sub('[|\\-\\n]', ' ', text)
    return re.sub('\\s+', ' ', cleaned).strip()

def fuzzy_distance(text1: str, text2: str) -> float:
    cleaned1 = clean_text(text1)
    cleaned2 = clean_text(text2)
    return fuzz.token_set_ratio(cleaned1, cleaned2)