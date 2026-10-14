"""Canonical node schema shared by KG construction and consumers.

``entity_type`` remains the persisted subtype for GraphML compatibility.  ``node_kind`` separates
LLM-extracted semantic entities from document structure and parser-owned assets.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Iterable

import networkx as nx

from src.rag.asset_validation import validate_asset_record


SCHEMA_VERSION = "kg-node-v2"

SEMANTIC_ENTITY_TYPES = (
    "concept",
    "code",
    "person",
    "organization",
    "location",
    "event",
    "work",
    "metric",
    "time",
    "group",
    "product",
    "dataset",
)
ASSET_TYPES = ("image", "table", "formula", "code_block")
STRUCTURE_TYPES = ("heading",)
NODE_KINDS = ("semantic", "structure", "asset")

_SEMANTIC = frozenset(SEMANTIC_ENTITY_TYPES)
_ASSET = frozenset(ASSET_TYPES)
_STRUCTURE = frozenset(STRUCTURE_TYPES)
_CONCEPT_ALIASES = frozenset({"method", "algorithm", "model", "term", "process", "claim"})


def clean_type(value) -> str:
    return str(value or "").strip().strip('"').lower()


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_PAREN_TAIL_RE = re.compile(r"\s*\([^)]*\)\s*$")


def name_variants(name: str) -> list[str]:
    """The forms of an entity name worth looking for in the document.

    An extractor canonicalises a name ("True Positives (TP)") while the prose writes only its base
    ("true positives"), so the parenthetical gloss is dropped for a second attempt."""
    name = str(name or "").strip()
    base = _PAREN_TAIL_RE.sub("", name).strip()
    return [v for v in dict.fromkeys([name, base]) if len(v) > 2]


def name_pattern(name: str):
    """Regex matching an entity name in text despite separator and case differences.

    "SGD Classifier" has to match "SGDClassifier"; joining the name's tokens with an optional
    separator run does that without a per-language rule. Returns None for an unusable name."""
    tokens = _TOKEN_RE.findall(str(name or ""))
    if not tokens:
        return None
    return re.compile(r"(?<!\w)" + r"[\W_]*".join(re.escape(t) for t in tokens) + r"(?!\w)",
                      re.IGNORECASE | re.UNICODE)


def name_patterns(name: str) -> list:
    return [p for p in (name_pattern(v) for v in name_variants(name)) if p is not None]


def name_in_text(name: str, text: str) -> bool:
    return any(p.search(text) for p in name_patterns(name))


def canonical_asset_type(value) -> str:
    """Map parser/legacy asset names to the persisted KG vocabulary."""
    value = clean_type(value)
    if value == "equation":
        return "formula"
    if value == "code":
        return "code_block"
    return value


def infer_node_kind(data: dict) -> str:
    explicit = clean_type(data.get("node_kind"))
    if explicit in NODE_KINDS:
        return explicit
    etype = clean_type(data.get("entity_type"))
    if etype == "equation" or etype in _ASSET:
        return "asset"
    if etype in _STRUCTURE:
        return "structure"
    return "semantic"


def is_semantic_node(data: dict) -> bool:
    return infer_node_kind(data) == "semantic" and clean_type(data.get("entity_type")) in _SEMANTIC


def is_asset_node(data: dict) -> bool:
    return (infer_node_kind(data) == "asset"
            and canonical_asset_type(data.get("entity_type")) in _ASSET)


def is_structure_node(data: dict) -> bool:
    return infer_node_kind(data) == "structure" and clean_type(data.get("entity_type")) in _STRUCTURE


def normalize_asset_records(records: Iterable[dict], *, strict_images: bool = True) -> tuple[list[dict], dict]:
    """Canonicalise ``asset_headings.json`` rows.

    Under the precision-first policy, an image is usable only when the VLM explicitly classified it
    as informative.  Legacy rows without that proof are intentionally treated as uncertain.
    """
    out: list[dict] = []
    report = Counter()

    for original in records or []:
        row = validate_asset_record(dict(original))
        row["type"] = canonical_asset_type(row.get("type"))
        if row["type"] not in _ASSET:
            report["invalid"] += 1
            continue
        if row["type"] == "image":
            classification = clean_type(row.get("classification"))
            if strict_images and classification != "informative":
                report[classification or "uncertain"] += 1
                continue
            row["classification"] = "informative"
        validation = row.get("asset_validation") or {}
        if row["type"] in {"formula", "table"} and not validation.get("valid", True):
            report[f"invalid_{row['type']}"] += 1
            continue
        out.append(row)
        report["kept"] += 1
    return out, dict(report)


def asset_node_fields(asset: dict) -> tuple[str, str, str]:
    """Return the stable ``(node id, description, entity_type)`` for one asset sidecar row."""
    atype = canonical_asset_type(asset.get("type"))
    page = asset.get("page")
    caption = str(asset.get("caption") or "").strip()
    if atype == "formula":
        latex = str(asset.get("latex") or "")
        return f"[Formula p{page}] {latex[:40]}", latex, atype
    if atype == "code_block":
        body = str(asset.get("code_body") or "")
        first = next((line for line in body.splitlines() if line.strip()), "")
        return f"[Code p{page}] {(caption or first)[:50]}", body, atype
    if atype == "image":
        short = (caption or str(asset.get("img_path") or ""))[:50]
        return f"[Figure p{page}] {short}", caption, atype
    if atype == "table":
        body = str(asset.get("table_body") or "")
        short = (caption or body)[:50]
        return f"[Table p{page}] {short}", caption or body, atype
    raise ValueError(f"unsupported asset type: {atype!r}")


def normalize_graph_schema(graph) -> dict:
    """Normalize a NetworkX graph in place and return an audit report.

    Unknown relation stubs are deleted.  Every remaining node receives a valid ``node_kind`` and a
    canonical subtype.  Images without explicit informative classification are fail-closed.
    """
    report = Counter()
    drop = []
    relabel = {}
    for node, data in list(graph.nodes(data=True)):
        raw = clean_type(data.get("entity_type"))
        kind = infer_node_kind(data)
        if raw == "unknown":
            drop.append(node)
            report["unknown_removed"] += 1
            continue

        if kind == "asset" or raw == "equation":
            etype = canonical_asset_type(raw)
            if etype not in _ASSET:
                drop.append(node)
                report["invalid_asset_removed"] += 1
                continue
            if etype == "image" and clean_type(data.get("classification")) != "informative":
                drop.append(node)
                report["unverified_image_removed"] += 1
                continue
            kind = "asset"
            if raw != etype:
                report[f"{raw or 'empty'}_to_{etype}"] += 1
            data["entity_type"] = etype
            data["node_kind"] = kind
            if etype == "image":
                data["classification"] = "informative"
            if isinstance(node, str) and node.startswith("[Equation "):
                relabel[node] = "[Formula " + node[len("[Equation "):]
        elif raw in _STRUCTURE:
            data["entity_type"] = raw
            data["node_kind"] = "structure"
        else:
            etype = raw
            if etype in _CONCEPT_ALIASES or etype not in _SEMANTIC:
                etype = "concept"
                report[f"{raw or 'empty'}_to_concept"] += 1
            data["entity_type"] = etype
            data["node_kind"] = "semantic"

    graph.remove_nodes_from(drop)
    if relabel:
        # NetworkX updates incident edges as part of the relabel operation.
        nx.relabel_nodes(graph, relabel, copy=False)
        report["formula_nodes_relabelled"] += len(relabel)
    report["nodes"] = graph.number_of_nodes()
    return dict(report)


def validate_graph_schema(graph) -> None:
    errors = []
    allowed = {"semantic": _SEMANTIC, "structure": _STRUCTURE, "asset": _ASSET}
    for node, data in graph.nodes(data=True):
        kind = clean_type(data.get("node_kind"))
        etype = clean_type(data.get("entity_type"))
        if kind not in allowed or etype not in allowed.get(kind, frozenset()):
            errors.append(f"{node!r}: node_kind={kind!r}, entity_type={etype!r}")
        elif kind == "asset" and etype == "image" and clean_type(data.get("classification")) != "informative":
            errors.append(f"{node!r}: image is not explicitly informative")
    if errors:
        raise ValueError("invalid KG node schema:\n" + "\n".join(errors[:20]))


def write_schema_metadata(kb_dir: Path) -> Path:
    path = Path(kb_dir) / "kg_schema.json"
    path.write_text(json.dumps({
        "version": SCHEMA_VERSION,
        "node_kinds": {
            "semantic": list(SEMANTIC_ENTITY_TYPES),
            "structure": list(STRUCTURE_TYPES),
            "asset": list(ASSET_TYPES),
        },
        "image_policy": "informative-only",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
