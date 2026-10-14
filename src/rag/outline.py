import json
import re
from pathlib import Path

from openai import OpenAI

from src.rag import build_kg as B

_NOISE_HEADING = re.compile(
    r"^\s*(table of contents|contents|about the authors?|about the technical reviewers?"
    r"|acknowledgments?|acknowledgements?|index|copyright|dedication|preface|foreword"
    r"|list of (figures|tables)|bibliography|references|glossary|introduction)\s*$",
    re.IGNORECASE,
)


def _extract_toc(content_list_path: str, drop_frontmatter: bool = False) -> list[dict]:
    items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    toc: list[dict] = []
    seen = set()
    for it in items:
        if it.get("type") != "text" or not it.get("text_level"):
            continue
        title = re.sub(r"\s+", " ", (it.get("text") or "")).strip()
        if len(title) < 3:
            continue
        if drop_frontmatter and _NOISE_HEADING.match(title):
            continue
        key = (int(it["text_level"]), title.lower())
        if key in seen:
            continue
        seen.add(key)
        page = it.get("page_idx", 0)
        toc.append({"level": int(it["text_level"]),
                    "title": title[:140],
                    "page": int(page) if str(page).isdigit() else 0})
    return toc


def _extract_printed_toc(content_list_path: str) -> str:
    items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    blocks: list[tuple[int, str]] = []
    for it in items:
        if it.get("type") != "text":
            continue
        t = it.get("text") or ""
        leader_lines = len(re.findall(r"\.{2,}.*?\d+\s*$", t, flags=re.MULTILINE))
        if re.search(r"Chapter\s+\d+\s*:", t) or leader_lines >= 3:
            page = it.get("page_idx", 0)
            blocks.append((int(page) if str(page).isdigit() else 0, t))
    if not blocks:
        return ""
    blocks.sort(key=lambda b: b[0])
    raw = "\n".join(t for _, t in blocks)
    raw = re.sub(r"\.{2,}\s*\.?", " .. ", raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    return raw.strip()


def _target_slides(toc: list[dict], total_tokens: int) -> int:
    n_l1 = sum(1 for h in toc if h["level"] == 1)
    n_l2 = sum(1 for h in toc if h["level"] >= 2)
    by_structure = round(n_l1 * 2 + n_l2 / 12)
    by_tokens = round(total_tokens / 2500)
    target = max(by_structure, by_tokens) if total_tokens else by_structure
    return max(5, min(target, 60))


async def _kg_themes(rag) -> str:
    try:
        return await rag.aquery(
            "List the 12-18 most important high-level themes/topics of this entire document, "
            "as a short comma-separated list. Only the list.",
            mode="global",
        )
    except Exception as e:
        return f"(KG themes unavailable: {e})"


async def _kg_structure(rag) -> str:
    try:
        return await rag.aquery(
            "This document has no table of contents. Based on the whole knowledge graph, produce a "
            "structured overview: list the main topics of the document, and under each list its key "
            "sub-points, as a nested markdown bullet list. Cover the whole document.",
            mode="global",
        )
    except Exception as e:
        return f"(KG structure unavailable: {e})"


def _synthesize(structure_text: str, is_printed_toc: bool, themes: str, target: int, model: str) -> str:
    client = OpenAI(api_key=B.OPENAI_API_KEY, base_url=B.LLM_BASE_URL)
    src_label = (
        "the book's PRINTED TABLE OF CONTENTS (authoritative: has 'Chapter N:' markers, section "
        "titles and page numbers — use the chapter numbers and page-number ordering to reconstruct "
        "the correct 3-level hierarchy of chapter → section → sub-section)"
        if is_printed_toc else
        "the document's reconstructed heading tree (2 levels only — infer deeper nesting where sensible)"
    )
    prompt = (
        "You are an expert lecture planner. Below is " + src_label + ", plus the most important "
        "themes extracted from its knowledge graph.\n\n"
        f"# DOCUMENT STRUCTURE\n{structure_text}\n\n"
        f"# KEY THEMES (from knowledge graph — use to judge importance)\n{themes}\n\n"
        f"# TASK\nProduce a COMPREHENSIVE hierarchical lecture outline that COVERS every major topic "
        f"in the document's logical order. Aim for about {target} leaf slides.\n"
        "Rules:\n"
        "- Preserve the real chapter titles EXACTLY (e.g. 'Chapter 1: Introduction to Apache Spark', "
        "not just 'Introduction'). Put each chapter's sections UNDER it, and sub-sections under their "
        "section — respect the true hierarchy, do not flatten.\n"
        "- Cover ALL chapters/sections — do not skip whole topics. Merge only exact duplicates.\n"
        "- KEEP front matter (title/cover, About the Author(s), Introduction) and back matter if present.\n"
        "- Prioritise depth on the KEY THEMES; keep minor topics as single lines.\n"
        "- Markdown headings by depth: '# ' chapter, '## ' section, '### ' sub-section. Strip page "
        "numbers and dot leaders. Titles short & specific, same language as the document. No commentary.\n"
    )
    resp = client.chat.completions.create(
        model=model, temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content.strip()
