"""The assembly tool of the Packet Builder agent (code, 0 LLM): the evidence a slide's Writer reads.

A planned slide names the KG nodes it teaches, the links it states and the paragraphs it draws on; the agent
(`agents/packet_builder.py`) decides which nodes are its KEY ideas and which paragraphs are presenter-only notes.
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

import re
from typing import Dict, Iterable, List, Optional, Set

from src.rag.code_text import cut_off, drawable_code, drawable_formula, joined_formula
from src.rag.embedder import embed_transient
from src.rag.retrieve_content import load_asset_headings
from src.utils.config import Config

_RELATED = 6
_NEIGHBOURS = 3             # paragraphs on each side of a thin slide searched for unused facts
_CANDIDATES = 12
_CANDIDATE_CHARS = 500
_FIGURE = re.compile(r"\bFig(?:ure|\.)\s*(\d+)\s*[-–‑.]\s*(\d+)", re.IGNORECASE)


def clean_ocr_latex(latex: str) -> str:
    """Collapse glyph-by-glyph extraction spacing ("l i f e", "\\theta _ { 0 }") without touching \\commands."""
    text = re.sub(r"^\s*\$\$|\$\$\s*$", "", (latex or "").strip()).strip()
    chars = r"[A-Za-z0-9_{}^()]"
    return re.sub(rf"(?:{chars}(?:\s{chars}){{2,}})", lambda m: re.sub(r"\s+", "", m.group(0)), text)


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

    # ── one slide ──────────────────────────────────────────────────────────────
    def build(self, slide: dict, goal: dict, used: Set[str], taken: Optional[Set[str]] = None) -> dict:
        """{packets, pool, assets, size, named, notes} for one planned slide; `used` = paragraphs and assets already in
        earlier slides' pools, `taken` = paragraphs other slides own. `slide["key"]` (default: every node it teaches)
        are the nodes that get a packet; `slide["notes"]` are its paragraphs for the presenter only."""
        taken = set(taken or ()) - set(slide["paragraphs"])
        ch, nodes, links = self.chapter, self.inventory["nodes"], self.inventory["relations"]
        notes = [p for p in slide.get("notes") or [] if p in ch.by_pid]
        own = [p for p in slide["paragraphs"] if p in ch.by_pid and ch.by_pid[p]["kind"] == "text" and p not in notes]
        key = slide["teaches"] if slide.get("key") is None else [k for k in slide["teaches"] if k in slide["key"]]
        packets: List[dict] = []
        for kid in key:
            node = nodes.get(kid)
            if node is None:
                continue
            mine = [r for r in slide["links"] if r in links and kid in (links[r]["kid_a"], links[r]["kid_b"])
                    and not any(r in p.get("link_ids", []) for p in packets)]
            anchors = [ch.pid_of[s] for s in node["sentences"] if s in ch.pid_of]
            anchors += [ch.pid_of[s] for r in mine for s in links[r]["sentences"] if s in ch.pid_of]
            anchors = list(dict.fromkeys(p for p in anchors if ch.by_pid[p]["kind"] == "text" and p not in notes))
            # the slide's own paragraphs first; a node stated only elsewhere brings one paragraph with it, and only its
            # anchor sentences when another slide owns that paragraph
            ranked = [p for p in anchors if p in own] or [p for p in anchors if p not in own][:1]
            stated = node["sentences"] + [s for r in mine for s in links[r]["sentences"]]
            only = {p: [s for s in stated if ch.pid_of.get(s) == p] for p in ranked if p in taken}
            packets.append({"id": kid, "teaches": [kid], "keyword": f"{node['name']} ({node['type']})", "name": node["name"],
                            "hint": node["description"][:140], "via": node.get("via", "name"),
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
        chars = sum(len(t) for row in pool.values() for _, t in row["sentences"])
        size = {"anchors": sum(1 for p in packets if p["id"] in key), "pool_chars": chars, "source_chars": chars}
        refs = {s: ch.sentences[s]["ref"] for row in pool.values() for s, _ in row["sentences"]}
        refs.update({a["sid"]: ch.sentences[a["sid"]]["ref"] for kind in ("formula", "table", "code")
                     for a in assets[kind]})
        return {"packets": packets, "pool": pool, "assets": assets, "size": size, "goal": goal.get("goal", ""),
                "questions": goal.get("questions") or [], "refs": refs, "visual_plan": self.visual_plan(assets),
                "named": [nodes[k]["name"] for k in slide["teaches"] if k not in key and k in nodes],
                "notes": [ch.text_of(p) for p in notes]}

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
        formula, table, code = [], [], []
        for pid in pool:
            following = [aid for aid in ch.following_assets(pid) if aid not in used]
            joined: Set[str] = set()
            for k, aid in enumerate(following):
                if aid in joined:
                    continue
                p = ch.by_pid[aid]
                s = ch.sentences[p["sentences"][0]]
                row = {"pid": aid, "pids": [aid], "sid": s["id"], "text": s["text"], "page": p["page"],
                       "normalized": s.get("normalized_content") or s["text"],
                       "structured": s.get("structured_table"), "validation": s.get("asset_validation")}
                if p["kind"] == "formula" and cut_off(s["text"]):
                    row = self._joined(row, following[k + 1:])
                    joined.update(row["pids"])
                # an extraction that broke a formula apart, or a fenced block that is not code, is never drawn
                if p["kind"] == "formula" and drawable_formula(row["text"]):
                    formula.append(row)
                elif p["kind"] == "table":
                    table.append(row)
                elif p["kind"] == "code" and drawable_code(_listing(s["text"])["text"]):
                    code.append({**row, **_listing(s["text"])})
        pages = {q for row in pool.values() for q in (_int(row["page"]), (_int(row["page"]) or 0) + 1) if q is not None}
        text = " ".join(t for row in pool.values() for _, t in row["sentences"])
        images = self._images(pages, slide, used, text) if not formula and not table else []
        return {"formula": formula, "table": table, "code": code, "image": images[0] if images else None,
                "images": images}

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

    def _images(self, pages: Set[int], slide: dict, used: Set[str], text: str = "") -> List[dict]:
        """The book figures the slide really shows, at most MAX_SLIDE_IMAGES: the figures its passages cite ("Figure
        3-5") in the order cited, else the figures on the pool's pages whose caption is close in meaning to the slide
        (cos >= PACKET_IMAGE_MIN_COS), closest first -- a figure merely printed on the same page is not the slide's."""
        if self.kb_dir is None:
            return []
        if self._figures is None:
            heads = {int(h) for h in self.chapter.heading_ids}
            self._figures = [a for a in load_asset_headings(self.kb_dir) or []
                            if a.get("type") == "image" and _int(a.get("node")) in heads
                            and a.get("classification") != "decorative"]
        free = [a for a in self._figures if str(a.get("img_path") or a.get("id")) not in used]
        label = lambda a: next((f"{m.group(1)}-{m.group(2)}" for m in [_FIGURE.search(a.get("real_caption") or "")] if m), None)
        by_label = {label(a): a for a in free if label(a)}
        cited = dict.fromkeys(f"{m.group(1)}-{m.group(2)}" for m in _FIGURE.finditer(text))
        found = [by_label[c] for c in cited if c in by_label][:Config.MAX_SLIDE_IMAGES]
        if not found:
            near = [a for a in free if _int(a.get("page")) in pages]
            if not near or self.embedder is None:
                return []
            texts = [f"{slide['title']}. {slide.get('message') or ''}"] + \
                    [a.get("real_caption") or a.get("caption") or "" for a in near]
            sims = embed_transient(self.embedder, texts)
            sims = sims[1:] @ sims[0]
            ranked = sorted(range(len(near)), key=lambda k: -float(sims[k]))
            found = [near[k] for k in ranked if float(sims[k]) >= Config.PACKET_IMAGE_MIN_COS][:Config.MAX_SLIDE_IMAGES]
        return [{"type": "image", "id": a.get("img_path") or a.get("id"), "page": a.get("page"),
                 "caption": a.get("caption") or "", "real_caption": a.get("real_caption") or ""} for a in found]

    # ── Content QA: facts a thin slide could still teach ──────────────────────
    def candidates(self, slide: dict, cited: Set[str], owners: Dict[str, str]) -> List[dict]:
        """Unused facts for a thin slide, each `{id: C#, pid, sids, text, page, source}`: its own presenter-only
        paragraphs, then uncited sentences of the neighbouring paragraphs, then uncited sentences of the chapter that
        retrieval finds on the slide's topic. Never a sentence already in its pool or cited by any slide."""
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

        for pid in slide.get("notes") or []:
            if pid in ch.by_pid:
                take(pid, ch.by_pid[pid]["sentences"])
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
            except Exception as exc:                  # retrieval is a help, never a reason to fail the deck
                print(f"[packet_assembly] retrieval failed for '{slide['title']}': {type(exc).__name__}")
                hits = []
            for hit in hits:
                if hit.get("pid"):
                    take(hit["pid"], [hit["id"]])
        out = []
        for pid in order[:_CANDIDATES]:
            sids = sorted(groups[pid], key=ch.by_pid[pid]["sentences"].index)
            text = " ".join(ch.sentences[s]["text"] for s in sids)
            source = ("presenter note of this slide" if pid in (slide.get("notes") or [])
                      else f"{pid} of slide '{owners[pid]}'" if pid in owners else pid)
            out.append({"id": f"C{len(out) + 1}", "pid": pid, "sids": sids, "page": ch.by_pid[pid]["page"],
                        "source": source, "text": text if len(text) <= _CANDIDATE_CHARS
                        else text[:_CANDIDATE_CHARS].rsplit(" ", 1)[0] + " …"})
        return out

    def add_facts(self, slide: dict, chosen: List[dict]) -> int:
        """Add the chosen candidates' sentences to the slide's pool (its context packet reads them); a presenter-only
        paragraph that is chosen leaves the notes. Returns how many sentences were added."""
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
            if c["pid"] in (slide.get("notes") or []):
                slide["notes"] = [p for p in slide["notes"] if p != c["pid"]]
        if added:
            built["pool"] = dict(sorted(built["pool"].items(), key=lambda kv: int(kv[0][1:])))
            built["notes"] = [ch.text_of(p) for p in slide.get("notes") or [] if p in ch.by_pid]
            chars = sum(len(t) for row in built["pool"].values() for _, t in row["sentences"])
            built["size"].update({"pool_chars": chars, "source_chars": chars})
            built["refs"].update({s: ch.sentences[s]["ref"] for row in built["pool"].values() for s, _ in row["sentences"]})
        return added
