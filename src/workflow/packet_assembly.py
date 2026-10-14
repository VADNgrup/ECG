"""The assembly tool of the Packet Builder agent (code, 0 LLM): the evidence a slide's Writer reads.

A planned slide names the KG nodes it teaches, the links it states and the paragraphs it draws on; the agent
(`agents/packet_builder.py`) decides which nodes are its KEY ideas; every paragraph of the slide reaches the Writer.
Each key node becomes an anchor packet: the node (name, type, a recognition hint), the slide's links that involve it
(with the sentence stating each), and the WHOLE paragraphs holding its anchor sentences; the other nodes are only named. A context packet
carries the slide's other paragraphs. A paragraph belongs to one slide (the Lecture Planner gives every paragraph to
one): another slide never receives it whole (a node stated only there brings just its anchor sentences). The packets'
paragraphs form the slide's pool, every paragraph whole -- the planner sized the slide, nothing is trimmed;
formulas and tables right after them, the figures its passages cite (else ones close in meaning on their pages, at most
two) and code listings (on the slide when it shows nothing else, always in the speaker notes) are its assets; the Writer
chooses which formula or listing the slide draws. Content QA uses it again to find a thin slide facts no slide uses yet
(`candidates`) and to add the chosen ones to its pool (`add_facts`).
"""
from __future__ import annotations

import json
import math
import re
from typing import Dict, Iterable, List, Optional, Set

from src.rag.asset_validation import holds_data
from src.rag.code_text import cut_off, drawable_code, drawable_formula, joined_formula
from src.rag.embedder import embed_transient
from src.rag.retrieve_content import load_asset_headings
from src.utils import lang
from src.utils.config import Config

_RELATED = 6
_NEIGHBOURS = 3             # paragraphs on each side of a thin slide searched for unused facts
_CANDIDATES = 12
_CANDIDATE_CHARS = 500
# without labels learned from the document: a word right before a dash/dot number ("Figure 3-5")
_ANY_REFERENCE = re.compile(rf"\b([^\W\d_]{{{Config.CAPTION_LABEL_MIN_CHARS},{Config.CAPTION_LABEL_MAX_CHARS}}})\.?\s*"
                            rf"(\d+\s*[-–‑.]\s*\d+)")


def reference_re(labels) -> "re.Pattern":
    """A numbered reference ("Figure 3-5", "Table 1", "TABLE .1", "Hình 2.1") with the document's own labels."""
    if not labels:
        return _ANY_REFERENCE
    words = "|".join(sorted((re.escape(w) for w in labels), key=len, reverse=True))
    return re.compile(rf"\b({words})[\s.]*(\d+(?:\s*[-–‑.]\s*\d+)*)", re.IGNORECASE)


def reference_keys(pattern: "re.Pattern", text: str, labels=()) -> List[tuple]:
    """(label, number) of each reference in the text, in order; a label stands for the shortest of `labels` it
    starts with ("Figure 2" and "Fig. 2" are one figure)."""
    found = [(m.group(1).casefold(), re.sub(r"\s*[-–‑.]\s*", "-", m.group(2))) for m in pattern.finditer(text or "")]
    short = lambda w: min((lb for lb in labels if w.startswith(lb)), key=len, default=w)
    return [(short(w), n) for w, n in found]


def cells_key(structured: Optional[dict]) -> str:
    """A table's identity: its header and cells."""
    structured = structured or {}
    return json.dumps([structured.get("headers") or [], structured.get("rows") or []], ensure_ascii=False)


def clean_ocr_latex(latex: str) -> str:
    """Collapse glyph-by-glyph extraction spacing ("l i f e", "\\theta _ { 0 }") without touching \\commands."""
    text = re.sub(r"^\s*\$\$|\$\$\s*$", "", (latex or "").strip()).strip()
    chars = r"[A-Za-z0-9_{}^()]"
    return re.sub(rf"(?:{chars}(?:\s{chars}){{2,}})", lambda m: re.sub(r"\s+", "", m.group(0)), text)


_STACK = (r"\begin{gathered}", r"\end{gathered}", "\\\\")      # how a formula's own lines are stacked


