"""Lecture JSON + distributed assets -> native editable PPTX, without browser/HTML."""
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from PIL import Image

from src.generator import deck_pptx_exporter
from src.generator.layout_frames import all_layouts
from src.generator.layout_purpose import LAYOUT_PURPOSE
from src.generator.layout_registry import IMAGE_CONTENT_LAYOUTS, layout_for_content
from src.generator.pptx_verify import verify_pptx
from src.rag.asset_validation import validate_formula, validate_table
from src.rag.figure_captions import own_caption
from src.utils.config import Config
from src.utils.content_cache import PIPELINE_VERSION


def _read(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _asset(path, json_path):
    if not path:
        return None
    p = Path(str(path).replace("\\", "/"))
    for candidate in (p, json_path.parent / p, Path(Config.DATA_DIR).parent / p,
                      Path(Config.ASSETS_DIR) / p):
        if candidate.is_file():
            return str(candidate.resolve())
    raise FileNotFoundError(f"Assigned PPTX asset does not exist: {path}")


def _groups(entry):
    content = entry.get("content", [])
    if isinstance(content, str):
        content = [content]
    elif isinstance(content, dict):
        content = [f"{k}: {v}" for k, vs in content.items() for v in (vs if isinstance(vs, list) else [vs])]
    refs = entry.get("fact_ids", [])
    groups, used = [], set()
    for g in entry.get("groups", []):
        indices = g.get("item_indices", [])
        if any(not isinstance(i, int) or i < 0 or i >= len(content) for i in indices):
            raise ValueError("Invalid content index in PPTX group")
        items = [{"item_id": str(i), "text": str(content[i]), "fact_ids": refs[i] if i < len(refs) else []}
                 for i in indices if i not in used]
        used.update(indices)
        if items:
            group = {**g, "items": items}
            if len(re.sub(r"\W+", "", str(group.get("title", "")))) < 3:
                group["title"] = _display_title(entry.get("slide", {}).get("slide_title", ""))
            groups.append(group)
    merged = []
    for group in groups:           # a run of points under one sub-topic is one parent with all their bullets
        prev = merged[-1] if merged else None
        if (prev and group.get("title") and not group.get("title_from_item") and not prev.get("title_from_item")
                and str(prev.get("title", "")).strip().casefold() == str(group["title"]).strip().casefold()):
            prev["items"] = prev["items"] + group["items"]
            prev["item_indices"] = list(prev.get("item_indices", [])) + list(group.get("item_indices", []))
        else:
            merged.append(group)
    groups = merged
    remaining = [i for i in range(len(content)) if i not in used]
    if remaining:
        family = str(entry.get("layout_hint") or "only_content")
        if family in {"steps", "timeline", "numbered", "cycle", "steps_horizontal_layout",
                      "numbered_conclusions_layout"}:
            # Legacy ordered bullets are explicit steps; prose bullets stay within one concept.
            for i in remaining:
                groups.append({"group_id": f"item-{i}", "title": str(content[i]), "title_from_item": True,
                               "items": [{"item_id": str(i), "text": str(content[i]),
                                          "fact_ids": refs[i] if i < len(refs) else []}]})
        else:
            groups.append({"group_id": "body", "title": "", "items": [
                {"item_id": str(i), "text": str(content[i]), "fact_ids": refs[i] if i < len(refs) else []}
                for i in remaining]})
    return groups


def _display_title(value):
    """Writer sequence numbers are metadata; the PPTX footer owns page numbering."""
    return re.sub(r"^\s*\d+\.\s+", "", str(value or "")).strip()




def _layout_args(layout_id, title, content, groups, images, table_data, formula, light, captions=None, structure=None,
                 code=None):
    """Translate the validated catalog vocabulary to its native builder arguments. Every layout receives the slide's
    real groups (title + bullets) and, for typed layouts, the values the Writer took from the source."""
    structure = structure or {}
    args = {"title": title, "content": content, "light": light}
    if images:
        captions = list(captions or []) + [""] * 2
        args.update({"img_path": images[0], "img1_path": images[0],
                     "caption": captions[0], "caption1": captions[0]})
    if len(images) > 1:
        args.update({"img2_path": images[1], "caption2": captions[1]})
    if formula:
        args["latex_formula_block"] = formula
    if code:
        args.update({"code": code.get("code", ""), "code_language": code.get("language", "")})
    if table_data:
        args["table_markdown"] = (table_data.get("table_markdown", "")
                                  if isinstance(table_data, dict) else str(table_data))
        if isinstance(table_data, dict) and table_data.get("structured_table"):
            args["parsed_table"] = table_data["structured_table"]
        if isinstance(table_data, dict) and table_data.get("table_caption"):
            args["table_caption"] = table_data["table_caption"]

    blocks = [g for g in groups if g.get("items")]
    body = lambda g: " ".join(i["text"] for i in g["items"])
    lead = lambda g: f'{g["title"]}: {body(g)}' if g.get("title") else body(g)
    text_of = {fid: i["text"] for g in blocks for i in g["items"] for fid in i.get("fact_ids", [])}
    if not blocks:
        blocks = [{"title": "", "items": [{"text": t, "fact_ids": []} for t in content]}]
    items = lambda g: [i["text"] for i in g["items"]]

    def fold(n):
        """The layout's first n blocks, a further block folded into the last one: no bullet is dropped."""
        head = [dict(g) for g in (blocks + [{"title": "", "items": []}] * n)[:n]]
        for extra in blocks[n:]:
            head[-1] = {**head[-1], "items": head[-1]["items"] + extra["items"]}
        return head
    # Two-level payload for every layout: a group title is a parent bullet only when it adds something (not the
    # slide title again, not a title copied from its own item); the renderer draws its items as sub-bullets.
    same = lambda a, b: str(a or "").strip().casefold() == str(b or "").strip().casefold()
    args["groups"] = [{"title": "" if g.get("title_from_item") or same(g.get("title"), title) else str(g.get("title") or ""),
                       "items": items(g)} for g in blocks]
    if layout_id == "key_points_layout":
        args["points"] = [{"icon": f"{n:02d}", "title": g["title"], "heading": g["title"], "text": body(g),
                           "body": body(g), "bullets": items(g)} for n, g in enumerate(blocks, 1)]
    elif layout_id in {"conclusion_cards_layout", "numbered_conclusions_layout"}:
        args["conclusions"] = [{"heading": g["title"], "body": body(g), "text": body(g), "items": items(g)}
                               for g in blocks]
    elif layout_id == "steps_horizontal_layout":
        args["steps"] = [{"title": g["title"], "body": body(g), "items": items(g)} for g in blocks]
    elif layout_id == "comparison_columns_layout":
        args["sides"] = [{"label": g["title"], "items": [i["text"] for i in g["items"]]} for g in fold(2)]
    elif layout_id == "comparison_matrix_layout":
        args["matrix"] = {"entities": [str(e) for e in structure.get("entities", [])],
                          "rows": [{"criterion": g["title"], "cells": [i["text"] for i in g["items"]]} for g in blocks]}
    elif layout_id == "concept_breakdown_layout":
        args["concepts"] = [{"term": g["title"], "def": body(g), "items": items(g)} for g in blocks]
    elif layout_id in {"timeline_vertical_layout", "checklist_layout", "highlight_list_layout", "pyramid_layout",
                       "funnel_layout", "icon_matrix_layout", "formula_annotated_layout"}:
        args["content"] = [lead(g) for g in blocks]
        if layout_id == "timeline_vertical_layout":
            args["timeline_rows"] = [{"title": g["title"], "items": items(g)} for g in blocks]
    elif layout_id == "cycle_layout":
        args["content"] = [body(g) for g in blocks]
    elif layout_id == "hub_spoke_layout":
        args["content"] = [str(structure.get("center") or title)] + [body(g) for g in blocks]
    elif layout_id == "grid_2x2_layout":
        args["cells"] = [{"title": g["title"], "body": body(g), "items": items(g)} for g in blocks]
    elif layout_id == "three_cols_content_layout":
        cols = fold(3)
        args["cols"] = [{"title": g["title"], "body": "", "bullets": [i["text"] for i in g["items"]]} for g in cols]
    elif layout_id == "nested_bullets_layout":
        args["items"] = [{"text": text} for g in blocks for text in items(g)]
    elif layout_id == "research_question_layout":
        main = text_of.get(str(structure.get("main")), content[0] if content else "")
        args["main_question"] = main
        args["sub_questions"] = [text_of[str(f)] for f in structure.get("subs", []) if str(f) in text_of] or content[1:]
    elif layout_id in {"stats_cards_layout", "kpi_cards_layout", "big_stat_layout"}:
        items = [i for i in structure.get("items", []) if isinstance(i, dict)]
        stats = [{"value": str(i.get("value", "")), "label": text_of.get(str(i.get("fact_id")), str(i.get("label", "")))}
                 for i in items]
        args["stats"] = stats
        args["content"] = [f'{x["value"]}: {x["label"]}' for x in stats]
    elif layout_id == "quote_layout":
        args["quote"] = text_of.get(str(structure.get("fact_id")), " ".join(content))
        args["attribution"] = str(structure.get("attribution", ""))
    elif layout_id == "pricing_cards_layout":
        cards = structure.get("cards", [])
        args["cards"] = [{"name": g["title"], "price": str(cards[n].get("value", "")) if n < len(cards) else "",
                          "features": [i["text"] for i in g["items"]]} for n, g in enumerate(blocks)]
    elif layout_id == "editorial_layout":
        pairs = [p for p in structure.get("pairs", []) if isinstance(p, dict)]
        args.update({"lede": text_of.get(str(structure.get("lede")), content[0] if content else ""),
                     "pull_quote": "", "meta": [{"key": str(p.get("key", "")), "value": text_of.get(str(p.get("fact_id")), "")}
                                                for p in pairs]})
    elif layout_id == "split_contrast_layout":
        left, right = fold(2)
        args.update({"left_title": left["title"], "left_items": [i["text"] for i in left["items"]],
                     "right_title": right["title"], "right_items": [i["text"] for i in right["items"]]})
    elif layout_id == "two_contents_in_a_slide_layout":
        first, second = fold(2)
        args.update({"sub_title_1": first["title"], "sub_content_1": [i["text"] for i in first["items"]],
                     "sub_title_2": second["title"], "sub_content_2": [i["text"] for i in second["items"]]})
    elif layout_id == "comparison_layout" and not args.get("table_markdown"):
        args["table_markdown"] = "| Point | Evidence |\n|---|---|\n" + "\n".join(
            f"| {i + 1} | {text.replace('|', '/')} |" for i, text in enumerate(content))
    return args


def _image_layout(layout_id, images):
    """A wide figure takes the full width above its text; a tall or square one sits beside it."""
    if layout_id == "image_right_layout" and len(images) == 1:
        try:
            width, height = Image.open(images[0]).size
            if height and width / height >= 1.6:
                return "image_above_layout"
        except Exception:
            pass
    return layout_id


def _bullet_provenance(entry, info):
    """A bullet cites every fact it draws on (it may merge several into one spoken point), so provenance keeps all
    of them and the source refs of each."""
    facts = {f.get("fact_id"): f for f in info.get("grounding", {}).get("facts", [])}
    result = []
    for text, ids in zip(entry.get("content", []), entry.get("fact_ids", [])):
        if not ids or any(i not in facts for i in ids):
            raise ValueError(f"Every bullet requires at least one known fact_id: {text!r}")
        for fid in ids:
            fact = facts[fid]
            if not fact.get("source_refs"):
                raise ValueError(f"Bullet {fid} points at no passage of the document")
            if fact.get("origin", "sentence") == "sentence" and not fact.get("source_owned", True):
                raise ValueError(f"Derived metadata cannot support bullet {fid}")
        result.append({"text": str(text), "fact_id": ids[0], "fact_ids": list(ids),
                       "sources": [r for fid in ids for r in facts[fid].get("source_refs", [])]})
    return result


def embedded_deck_themes() -> list[dict]:
    """Return every curated design in the renderer's manifest format."""
    return [deck_pptx_exporter._design_spec_to_theme(
        deck_pptx_exporter._validate_and_fill_design_spec({"theme_name": item["name"]}, 0))
        for item in deck_pptx_exporter.THEME_LIBRARY]


def build_manifest(json_path, *, title=None, speaker="", institution=""):
    json_path = Path(json_path)
    data = _read(json_path, {})
    lecture_id = data.get("lecture_id", json_path.stem)
    title = title or data.get("lecture_title", lecture_id)
    entries = data.get("slides", [])
    theme = deck_pptx_exporter.generate_deck_theme(lecture_id, title, "", len(entries))
    light = theme.get("default_mode", "light") == "light"
    images, tables = {}, {}
    for suffix, target in (("image", images), ("table", tables)):
        for row in _read(json_path.parent / f"{lecture_id}_{suffix}_distribution.json", []):
            target.setdefault(int(row["slide_number"]), []).append(row)
    slides = [{"type": "cover", "title": title, "speaker": speaker, "institution": institution,
               "is_light": light, "layout_id": "cover_split_layout"}]
    toc_entries = []
    audit = []
    layout_notes = []
    used_images = set()
    sections: dict = {}          # scope root id -> its running-header label ("Chapter 3 · Classification")
    for entry in entries:
        info = entry.get("slide", {})
        source_num = int(info.get("slide_number", 0))
        heading = _display_title(info.get("slide_title", ""))
        if len(re.sub(r"\W+", "", heading)) < 3:
            concepts = [f.get("concept", "") for f in info.get("grounding", {}).get("facts", [])]
            heading = next((c for c in concepts if len(re.sub(r"\W+", "", c)) >= 3),
                           info.get("section_heading") or heading)
        if len(re.sub(r"\W+", "", heading)) < 3:
            raise ValueError(f"Invalid slide title at semantic slide {source_num}: {heading!r}")
        groups = _groups(entry)
        root = (info.get("grounding") or {}).get("scope_root_id")
        if info.get("is_section_opener"):
            if root is not None and root == info.get("kg_node_id"):
                sections[root] = re.sub(r"^(\S+\s+\d+)\.\s+", r"\1 · ", heading)
            slides.append({"type": "section-divider", "title": heading,
                           "source_slide_number": source_num,
                           "source_heading_id": info.get("kg_node_id"), "is_light": light,
                           "layout_id": "section_divider_layout"})
            toc_entries.append({"title": heading, "source_slide_number": source_num})
            audit.append({"slide_number": source_num, "kind": "section-divider",
                          "density": None, "support": "exempt", "layout_eligible": True})
            continue
        content = entry.get("content", [])
        if isinstance(content, str):
            content = [content]
        content = [str(x) for x in content]
        provenance = _bullet_provenance(entry, info)
        facts = info.get("grounding", {}).get("facts", [])
        if not content or any(not text.strip() for text in content):
            raise ValueError(f"Empty visible content on slide {source_num}")
        if len(provenance) != len(content):
            raise ValueError(f"Missing source references on slide {source_num}")

        image_rows = images.get(source_num, [])
        assigned_images = []
        assigned_captions = []
        image_provenance = []
        for row in image_rows:
            path = row.get("image_path")
            if not path:
                continue
            resolved = _asset(path, json_path)
            key = Path(resolved).name.casefold()
            if key in used_images:
                continue
            used_images.add(key)
            assigned_images.append(resolved)
            assigned_captions.append(own_caption(str(row.get("caption") or "")))
            image_provenance.append({"path": resolved, "score": row.get("score"),
                                     "source": row.get("source", "existing")})
            if len(assigned_images) == 2:
                break

        drawn = []                                  # the slide's formulas, each checked; a broken one is left out
        for latex in info.get("latex_formulas") or [info.get("latex_block_formula") or ""]:
            if not latex:
                continue
            formula_check = validate_formula(latex)
            if formula_check.get("valid"):
                drawn.append(formula_check.get("normalized_content") or latex)
            else:
                layout_notes.append({"slide": source_num, "asked": entry.get("layout_hint"),
                                     "used": "only_content",
                                     "reason": f"invalid formula removed: {formula_check.get('validation_reason')}"})
        formula = "\n\n".join(drawn)                # stacked in one box, a blank line between two
        table_data = info.get("table")
        if isinstance(table_data, dict) and not table_data.get("structured_table"):
            table_check = validate_table(table_data.get("table_markdown") or table_data.get("table_data") or "",
                                         table_data.get("table_caption") or "")
            if table_check.get("valid"):
                table_data = {**table_data,
                              "table_markdown": table_check["normalized_content"],
                              "structured_table": table_check["structured_table"],
                              "validation_status": table_check["validation_status"]}
            else:
                layout_notes.append({"slide": source_num, "asked": entry.get("layout_hint"),
                                     "used": "only_content",
                                     "reason": f"invalid table removed: {table_check.get('validation_reason')}"})
                table_data = None
        distributed = tables.get(source_num, [])
        # A table reconstructed and validated in the KG is authoritative.
        # The distribution cache predates structured-table validation and may
        # contain the collapsed OCR header that the normalizer has repaired.
        has_validated_table = bool(
            isinstance(table_data, dict)
            and (table_data.get("structured_table") or table_data.get("validation_status") in {"valid", "repaired"})
        )
        if distributed and not has_validated_table:
            table_data = {"table_markdown": distributed[0].get("table_data", ""),
                          "table_caption": distributed[0].get("table_caption", "")}
        requested = entry.get("layout_hint") or "only_content"
        formula_layouts = {"formula_top_layout", "formula_below_layout", "formula_hero_layout", "formula_annotated_layout"}
        table_layouts = {"table_above_layout", "table_highlight_layout", "data_table_layout"}
        if formula and requested not in formula_layouts:
            layout_notes.append({"slide": source_num, "asked": requested, "used": "formula_top_layout",
                                 "reason": "slide has a formula the requested layout cannot show"})
            requested = "formula_top_layout"
        elif table_data and requested not in table_layouts:
            layout_notes.append({"slide": source_num, "asked": requested, "used": "table_above_layout",
                                 "reason": "slide has a table the requested layout cannot show"})
            requested = "table_above_layout"
        elif not formula and requested in formula_layouts:
            layout_notes.append({"slide": source_num, "asked": requested, "used": "only_content",
                                 "reason": "formula layout without a formula"})
            requested = "only_content"
        if requested == "formula_annotated_layout" and not all(
                len(re.sub(r"\W+", "", str(g.get("title") or ""))) >= 1 for g in entry.get("groups") or [{}]):
            layout_notes.append({"slide": source_num, "asked": requested, "used": "formula_top_layout",
                                 "reason": "an annotated formula needs a named term per point"})
            requested = "formula_top_layout"
        code = info.get("code_block") if not (formula or table_data or assigned_images) else None
        code_layouts = {"code_beside_layout", "code_above_layout"}
        if code and requested not in code_layouts:
            layout_notes.append({"slide": source_num, "asked": requested, "used": "code_beside_layout",
                                 "reason": "slide has a code listing the requested layout cannot show"})
            requested = "code_beside_layout"
        elif not code and requested in code_layouts:
            layout_notes.append({"slide": source_num, "asked": requested, "used": "nested_bullets_layout",
                                 "reason": "code layout without a code listing on this slide"})
            requested = "nested_bullets_layout"
        spec = LAYOUT_PURPOSE.get(requested)
        if spec and spec.unit == "points" and len(groups) < spec.min_blocks:
            layout_notes.append({"slide": source_num, "asked": requested, "used": "nested_bullets_layout",
                                 "reason": f"{len(groups)} block(s) drawn; the layout needs {spec.min_blocks}"})
            requested = "nested_bullets_layout"
        if requested in IMAGE_CONTENT_LAYOUTS and not assigned_images:
            visual_plan = (entry.get("layout_structure") or {}).get("visual_plan") or {}
            fallback = "nested_bullets_layout"         # keeps the slide's title; a big statement would drop it
            layout_notes.append({"slide": source_num, "asked": requested, "used": fallback,
                                 "reason": (f"{visual_plan.get('type', 'image')} unavailable; "
                                            f"native fallback {visual_plan.get('fallback', 'native composition')}")})
            requested = fallback
        layout_id = _image_layout(layout_for_content(requested, len(assigned_images)), assigned_images)
        args = _layout_args(layout_id, heading, content, groups, assigned_images,
                            table_data, formula, light, assigned_captions, entry.get("layout_structure"), code)
        native = deck_pptx_exporter._layout_log_to_slide({"layout_function_name": layout_id, "args": args},
                                      json_path.parent, len(slides) + 1)
        visible_payload = json.dumps(native, ensure_ascii=False)
        missing_visible = [text for text in content
                           if text not in visible_payload and json.dumps(text, ensure_ascii=False)[1:-1] not in visible_payload]
        if missing_visible:
            raise ValueError(f"Layout {layout_id!r} lost or rewrote content on slide {source_num}: "
                             f"{missing_visible[:2]!r}")
        native.update({"title": heading, "layout_id": layout_id, "section": sections.get(root, ""),
                       "source_slide_number": source_num,
                       "source_heading_id": info.get("kg_node_id"),
                       "source_refs": [r for f in facts for r in f.get("source_refs", [])],
                       "bullet_provenance": provenance, "content_items": content,
                       "speaker_notes": entry.get("speaker_notes", []),
                       "image_provenance": image_provenance})
        # Contract: one semantic slide produces exactly one native page and image layouts retain
        # the same content. No adaptive pagination and no formula/table appendices are permitted.
        slides.append(native)
        audit.append({"slide_number": source_num, "kind": "content", "density": len(content),
                      "coverage": [p["fact_id"] for p in provenance], "support": "source-owned",
                      "heading_id": info.get("kg_node_id"),
                      "items": info.get("grounding", {}).get("items", []),
                      "omissions": info.get("grounding", {}).get("omissions", []),
                      "qa_verdict": data.get("metadata", {}).get("qa_status", "not_checked"),
                      "layout": layout_id, "layout_reason": entry.get("layout_reason", ""), "layout_eligible": True,
                      "image_provenance": image_provenance})
    slides.append({"type": "end", "title": "Thank you", "is_light": light,
                   "layout_id": "end_layout"})
    # Contents represents only the authored section-divider hierarchy.
    page_map_seed = {}
    for num, slide in enumerate(slides, 1):
        if "source_slide_number" in slide:
            page_map_seed.setdefault(str(slide["source_slide_number"]), []).append(num)
    per_toc = 30                      # up to 3 columns x 10 rows on one contents page (deck_pptx_gen.js TOC_*)
    toc_count = (len(toc_entries) + per_toc - 1) // per_toc
    toc_pages = []
    for page_i in range(toc_count):
        chunk = toc_entries[page_i * per_toc:(page_i + 1) * per_toc]
        items = []
        for local_i, entry in enumerate(chunk):
            source = str(entry["source_slide_number"])
            target = page_map_seed.get(source, [2])[0] + toc_count
            items.append({"n": f"{page_i * per_toc + local_i + 1:02d}",
                          "t": entry["title"], "p": target})
        toc_pages.append({"type": "toc_sidebar", "title": "Contents", "toc_items": items, "layout_id": "toc_layout",
                          "is_light": light, "toc_page": page_i + 1, "toc_total": toc_count})
    slides[1:1] = toc_pages
    page_map = {}
    for num, slide in enumerate(slides, 1):
        slide["page_num"] = num
        if "source_slide_number" in slide:
            page_map.setdefault(str(slide["source_slide_number"]), []).append(num)
        if slide.get("composed_shapes"):
            slide["composed_shapes"].append({"type": "bullet", "text": str(num), "x": 9.1, "y": 5.25,
                                             "w": .4, "h": .2, "font_size_hint": 10,
                                             "color": theme.get("accent", "2864DC")})
    for t in toc_entries:
        t["pages"] = page_map.get(str(t["source_slide_number"]), [])
    embedded_themes = embedded_deck_themes()
    # Put the selected design first.  Slides initially remain attached to this
    # master while the other designs are available under "This Presentation".
    embedded_themes.sort(key=lambda item: item.get("name") != theme.get("name"))
    return {"version": PIPELINE_VERSION, "evidence_schema_version": "chapters-v2",
            "qa_report": data.get("qa_report", {}), "layout_notes": layout_notes,
            "lecture_title": title, "speaker": speaker, "theme": theme,
            "active_theme_name": theme.get("name", ""), "embedded_themes": embedded_themes,
            "layouts": all_layouts(), "slides": slides,
            "source_page_map": page_map, "contents": toc_entries, "audit": audit}


def export_native(json_path, output_path, *, title=None, speaker="", institution=""):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = build_manifest(json_path, title=title, speaker=speaker, institution=institution)
    manifest_path = Path(json_path).parent / f"{Path(json_path).stem}_pptx_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # Write to a separate file: a failed export must never look like a successful stale deck.
    temporary = output_path.with_name(output_path.stem + ".pending.pptx")
    try:
        result = subprocess.run(["node", str(Path(__file__).with_name("deck_pptx_gen.js")),
                                 "--manifest", str(manifest_path), "--output", str(temporary)],
                                capture_output=True, text=True, encoding="utf-8", timeout=300)
        if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
            raise RuntimeError(f"PPTX export failed: {result.stderr or result.stdout}")
        report = verify_pptx(temporary, manifest)
        fitted = [line for line in result.stdout.splitlines() if line.startswith("[fit]")]
        if fitted:                    # boxes the renderer's fit guard set smaller than their plan
            print(fitted[0])
        (Path(json_path).parent / f"{Path(json_path).stem}_pptx_verify.json").write_text(
            json.dumps({**report, "layout_notes": manifest.get("layout_notes", []), "fit_guard": fitted[1:]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        if report["empty_body"] or report["missing_text"]:
            message = (f"[pptx_verify] empty pages {report['empty_body']}; "
                       f"{len(report['missing_text'])} bullet(s) not drawn")
            if report["empty_body"]:
                raise RuntimeError(message)
            print(message)
        try:
            temporary.replace(output_path)
        except PermissionError:      # the deck is open in PowerPoint: keep the new one beside it, never lose the export
            output_path = output_path.with_name(f"{output_path.stem}_{datetime.now():%H%M%S}.pptx")
            temporary.replace(output_path)
            print(f"[export] target is open elsewhere; wrote {output_path}")
    finally:
        temporary.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)          # only the renderer's input
    return len(manifest["slides"])
