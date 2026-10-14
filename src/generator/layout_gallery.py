"""One sample slide per layout in LAYOUT_PURPOSE, at the layout's full declared capacity, exported to a PPTX and read
back with pptx_verify. It is the safety net for the layout catalogue: a layout whose data mapping or capacity is wrong
shows up here as missing text or an overflowing box, not in a real lecture."""
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from src.generator import layout_purpose as lp
from src.generator.layout_select import fits_spec
from src.generator.pptx_pipeline import export_native

_SENTENCE = "Each stage refines the previous estimate before the next one starts to work on it. "


def _text(chars: int, tag: str) -> str:
    body = (_SENTENCE * (chars // len(_SENTENCE) + 1))[: max(20, int(chars * 0.9))].rsplit(" ", 1)[0]
    return f"{tag}: {body}."


def _facts(blocks: List[Tuple[str, List[str]]]) -> Tuple[List[dict], List[dict]]:
    facts, groups = [], []
    for n, (title, texts) in enumerate(blocks):
        indices = []
        for k, text in enumerate(texts):
            indices.append(len(facts))
            facts.append({"fact_id": f"f{n}_{k}", "item_id": f"i{n}", "role": "main" if k == 0 else "support",
                          "concept": title, "kind": "explanation", "text": text, "source_owned": True,
                          "source_refs": [{"page": 1, "start": 10 * (len(facts) + 1), "quote": text}]})
        groups.append({"group_id": f"point-{n}", "title": title, "item_indices": indices})
    return facts, groups


def _fits(spec: lp.LayoutSpec, blocks: List[Tuple[str, List[str]]]) -> bool:
    groups = [{"title": t, "items": list(items)} for t, items in blocks]
    return fits_spec(spec.name, groups, [x for _, items in blocks for x in items])


def sample(spec: lp.LayoutSpec) -> Tuple[List[Tuple[str, List[str]]], dict]:
    """(blocks, structure) filling `spec` as far as the layout really fits: the most blocks, then the longest text,
    that its own capacity model accepts (the deck then proves the renderer agrees)."""
    if spec.typed == "comparison":
        entities = ["Method A", "Method B", "Method C"]
        criteria = ["How it trains", "Cost per step", "Best used when", "Main limit", "Typical output"]
        for rows in range(len(criteria), 1, -1):
            for chars in range(spec.body_chars, 30, -10):
                blocks = [(c, [_text(chars, f"{e} {k}") for k, e in enumerate(entities)]) for c in criteria[:rows]]
                if _fits(spec, blocks):
                    cells = [[f"f{r}_{c}" for c in range(3)] for r in range(rows)]
                    return blocks, {"entities": entities, "rows": [{"criterion": c, "cells": cs}
                                                                    for c, cs in zip(criteria, cells)]}
        raise ValueError("comparison sample does not fit")
    if spec.typed == "stats":
        k = min(spec.max_blocks, 4)
        blocks = [("Figures", [f"Images in set {i}: {70000 + i}." for i in range(k)])]
        return blocks, {"items": [{"fact_id": f"f0_{i}", "value": str(70000 + i), "label": "examples"} for i in range(k)]}
    if spec.typed == "quote":
        return [("Quote", ['As the author writes, "learning is the study of algorithms that improve with data".'])],             {"fact_id": "f0_0", "quote": "learning is the study of algorithms that improve with data", "attribution": "the author"}
    if spec.typed == "options":
        blocks = [(f"Option {i + 1}", [f"Option {i + 1} costs {10 * (i + 1)} units per run.", "It keeps every intermediate result."])
                  for i in range(4)]
        return blocks, {"cards": [{"name": f"Option {i + 1}", "value": f"{10 * (i + 1)} units"} for i in range(4)]}
    if spec.typed == "profile":
        texts = ["The set is a benchmark for digit recognition."] + [f"It has {10 * (i + 1)} items in group {i}." for i in range(5)]
        return [("Profile", texts)], {"lede": "f0_0", "pairs": [{"key": f"Group {i}", "value": f"{10 * (i + 1)} items", "fact_id": f"f0_{i + 1}"}
                                                                for i in range(5)]}
    if spec.typed == "questions":
        texts = ["What limits the accuracy of the model?"] + [f"Which data would reduce error {i}?" for i in range(4)]
        return [("Questions", texts)], {"main": "f0_0", "subs": [f"f0_{i}" for i in range(1, 5)]}
    per = min(spec.max_facts_per_block, 2)
    structure = {"center": "Central concept"} if spec.name == "hub_spoke_layout" else {}
    if spec.relation == "definition":
        return [("Concept", [_text(300, "Definition")])], structure
    if spec.unit == "facts":
        for k in range(min(spec.max_blocks, 5), spec.min_blocks - 1, -1):
            for chars in range(spec.body_chars, 30, -10):
                blocks = [("Main idea", [_text(chars, f"Point {i}") for i in range(k)])]
                if _fits(spec, blocks):
                    return blocks, structure
        return [("Main idea", [_text(60, f"Point {i}") for i in range(spec.min_blocks)])], structure
    for n in range(min(spec.max_blocks, 6), spec.min_blocks - 1, -1):
        for chars in range(spec.body_chars, 30, -10):
            blocks = [(f"Idea {i + 1}", [_text(chars, f"Detail {i + 1}.{j}") for j in range(per)]) for i in range(n)]
            if _fits(spec, blocks):
                return blocks, structure
    return [(f"Idea {i + 1}", [_text(40, f"Detail {i + 1}")]) for i in range(spec.min_blocks)], structure


def build_lecture(out_dir: Path, names: List[str] = None) -> Path:
    specs = [s for s in lp.SPECS if (names is None or s.name in names) and not s.needs]
    slides = []
    for number, spec in enumerate(specs, 1):
        blocks, structure = sample(spec)
        facts, groups = _facts(blocks)
        if spec.typed == "quote":
            facts[0]["text"] = blocks[0][1][0]
        content = [f["text"] for f in facts]
        slides.append({"slide": {"slide_number": number, "slide_title": spec.name.replace("_layout", "").replace("_", " ").title(),
                                 "slide_type": "content", "goal": spec.purpose, "kg_node_id": 1,
                                 "grounding": {"facts": facts, "items": [], "packet_version": "chapters-v2", "outline": [], "omissions": []}},
                       "content": content, "layout_hint": spec.name, "fact_ids": [[f["fact_id"]] for f in facts],
                       "groups": groups, "bullet_sources": [f["source_refs"] for f in facts], "speaker_notes": [],
                       "layout_structure": structure, "layout_reason": f"gallery sample of {spec.relation}"})
    path = Path(out_dir) / "layout_gallery.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lecture_id": "layout_gallery", "lecture_title": "Layout gallery", "slides": slides,
                                "metadata": {"qa_status": "passed"}}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def export_gallery(out_dir: Path, names: List[str] = None) -> Dict[str, object]:
    """Builds the gallery deck; returns the pptx_verify report and the path of the PPTX."""
    lecture = build_lecture(out_dir, names)
    target = Path(out_dir) / "layout_gallery.pptx"
    export_native(lecture, target, title="Layout gallery")
    report = json.loads((Path(out_dir) / "layout_gallery_pptx_verify.json").read_text(encoding="utf-8"))
    return {"pptx": str(target), "report": report}


if __name__ == "__main__":
    result = export_gallery(Path(sys.argv[1] if len(sys.argv) > 1 else "output/layout_gallery"))
    print(result["pptx"], "missing:", len(result["report"]["missing_text"]), "empty:", result["report"]["empty_body"])
