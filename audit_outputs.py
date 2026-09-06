import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LECTURES_DIR = ROOT / "data" / "lectures"
CONTEXT_DIR = ROOT / "data" / "context"

BAD_RE = re.compile(r"key\s*words?|target\s+audience|cite\s+this\s+document|last\s+retrieved|references\s*$|figure:|\*\*", re.I)
STOP = set(
    "the and for with from this that into using through about include includes including based project projects content slide knowledge technology public science product data open education resources resource research".split()
)


def tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-zA-ZÀ-ỹ0-9]{4,}", text.lower()) if token not in STOP}


def flatten(slides: list[dict]) -> str:
    return "\n".join(" ".join(slide.get("content", [])) for slide in slides)


def pairwise_repetition(slides: list[dict]) -> tuple[float, tuple]:
    rows = []
    for slide in slides:
        rows.append((slide["slide"]["slide_number"], slide["slide"]["slide_title"], tokens(" ".join(slide.get("content", [])))))
    values = []
    max_pair = (0.0, None)
    for i in range(len(rows)):
        for j in range(i + 1, len(rows)):
            left = rows[i][2]
            right = rows[j][2]
            score = len(left & right) / len(left | right) if left | right else 0.0
            values.append(score)
            if score > max_pair[0]:
                max_pair = (score, (rows[i][0], rows[j][0], rows[i][1], rows[j][1]))
    return (sum(values) / len(values) if values else 0.0), max_pair


def numbered_coverage(doc_id: str, slides: list[dict]) -> tuple[int, int, list[int]] | None:
    context_path = CONTEXT_DIR / f"{doc_id}.json"
    if not context_path.exists():
        return None
    context = json.loads(context_path.read_text(encoding="utf-8"))
    markdown = context.get("text_content", {}).get("markdown", "")
    numbers = {int(number) for number in re.findall(r"(?:^|\n)\s*(\d{1,2})\.\s+", markdown)}
    if len(numbers) < 5:
        return None
    output = flatten(slides).lower()
    hits = {number for number in numbers if re.search(rf"\b(?:principle|step|item)?\s*{number}\b", output)}
    return len(hits), len(numbers), sorted(numbers - hits)


def source_overlap(doc_id: str, slides: list[dict]) -> float:
    context_path = CONTEXT_DIR / f"{doc_id}.json"
    if not context_path.exists():
        return 0.0
    context = json.loads(context_path.read_text(encoding="utf-8"))
    source = tokens(context.get("text_content", {}).get("markdown", ""))
    output = tokens(flatten(slides))
    return len(source & output) / len(output) if output else 0.0


def audit_doc(path: Path) -> dict:
    doc_id = path.parent.name
    data = json.loads(path.read_text(encoding="utf-8"))
    slides = data.get("slides", [])
    bullet_counts = [len(slide.get("content", [])) for slide in slides]
    noise = []
    for slide in slides:
        for bullet in slide.get("content", []):
            if BAD_RE.search(bullet):
                noise.append({"slide": slide["slide"]["slide_number"], "text": bullet[:120]})
    avg_rep, max_rep = pairwise_repetition(slides)
    numbered = numbered_coverage(doc_id, slides)
    warnings = []
    if any(count == 0 for count in bullet_counts):
        warnings.append("empty_slide")
    if any(count < 3 for count in bullet_counts):
        warnings.append("too_few_bullets")
    if noise:
        warnings.append("metadata_noise")
    if max_rep[0] >= 0.38:
        warnings.append("high_repetition")
    if numbered and numbered[1] and numbered[0] / numbered[1] < 0.75:
        warnings.append("low_numbered_coverage")
    return {
        "doc_id": doc_id,
        "slides": len(slides),
        "bullet_counts": bullet_counts,
        "source_overlap": round(source_overlap(doc_id, slides), 3),
        "avg_repetition": round(avg_rep, 3),
        "max_repetition": round(max_rep[0], 3),
        "max_repetition_pair": max_rep[1],
        "noise_count": len(noise),
        "numbered_coverage": numbered,
        "warnings": warnings,
    }


def main() -> None:
    results = []
    for path in sorted(LECTURES_DIR.glob("*/*.json")):
        if path.name == f"{path.parent.name}.json":
            results.append(audit_doc(path))
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
