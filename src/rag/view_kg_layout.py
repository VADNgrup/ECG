"""Where each KG node sits in kg_view.html, computed once from the document's structure (no physics in the browser).

Each top-level part of the document (a document of a collection, a chapter of a book) is a cell; inside a cell every
section is a square of its own nodes around its heading, in reading order; cells and squares are shelf-packed in
reading order. A node belongs to the first section (in reading order) it was seen in (`section`), else to the
section heading it is linked to, else to the section most of its neighbours are in, else to the trailing "unplaced"
cell.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from src.rag.structural import HEAD_MARK

NODE_SPACING = 22.0        # distance between neighbouring nodes of a section's sunflower
GROUP_PAD = 14.0           # empty margin around a section's square
CELL_PAD = 40.0            # empty margin inside a cell, around its sections (room for the cell title)
CELL_GAP = 90.0            # gap between cells
ROW_ASPECT = 1.6           # a shelf row is this many times the square root of the packed area wide
GOLDEN_ANGLE = math.pi * (3.0 - math.sqrt(5.0))

Box = Tuple[float, float]


def shelf_pack(sizes: List[Box], gap: float) -> Tuple[List[Box], Box]:
    """Top-left corners of boxes (w, h) laid left to right in rows of about ROW_ASPECT * sqrt(area), and the whole
    extent (w, h)."""
    if not sizes:
        return [], (0.0, 0.0)
    area = sum((w + gap) * (h + gap) for w, h in sizes)
    row_w = max(max(w for w, _ in sizes), ROW_ASPECT * math.sqrt(area))
    out, x, y, row_h, width = [], 0.0, 0.0, 0.0, 0.0
    for w, h in sizes:
        if x > 0 and x + w > row_w:
            x, y, row_h = 0.0, y + row_h + gap, 0.0
        out.append((x, y))
        x += w + gap
        row_h = max(row_h, h)
        width = max(width, x - gap)
    return out, (width, y + row_h)


def sunflower(count: int, spacing: float) -> List[Box]:
    """Offsets of `count` points around a centre, evenly spread (Vogel's spiral); point 0 is nearest the centre."""
    return [(spacing * math.sqrt(i + 1.0) * math.cos(i * GOLDEN_ANGLE),
             spacing * math.sqrt(i + 1.0) * math.sin(i * GOLDEN_ANGLE)) for i in range(count)]


class Structure:
    """The structural index as the viewer needs it: canonical heading ids (an alias is its parent), their order,
    parents, children and the part (cell) each belongs to."""

    def __init__(self, index: dict):
        heads = {int(k): h for k, h in (index.get("headings") or {}).items()}
        self.heads = heads
        self.canon = {h: (int(d["alias"]) if d.get("alias") is not None else h) for h, d in heads.items()}
        self.ids = sorted((h for h in heads if self.canon[h] == h), key=lambda h: heads[h].get("order", 0))
        self.rank = {h: i for i, h in enumerate(self.ids)}
        self.parent: Dict[int, Optional[int]] = {}
        for h in self.ids:
            p = heads[h].get("parent")
            while p is not None and int(p) in heads and self.canon[int(p)] != int(p):
                p = self.canon[int(p)]
            self.parent[h] = int(p) if p is not None and int(p) in heads else None
        self.children: Dict[Optional[int], List[int]] = {}
        for h in self.ids:
            self.children.setdefault(self.parent[h], []).append(h)
        # the parts: below the root(s), descending while the structure has a single branch (a book's chapters)
        front: List[int] = []
        level = self.children.get(None, [])
        while len(level) == 1 and self.children.get(level[0]):
            front.append(level[0])
            level = self.children[level[0]]
        self.front, self.parts = front, level
        self.part_of: Dict[int, int] = {}
        for i, p in enumerate(self.parts):
            stack = [p]
            while stack:
                h = stack.pop()
                self.part_of[h] = i
                stack += self.children.get(h, [])

    def name(self, h: int) -> str:
        return HEAD_MARK + str(self.heads[h].get("display", ""))

    def level(self, h: int) -> int:
        return int(self.heads[h].get("level", 1))


def node_sections(graph, st: Structure) -> Dict[str, Optional[int]]:
    """The section (canonical heading id) each node belongs to."""
    by_name: Dict[str, int] = {}
    for h in st.ids:                                   # a title shared by several headings names the first
        by_name.setdefault(st.name(h), h)
    out: Dict[str, Optional[int]] = {}
    for node, data in graph.nodes(data=True):
        if node in by_name:
            out[node] = by_name[node]
            continue
        secs = []
        for s in str(data.get("section") or "").split(","):
            s = s.strip()
            if s.lstrip("-").isdigit() and int(s) in st.canon and st.canon[int(s)] in st.rank:
                secs.append(st.canon[int(s)])
        if not secs:
            secs = [by_name[n] for n in graph.neighbors(node) if n in by_name]
        out[node] = min(secs, key=st.rank.get) if secs else None
    for node in [n for n, h in out.items() if h is None]:   # else the section most of its placed neighbours are in
        near = [out[n] for n in graph.neighbors(node) if out.get(n) is not None]
        if near:
            out[node] = max(set(near), key=lambda h: (near.count(h), -st.rank[h]))
    return out


def layout(graph, index: dict) -> dict:
    """{"pos": {node: (x, y)}, "sec": {node: hid|None}, "part": {node: cell index|-1}, "cells": [...]} -- cells
    [{"hid", "title", "x", "y", "w", "h"}] in reading order (the front matter and the unplaced nodes are cells too)."""
    st = Structure(index)
    sec = node_sections(graph, st)
    deg = dict(graph.degree())
    members: Dict[Optional[int], List[str]] = {}
    for node, h in sec.items():
        members.setdefault(h, []).append(node)
    heading_of = {h: n for n, h in sec.items() if n.startswith(HEAD_MARK) and h is not None and st.name(h) == n}

    cell_groups: List[Tuple[Optional[int], str, List[Optional[int]]]] = []
    front = [h for h in st.ids if h not in st.part_of]
    if any(members.get(h) for h in front):
        cell_groups.append((front[0] if front else None, st.heads[front[0]].get("display", "") if front else "",
                            front))
    for p in st.parts:
        subtree = sorted((h for h, i in st.part_of.items() if st.parts[i] == p), key=st.rank.get)
        cell_groups.append((p, str(st.heads[p].get("display", "")), subtree))
    if members.get(None):
        cell_groups.append((None, "", [None]))

    pos: Dict[str, Box] = {}
    part: Dict[str, int] = {}
    cells, cell_boxes, cell_local = [], [], []
    for ci, (hid, title, groups) in enumerate(cell_groups):
        squares, local = [], []
        for h in groups:
            nodes = members.get(h) or []
            if not nodes:
                continue
            head = heading_of.get(h) if h is not None else None
            rest = sorted((n for n in nodes if n != head), key=lambda n: (-deg.get(n, 0), n))
            offsets = sunflower(len(rest), NODE_SPACING)
            radius = max([math.hypot(x, y) for x, y in offsets] + [0.0]) + GROUP_PAD
            squares.append((2 * radius, 2 * radius))
            local.append((radius, ([(head, (0.0, 0.0))] if head else []) + list(zip(rest, offsets))))
        corners, (w, h_) = shelf_pack(squares, GROUP_PAD)
        cell_boxes.append((w + 2 * CELL_PAD, h_ + 2 * CELL_PAD))
        cell_local.append([(cx + CELL_PAD + r, cy + CELL_PAD + r, placed)
                           for (cx, cy), (r, placed) in zip(corners, local)])
        cells.append({"hid": hid, "title": title})
    corners, _ = shelf_pack(cell_boxes, CELL_GAP)
    for ci, ((x0, y0), (w, h_), groups) in enumerate(zip(corners, cell_boxes, cell_local)):
        cells[ci].update(x=round(x0, 1), y=round(y0, 1), w=round(w, 1), h=round(h_, 1))
        for gx, gy, placed in groups:
            for node, (dx, dy) in placed:
                pos[node] = (round(x0 + gx + dx, 1), round(y0 + gy + dy, 1))
                part[node] = ci
    return {"pos": pos, "sec": sec, "part": part, "cells": cells, "structure": st}