def arranged_latex(source: str, lines) -> str:
    """`source` drawn as `lines` (one equation per line) when they are its text in order, split only at spaces;
    else "" (the formula is drawn as it is). Only a one-line formula is arranged."""
    lines = [" ".join(str(line).split()) for line in lines or [] if str(line).strip()]
    flat = " ".join(str(source or "").split())
    if len(lines) < 2 or any(mark in flat for mark in _STACK[1:]) or "\\begin" in flat or " ".join(lines) != flat:
        return ""
    return f"{_STACK[0]} {' \\\\ '.join(lines)} {_STACK[1]}"


def formula_key(latex: str) -> str:
    """A formula's identity whatever its line arrangement: its text without spaces or stacking."""
    text = str(latex or "")
    for mark in _STACK:
        text = text.replace(mark, "")
    return "".join(text.split())


def _listing(text: str) -> dict:
    """A fenced code unit as {text: the listing without its fences, language}."""
    lines = text.strip("\n").splitlines()
    language = ""
    if lines and lines[0].lstrip().startswith("```"):
        language = lines[0].strip().strip("`").strip()
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return {"text": "\n".join(ln.replace("\t", "    ").rstrip() for ln in lines).strip("\n"), "language": language}


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PacketAssembler:
    def __init__(self, chapter, inventory: dict, retrieval, kb_dir=None, embedder=None):
        self.chapter, self.inventory, self.retrieval = chapter, inventory, retrieval
        self.kb_dir, self.embedder = kb_dir, embedder
        self._figures: Optional[List[dict]] = None
        self._table_captions: Optional[Dict[str, str]] = None
        self._merged: Set[str] = set()           # pictures that are the same as a table drawn
        self._labels = [w.casefold() for w in (lang.labels_for_kb(kb_dir) if kb_dir is not None else [])]
        self._references = reference_re(self._labels)

    # ── one slide ──────────────────────────────────────────────────────────────
    def build(self, slide: dict, goal: dict, used: Set[str], taken: Optional[Set[str]] = None) -> dict:
        """{packets, pool, assets, size, named} for one planned slide; `used` = paragraphs and assets already in
        earlier slides' pools, `taken` = paragraphs other slides own. `slide["key"]` (default: every node it teaches)
        are the nodes that get a packet."""
        taken = set(taken or ()) - set(slide["paragraphs"])
        ch, nodes, links = self.chapter, self.inventory["nodes"], self.inventory["relations"]
        own = [p for p in slide["paragraphs"] if p in ch.by_pid and ch.by_pid[p]["kind"] == "text"]
        key = slide["teaches"] if slide.get("key") is None else [k for k in slide["teaches"] if k in slide["key"]]
        packets: List[dict] = []
        elsewhere: List[str] = []
        for kid in key:
            node = nodes.get(kid)
            if node is None:
                continue
            mine = [r for r in slide["links"] if r in links and kid in (links[r]["kid_a"], links[r]["kid_b"])
                    and not any(r in p.get("link_ids", []) for p in packets)]
            anchors = [ch.pid_of[s] for s in node["sentences"] if s in ch.pid_of]
            anchors += [ch.pid_of[s] for r in mine for s in links[r]["sentences"] if s in ch.pid_of]
            anchors = list(dict.fromkeys(p for p in anchors if ch.by_pid[p]["kind"] == "text"))
            if anchors and not any(p in own for p in anchors) and all(p in taken for p in anchors):
                elsewhere.append(kid)            # every sentence stating it is another slide's: named here, taught there
                continue
            # the slide's own paragraphs first; a node stated only outside every slide brings one paragraph with it
            ranked = [p for p in anchors if p in own] or [p for p in anchors if p not in own][:1]
            stated = node["sentences"] + [s for r in mine for s in links[r]["sentences"]]
            only = {p: [s for s in stated if ch.pid_of.get(s) == p] for p in ranked if p in taken}
            packets.append({"id": kid, "teaches": [kid], "keyword": f"{node['name']} ({node['type']})", "name": node["name"],
                            "hint": ch.sentences[node["sentences"][0]]["text"][:Config.PACKET_HINT_CHARS], "via": node.get("via", "name"),
                            "passages": ranked[:Config.PACKET_PASSAGES], "only": only,
                            "links": [{"id": r, "a": links[r]["a"], "b": links[r]["b"], "says": links[r]["sentences"][0]}
                                      for r in mine],
                            "link_ids": mine})
        stray = [r for r in slide["links"] if r in links and not any(r in p["link_ids"] for p in packets)]
        held = {p for pk in packets for p in pk["passages"]}
        context = [p for p in own if p not in held]
        if context or stray:
            packets.append({"id": "ctx", "teaches": [], "keyword": "", "name": "", "hint": "", "passages": context,
                            "links": [{"id": r, "a": links[r]["a"], "b": links[r]["b"], "says": links[r]["sentences"][0]}
                                      for r in stray],
                            "link_ids": stray})
        pool = self._pool(packets)
        for pk in packets:
            pk["related"] = self._related(pk, slide)
        assets = self._assets(pool, slide, used)
        chars = sum(lang.source_size(t) for row in pool.values() for _, t in row["sentences"])
        size = {"anchors": sum(1 for p in packets if p["id"] in key), "pool_chars": chars, "source_chars": chars}
        refs = {s: ch.sentences[s]["ref"] for row in pool.values() for s, _ in row["sentences"]}
        refs.update({a["sid"]: ch.sentences[a["sid"]]["ref"] for kind in ("formula", "table", "code")
                     for a in assets[kind]})
        titles = getattr(ch, "heading_titles", {})
        sections = list(dict.fromkeys(titles.get(ch.by_pid[p]["heading"], "") for p in own))
        return {"packets": packets, "pool": pool, "assets": assets, "size": size, "goal": goal.get("goal", ""),
                "language": self.chapter.language,
                "sections": [t for t in sections if t],
                "questions": goal.get("questions") or [], "refs": refs, "visual_plan": self.visual_plan(assets),
                "draw": slide.get("draw"),
                "named": [nodes[k]["name"] for k in slide["teaches"] if (k not in key or k in elsewhere) and k in nodes]}

    @staticmethod
    def visual_plan(assets: dict) -> dict:
        """What the slide shows besides its bullets: only a real asset of the book, never an invented visual."""
        if assets.get("formula"):
            return {"type": "formula", "source": "document", "primary": True}
        if assets.get("table"):
            return {"type": "table", "source": "document", "primary": True}
        if assets.get("image"):
            return {"type": "source_image", "source": "document", "primary": True}
        if assets.get("code"):
            return {"type": "code", "source": "document", "primary": True}
        return {"type": "none", "source": "none", "primary": False}

    # ── pool: whole paragraphs, each once; another slide's paragraph only by its anchor sentences ──
    def _pool(self, packets: List[dict]) -> Dict[str, dict]:
        ch = self.chapter
        keywords: Dict[str, List[str]] = {}         # pid -> the packets reading it
        partial: Dict[str, Optional[set]] = {}              # pid -> the only sentences to show (None: the paragraph)
        for pk in packets:
            for pid in pk["passages"]:
                keywords.setdefault(pid, [])
                if pk["name"]:
                    keywords[pid].append(pk["name"])
                only = (pk.get("only") or {}).get(pid)
                whole = not only or (pid in partial and partial[pid] is None)
                partial[pid] = None if whole else (partial.get(pid) or set()) | set(only)
        says = {l["says"] for pk in packets for l in pk["links"]}
        pool: Dict[str, dict] = {}
        for pid in sorted(keywords, key=lambda p: int(p[1:])):
            sids = [s for s in ch.by_pid[pid]["sentences"] if ch.sentences[s]["kind"] == "text"]
            if partial.get(pid):
                sids = [s for s in sids if s in partial[pid] or s in says]
            pool[pid] = {"page": ch.by_pid[pid]["page"], "sentences": [[s, ch.sentences[s]["text"]] for s in sids]}
        return pool

    def _related(self, packet: dict, slide: dict) -> List[str]:
        """Chapter nodes named in the packet's paragraphs, other than the one it teaches (names only)."""
        nodes = self.inventory["nodes"]
        where = set(packet["passages"])
        return [n["name"] for k, n in nodes.items() if k != packet["id"] and where & set(n["pids"])][:_RELATED]

    # ── assets ─────────────────────────────────────────────────────────────────
    def _assets(self, pool: Dict[str, dict], slide: dict, used: Set[str]) -> dict:
        ch = self.chapter
        formula, table, code, not_drawn = [], [], [], []
        # what follows the slide's passages, and the assets among its own paragraphs (one after a line too short to be a
        # passage follows none of them)
        mine = [p for p in slide.get("paragraphs") or [] if p in ch.by_pid and ch.by_pid[p]["kind"] != "text"]
        runs = [ch.following_assets(pid) for pid in pool] + [mine]
        joined: Set[str] = set()
        for run in runs:
            following = [aid for aid in run if aid not in used]
            for k, aid in enumerate(following):
                if aid in joined:
                    continue
                joined.add(aid)
                p = ch.by_pid[aid]
                s = ch.sentences[p["sentences"][0]]
                row = {"pid": aid, "pids": [aid], "sid": s["id"], "text": s["text"], "page": p["page"],
                       "normalized": s.get("normalized_content") or s["text"],
                       "structured": s.get("structured_table"), "validation": s.get("asset_validation")}
                if p["kind"] == "invalid_table" and holds_data(s["text"]):
                    not_drawn.append(s["text"])
                if p["kind"] == "formula" and cut_off(s["text"]):
                    row = self._joined(row, following[k + 1:])
                    joined.update(row["pids"])
                # an extraction that broke a formula apart, or a fenced block that is not code, is never drawn
                if p["kind"] == "formula" and drawable_formula(row["text"]):
                    formula.append(row)
                elif p["kind"] == "table":
                    table.append({**row, "caption": self._table_caption(row)})
                elif p["kind"] == "code" and drawable_code(_listing(s["text"])["text"]):
                    code.append({**row, **_listing(s["text"])})
        pages = {q for row in pool.values() for q in (_int(row["page"]), (_int(row["page"]) or 0) + 1) if q is not None}
        text = " ".join(t for row in pool.values() for _, t in row["sentences"])
        heads = {ch.by_pid[pid]["heading"] for pid in [*pool, *mine]}
        pages |= {q for pid in mine for q in [_int(ch.by_pid[pid]["page"])] if q is not None}
        images = self._images(pages, slide, used, text, heads, pool)
        images = self._merge_table_pictures(images, table)
        return {"formula": formula, "table": table, "code": code, "image": images[0] if images else None,
                "images": images, "not_drawn": not_drawn, "merged_pictures": sorted(self._merged)}

    def place_left_figures(self, slides: List[dict], used: Set[str]) -> None:
        """A figure no slide took (captioned, or uncaptioned and called content) goes to a slide with room for it, of its
        own section first, then nearest in pages: nothing is left out because no paragraph sits on its page, or because
        its slide cites another figure."""
        if self.kb_dir is None:
            return
        self._load_figures()
        for a in self._figures:
            name = str(a.get("img_path") or a.get("id"))
            page = _int(a.get("page"))
            if name in used or name in self._merged or page is None:
                continue
            room = [s for s in slides if s["built"]["pool"]
                    and len(s["built"]["assets"].get("images") or []) < Config.MAX_SLIDE_IMAGES]
            if not room:
                return

            def rank(slide: dict) -> tuple:
                pool = slide["built"]["pool"]
                pages = [q for q in map(_int, (row["page"] for row in pool.values())) if q is not None]
                own = _int(a.get("node")) in {self.chapter.by_pid[pid]["heading"] for pid in pool}
                return not own, min((abs(q - page) for q in pages), default=math.inf)
            slide = min(room, key=rank)
            assets = slide["built"]["assets"]
            assets["images"] = [*(assets.get("images") or []), self._figure_row(a)]
            assets["image"] = assets["images"][0]
            slide["built"]["visual_plan"] = self.visual_plan(assets)
            used.add(name)

    def _load_figures(self) -> None:
        if self._figures is None:
            chapter_heads = {int(h) for h in self.chapter.heading_ids}
            self._figures = [a for a in load_asset_headings(self.kb_dir) or []
                            if a.get("type") == "image" and _int(a.get("node")) in chapter_heads
                            and a.get("classification") != "decorative"]

    @staticmethod
    def _figure_row(a: dict) -> dict:
        return {"type": "image", "id": a.get("img_path") or a.get("id"), "page": a.get("page"),
                "caption": a.get("caption") or "", "real_caption": a.get("real_caption") or ""}

    def _merge_table_pictures(self, images: List[dict], table: List[dict]) -> List[dict]:
        """A picture captioned with the label the document's tables carry, on the page of a table without a caption, is
        a picture of that table: the editable table stays and takes its caption."""
        self._table_caption({})
        labels = {k[0] for c in self._table_captions.values() for k in reference_keys(self._references, c, self._labels)}
        kept = []
        for image in images:
            key = next(iter(reference_keys(self._references, image.get("real_caption") or "", self._labels)), None)
            same = next((t for t in table if not t.get("caption") and _int(t.get("page")) == _int(image.get("page"))), None)
            if key and key[0] in labels and same:
                same["caption"] = image["real_caption"]
                self._merged.add(str(image.get("id")))
            else:
                kept.append(image)
        return kept

    def _table_caption(self, row: dict) -> str:
        """The document's own caption of a table (never the LLM summary), matched by the table's cells."""
        if self.kb_dir is None:
            self._table_captions = {}
            return ""
        if self._table_captions is None:
            self._table_captions = {cells_key(a.get("structured_table")): a.get("real_caption") or ""
                                    for a in load_asset_headings(self.kb_dir) or [] if a.get("type") == "table"}
        return self._table_captions.get(cells_key(row.get("structured")), "")

    def _joined(self, row: dict, after: List[str]) -> dict:
        """A formula the extraction cut apart: its block joined with the formula blocks right after it while the
        expression is still open (`F₁ = 2 ×` + `\\frac{precision × recall}{precision + recall}`)."""
        texts, pids = [row["text"]], [row["pid"]]
        for aid in after:
            p = self.chapter.by_pid[aid]
            if p["kind"] != "formula" or not cut_off(texts[-1]):
                break
            texts.append(self.chapter.sentences[p["sentences"][0]]["text"])
            pids.append(aid)
        text = joined_formula(texts)
        return {**row, "pids": pids, "text": text, "normalized": text}

    def _closed_sections(self, heads: Set[int], pool: Dict[str, dict]) -> Set[int]:
        """The sections among `heads` whose last teachable paragraph is in this pool."""
        ch = self.chapter
        last = {}
        for p in ch.paragraphs:
            if ch.teachable(p):
                last[p["heading"]] = p["pid"]
        return {h for h in heads if last.get(h) in pool}

    def _images(self, pages: Set[int], slide: dict, used: Set[str], text: str = "", heads: Optional[Set[int]] = None,
                pool: Optional[Dict[str, dict]] = None) -> List[dict]:
        """The document's figures the slide really shows, at most MAX_SLIDE_IMAGES: the figures its passages cite
        ("Figure 3-5", "Table 1": the document's own labels) in the order cited, else, closest in meaning first, among the figures of the slide's own sections (`heads`) on its pages, and the rest of
        a section's figures on the slide that closes the section; a figure of a section no slide teaches goes with the slide
        on its page -- a figure merely printed on the page of another section's slide is not this one's; without `heads`, the caption must be close to the slide (cos >= PACKET_IMAGE_MIN_COS)."""
        if self.kb_dir is None:
            return []
        self._load_figures()
        free = [a for a in self._figures if str(a.get("img_path") or a.get("id")) not in used | self._merged]
        label = lambda a: next(iter(reference_keys(self._references, a.get("real_caption") or "", self._labels)), None)
        by_label = {label(a): a for a in free if label(a)}
        cited = dict.fromkeys(reference_keys(self._references, text, self._labels))
        found = [by_label[c] for c in cited if c in by_label][:Config.MAX_SLIDE_IMAGES]
        if not found:
            closing = self._closed_sections(heads, pool) if heads else set()
            taught = {p["heading"] for p in self.chapter.paragraphs if self.chapter.teachable(p)}
            near = [a for a in free if (_int(a.get("page")) in pages and (heads is None or _int(a.get("node")) not in taught))
                    or (heads and _int(a.get("node")) in heads and (_int(a.get("page")) in pages or _int(a.get("node")) in closing))]
            if not near or self.embedder is None:
                return []
            texts = [f"{slide['title']}. {slide.get('message') or ''}"] + \
                    [a.get("real_caption") or a.get("caption") or "" for a in near]
            sims = embed_transient(self.embedder, texts)
            sims = sims[1:] @ sims[0]
            ranked = sorted(range(len(near)), key=lambda k: -float(sims[k]))
            floor = Config.PACKET_IMAGE_MIN_COS if heads is None else -1.0
            found = [near[k] for k in ranked if float(sims[k]) >= floor][:Config.MAX_SLIDE_IMAGES]
        return [self._figure_row(a) for a in found]

    # ── Content QA: facts a thin slide could still teach ──────────────────────
    def candidates(self, slide: dict, cited: Set[str], owners: Dict[str, str]) -> List[dict]:
        """Unused facts for a thin slide, each `{id: C#, pid, sids, text, page, source}`: uncited sentences of the
        neighbouring paragraphs, then uncited sentences of the chapter that retrieval finds on the slide's topic. Never a
        sentence already in its pool or cited by any slide."""
        ch, built = self.chapter, slide["built"]
        held = {s for row in built["pool"].values() for s, _ in row["sentences"]}
        usable = lambda s: (s in ch.sentences and s not in held and s not in cited and ch.sentences[s]["kind"] == "text"
                            and ch.pid_of.get(s) in ch.by_pid and ch.by_pid[ch.pid_of[s]]["kind"] == "text"
                            and len(ch.sentences[s]["text"].split()) >= 4)
        groups: Dict[str, List[str]] = {}
        order: List[str] = []

        def take(pid: str, sids: Iterable[str]) -> None:
            fresh = [s for s in sids if usable(s) and s not in groups.get(pid, [])]
            if fresh:
                if pid not in groups:
                    order.append(pid)
                groups.setdefault(pid, []).extend(fresh)

        own = sorted((int(p[1:]) for p in slide["paragraphs"] if p in ch.by_pid), key=int)
        if own:
            near = sorted({k for n in (own[0], own[-1]) for k in range(n - _NEIGHBOURS, n + _NEIGHBOURS + 1)} - set(own),
                          key=lambda k: min(abs(k - own[0]), abs(k - own[-1])))
            for k in near:
                if f"P{k}" in ch.by_pid:
                    take(f"P{k}", ch.by_pid[f"P{k}"]["sentences"])
        if self.retrieval is not None:
            query = " ".join([slide["title"], slide.get("message") or ""] + list(slide.get("bullets") or []))
            names = [pk["name"] for pk in built["packets"] if pk.get("name")]
            try:
                hits = self.retrieval.sentences(query, names, k=16, exclude=held | set(cited))
            except Exception as exc:                  # the embedding API still unanswered after its client's retries
                raise RuntimeError(f"[packet_assembly] retrieval failed for '{slide['title']}' after "
                                   f"{Config.EMBED_MAX_RETRIES} retries: {type(exc).__name__}: {str(exc)[:160]}") from exc
            for hit in hits:
                if hit.get("pid"):
                    take(hit["pid"], [hit["id"]])
        out = []
        for pid in order[:_CANDIDATES]:
            sids = sorted(groups[pid], key=ch.by_pid[pid]["sentences"].index)
            text = " ".join(ch.sentences[s]["text"] for s in sids)
            source = f"{pid} of slide '{owners[pid]}'" if pid in owners else pid
            out.append({"id": f"C{len(out) + 1}", "pid": pid, "sids": sids, "page": ch.by_pid[pid]["page"],
                        "source": source, "text": text if len(text) <= _CANDIDATE_CHARS
                        else text[:_CANDIDATE_CHARS].rsplit(" ", 1)[0] + " …"})
        return out

    def add_facts(self, slide: dict, chosen: List[dict]) -> int:
        """Add the chosen candidates' sentences to the slide's pool (its context packet reads them). Returns how many
        sentences were added."""
        ch, built = self.chapter, slide["built"]
        added = 0
        ctx = next((pk for pk in built["packets"] if pk["id"] == "ctx"), None)
        if ctx is None and chosen:
            ctx = {"id": "ctx", "teaches": [], "keyword": "", "name": "", "hint": "", "passages": [], "links": [],
                   "link_ids": [], "related": []}
            built["packets"].append(ctx)
        for c in chosen:
            row = built["pool"].setdefault(c["pid"], {"page": ch.by_pid[c["pid"]]["page"], "sentences": []})
            have = {s for s, _ in row["sentences"]}
            new = [s for s in c["sids"] if s not in have]
            if not new:
                continue
            keep = have | set(new)
            row["sentences"] = [[s, ch.sentences[s]["text"]] for s in ch.by_pid[c["pid"]]["sentences"] if s in keep]
            added += len(new)
            if c["pid"] not in ctx["passages"]:
                ctx["passages"].append(c["pid"])
        if added:
            built["pool"] = dict(sorted(built["pool"].items(), key=lambda kv: int(kv[0][1:])))
            chars = sum(lang.source_size(t) for row in built["pool"].values() for _, t in row["sentences"])
            built["size"].update({"pool_chars": chars, "source_chars": chars})
            built["refs"].update({s: ch.sentences[s]["ref"] for row in built["pool"].values() for s, _ in row["sentences"]})
        return added
