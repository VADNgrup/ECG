"""The lecture pipeline for one deck. Each chapter of the scope (a top-level section; a Part too big for one outline
is planned chapter by chapter) goes through the four agents:
KG inventory -> Outline (goals and slides from the KG and retrieval, one call per chapter) -> Packet Builder (what each slide's
packets carry, assembled by `PacketAssembler`) -> Slide Writer -> Content QA. This object holds the per-chapter state
the graph nodes share: the slide specs, the packets the Writer reads and the downstream packets the multimodal/export
steps read. A planned slide is never merged or split; Content QA may only drop a slide it could not fill.
"""
from __future__ import annotations

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.models.slide import Slide, SlideType, Table
from src.rag.chapter_map import load_chapters
from src.rag.evidence_store import EvidenceStore
from src.rag.kg_inventory import HeadingLayer, build_inventory, summary
from src.rag.multi_retrieval import ChapterRetrieval
from src.rag.retrieve_content import load_kg_graph, load_structural_index
from src.utils.config import Config
from src.utils.content_cache import digest
from src.workflow import deck_labels, fenced
from src.workflow.agents.outline import OutlineAgent
from src.workflow.agents.packet_builder import PacketBuilderAgent
from src.workflow import domain as fields
from src.workflow.deck_title import book_title, deck_title
from src.workflow.packet_assembly import PacketAssembler, clean_ocr_latex
from src.workflow.scope import find_kb_dir, ScopeOrchestrator


class ChapterPipeline:
    def __init__(self, model: str, domain: Optional[str] = None):
        self.model = model
        self.parts: Dict[int, List[int]] = {}       # unit -> the split headings above it (outermost first)
        self.part_titles: Dict[int, str] = {}       # a heading too big for one plan, planned section by section
        self.part_notes: Dict[int, str] = {}        # its own text too short for a slide: its opener's notes
        self.own_only: set = set()                  # units holding only a split heading's own text
        self.layer = None                           # the headings' own inventories (kg_inventory.HeadingLayer)
        self.requested = domain                  # the field the user chose; None: detected in open()
        self.domain = domain or "general"
        self.state: Dict[int, dict] = {}
        self.specs: List[Slide] = []
        self.packets: List[dict] = []
        self.jobs: List[dict] = []
        self.base: dict = {}

    # ── scope ──────────────────────────────────────────────────────────────────
    def open(self, context, root_heading: Optional[str] = None) -> str:
        """Resolve the KB, the chapters (roots) and their headings; load the chapters and the KG. Returns the deck title."""
        kb_dir = find_kb_dir(context.document_id)
        if kb_dir is None:
            raise ValueError(f"No knowledge base for {context.document_id}; run extract first")
        self.kb_dir, self.index = Path(kb_dir), load_structural_index(kb_dir)
        scope = ScopeOrchestrator(self.index, kb_dir)
        roots = scope.resolve_roots(root_heading, scope.visible)
        if not roots:
            raise RuntimeError("Structural KG contains no usable scope roots")
        ordered = scope.ordered_scope(roots, scope.visible)
        self.base = scope.manifest(roots, ordered)
        self.store = EvidenceStore(kb_dir, roots)
        self.store.mentions                      # KG mention index built once, before the chapter threads start
        self.chapters, self.parts = self._units(kb_dir, roots, scope)
        for chapter in self.chapters:
            fenced.read(chapter, self.model)
        self.retrieval = {c.id: ChapterRetrieval(self.store, c) for c in self.chapters}
        try:
            self.graph = load_kg_graph(kb_dir)
        except Exception as exc:
            print(f"[Outline] KG graph unavailable ({type(exc).__name__}); planning without it")
            self.graph = None
        self.layer = HeadingLayer(kb_dir, self.graph) if self.graph is not None else None
        self.titles = {int(h): v.get("title", "") for h, v in (self.index.get("headings") or {}).items()}
        title = self._deck_title(context, roots, root_heading, scope)
        self.domain = fields.resolve(self.model, self.requested, title, *self._field_sample())
        self.planner = OutlineAgent(self.model, self.domain, self.retrieval, self.kb_dir)
        self.packet_agent = PacketBuilderAgent(self.model, self.domain)
        return title

    def _units(self, kb_dir, roots: List[int], scope):
        """The chapters the outline plans one by one: each root, except one bigger than OUTLINE_UNIT_MAX_CHARS with
        sections under it, which is planned section by section (again inside a section still too big). A split heading
        keeps its place in the deck as an opener over its sections; its own text is a unit of its own when it can fill a
        slide (SECTION_SLIDE_MIN_CHARS), else the opener's notes. Returns the units and {unit: [split headings above it,
        outermost first]}; `part_titles` names the split headings, `own_only` the units holding a split heading's text."""
        text = lambda ch, pids: sum(ch.size(p["pid"]) for p in ch.paragraphs
                                    if p["pid"] in pids and ch.teachable(p) and p["kind"] == "text")
        units, chains = [], {}
        self.part_titles, self.part_notes, self.own_only = {}, {}, set()

        def place(ch, above):
            kids = [c for c in scope.under(ch.id) if scope.visible(c)]
            if text(ch, {p["pid"] for p in ch.paragraphs}) <= Config.OUTLINE_UNIT_MAX_CHARS or not kids:
                units.append(ch)
                chains[ch.id] = above
                return
            self.part_titles[ch.id] = ch.title
            own = {p["pid"] for p in ch.paragraphs if p["heading"] == ch.id}
            if text(ch, own) >= Config.SECTION_SLIDE_MIN_CHARS:
                intro = load_chapters(kb_dir, self.index, [ch.id], scope.visible, own_only=[ch.id])[0]
                units.append(intro)
                chains[ch.id] = above + [ch.id]
                self.own_only.add(ch.id)
            else:
                self.part_notes[ch.id] = " ".join(ch.text_of(p["pid"]) for p in ch.paragraphs
                                                  if p["pid"] in own and ch.teachable(p) and p["kind"] == "text")
            for kid in load_chapters(kb_dir, self.index, kids, scope.visible):
                place(kid, above + [ch.id])

        for ch in load_chapters(kb_dir, self.index, roots, scope.visible):
            place(ch, [])
        return units, chains

    def _homes(self) -> List[dict]:
        """Where each unit sits among the deck's units, for the inventory's `home`: its headings, title and order."""
        return [{"id": c.id, "title": c.title, "order": n, "headings": set(c.heading_ids)}
                for n, c in enumerate(self.chapters)]

    def _field_sample(self):
        """What the field detector reads: the scope's headings, its most mentioned KG concepts, its opening text."""
        headings = [str(row.get("title") or "") for row in self.base.get("heading_tree") or []][:60]
        mentions = self.store.mentions
        concepts = sorted(mentions, key=lambda n: -len(mentions[n]))[:Config.DOMAIN_SAMPLE_CONCEPTS]
        names = [getattr(self.store, "_concept_names", {}).get(n, n) for n in concepts]
        opening = " ".join(c.text_of(p["pid"]) for c in self.chapters for p in c.paragraphs
                           if c.teachable(p) and p["kind"] == "text")[:Config.DOMAIN_SAMPLE_CHARS]
        return headings, names, opening

    def _deck_title(self, context, roots: List[int], root_heading, scope) -> str:
        """Short: the book's title for the whole document, the chapter's title for one, a subject title for several."""
        book = book_title(getattr(context, "source_file", ""))
        if not root_heading:
            return book or scope.display(scope.title_id) or context.document_id.replace("_", " ")
        return deck_title(self.model, [scope.display(r) for r in roots], book)

    def chapter_of(self, chapter_id) -> Any:
        return next(c for c in self.chapters if c.id == chapter_id)

    # ── Outline: one call per chapter (goals and slides), paragraphs given by code ──
    def plan(self) -> None:
        with ThreadPoolExecutor(max_workers=max(1, min(len(self.chapters), Config.PLANNER_MAX_CONCURRENCY))) as pool:
            for chapter_id, state in pool.map(lambda c: (c.id, self._plan_chapter(c)), self.chapters):
                self.state[chapter_id] = state
        if self.layer is not None:                   # the headings' inventories and the KG texts' vectors, kept
            self.layer.save()
            getattr(self.store.embedder, "save", lambda: None)()

    def labels(self) -> Dict[str, str]:
        """The deck's fixed words, in the language of the chapter with the most text."""
        chars = lambda c: sum(c.size(p["pid"]) for p in c.paragraphs if c.teachable(p) and p["kind"] == "text")
        return deck_labels.labels(self.model, max(self.chapters, key=chars).language) if self.chapters else {}

    def numbered(self, labels: Dict[str, str]) -> Dict[str, List[int]]:
        """{kind: numbers} the scope's own text gives a figure or a table under the deck's word for it ("given in
        Table 1"), so a visual the document prints no caption for is never named with one of them."""
        text = " ".join(s["text"] for c in self.chapters for s in c.sentences.values())
        out = {}
        for kind in ("figure", "table"):
            word = labels.get(kind) or kind.capitalize()
            out[kind] = sorted({int(n) for n in re.findall(rf"\b{re.escape(word)}\.?\s*(\d+)\b", text, re.IGNORECASE)})
        return out

    def _plan_chapter(self, chapter) -> dict:
        chapter.language = deck_labels.language(self.model, deck_labels.sample(chapter))
        heads = (self.index or {}).get("headings") or {}
        chapter.parent_of = {h: (heads.get(str(h)) or {}).get("parent") for h in chapter.heading_ids}
        inventory = (build_inventory(chapter, self.store, self.graph, self.index, homes=self._homes(), layer=self.layer)
                     if self.graph is not None
                     else {"nodes": {}, "relations": {}, "noise": [], "api": [], "ungrounded": []})
        planned = self.planner.plan(chapter, inventory, self.titles)
        print(f"[Outline] {chapter.title}: {len(planned['goals'])} goal(s), {len(planned['slides'])} slide(s) from "
              f"{planned['parts']} part(s) in {planned['calls']} call(s); KG {summary(inventory)['nodes']} node(s), "
              f"{summary(inventory)['relations']} link(s), {len(planned['left_kg'])} left")
        return {"chapter": chapter, "inventory": inventory, "goals": planned["goals"], "slides": planned["slides"],
                "left_kg": planned["left_kg"], "unplaced": planned["unplaced"], "calls": planned["calls"],
                "openers": planned.get("openers") or {}, "agenda": planned.get("agenda") or [],
                "packet_decisions": {}, "dropped": []}

    # ── Packet Builder: the agent decides, PacketAssembler assembles ──────────
    def assembler(self, chapter_id):
        state = self.state[chapter_id]
        if "assembler" not in state:
            state["assembler"] = PacketAssembler(state["chapter"], state["inventory"], self.retrieval[chapter_id],
                                                 self.kb_dir, getattr(self.store, "embedder", None))
        return state["assembler"]

    def build_packets(self) -> None:
        def one(chapter):
            state = self.state[chapter.id]
            goals = {g["gid"]: g for g in state["goals"]}
            state["packet_decisions"] = self.packet_agent.build(
                chapter, state["inventory"], state["slides"],
                lambda: self._fill(state, self.assembler(chapter.id), goals))
        with ThreadPoolExecutor(max_workers=max(1, min(len(self.chapters), Config.PLANNER_MAX_CONCURRENCY))) as pool:
            list(pool.map(one, self.chapters))
        self._deck()

    @staticmethod
    def _fill(state: dict, assembler, goals: Dict[str, dict]) -> None:
        used: set = set()
        owned = {p for s in state["slides"] for p in s["paragraphs"]} | set(state.get("agenda") or [])   # an opener's text is its own
        for slide in state["slides"]:
            goal = {} if slide.get("continues") else goals.get(slide["gid"], {})     # its goal opened an earlier slide
            slide["built"] = assembler.build(slide, goal, used, owned)
            used.update(slide["built"]["pool"])
            used.update(p for kind in ("formula", "table", "code") for a in slide["built"]["assets"][kind]
                        for p in a.get("pids") or [a["pid"]])
            used.update(str(a["id"]) for a in slide["built"]["assets"].get("images") or [])
        assembler.place_tables(state["slides"])
        assembler.place_left_figures(state["slides"], used)
        ChapterPipeline.known(state["inventory"], state["slides"])

    @staticmethod
    def known(inventory: dict, slides: List[dict], limit: int = 10) -> None:
        """Each slide's `known`: names of the nodes its passages state that another slide teaches (the map of where each
        idea is taught: `home`), so the Writer uses them by name instead of teaching them again. Names only: given the
        sentence that defines one there, the Writer defined it again (HOML Ch.3, 'Precision measures the accuracy of
        positive predictions' on the tradeoff slide after the confusion-matrix slide taught it)."""
        nodes = inventory["nodes"]
        home = {k: s for s in slides for k in s["teaches"]}
        for slide in slides:
            built = slide["built"]
            shown = {sid for row in built["pool"].values() for sid, _ in row["sentences"]}
            mine = {k for pk in built["packets"] for k in pk.get("teaches") or []}
            built["known"] = [nodes[k]["name"] for k in nodes if k in home and k not in mine and home[k] is not slide
                              and shown & set(nodes[k]["sentences"])][:limit]

    # ── the deck: specs, downstream packets and Writer jobs in deck order ─────
    def _deck(self) -> None:
        specs, packets, jobs = [], [], []
        opened = set()
        for chapter in self.chapters:
            state = self.state[chapter.id]
            for part in self.parts.get(chapter.id) or []:          # the first unit under a split heading: its opener
                if part not in opened:
                    opened.add(part)
                    self._opener(specs, packets, part, self.part_titles[part], "Part overview",
                                 notes=self.part_notes.get(part, ""))
            if chapter.id not in self.own_only:                    # a split heading's own text sits under its opener
                self._opener(specs, packets, chapter.id, chapter.title, "Chapter overview", chapter.id)
            goals = {g["gid"]: g for g in state["goals"]}
            before = {id(o["before"]): (hid, o) for hid, o in state.get("openers", {}).items() if hid != chapter.id}
            for n, slide in enumerate(state["slides"]):
                if id(slide) in before:                     # a part whose heading only introduces it: its divider
                    hid, o = before[id(slide)]
                    self._opener(specs, packets, hid, o["title"], o["goal"], chapter.id)
                built = slide["built"]
                assets = built["assets"]
                kind = (SlideType.HAVE_FORMULA if assets["formula"] else SlideType.HAVE_TABLE if assets["table"]
                        else SlideType.CONTENT)
                message = slide.get("message") or ("" if slide.get("continues") else slide["title"])
                spec = Slide(slide["title"], kind, message, kg_node_id=chapter.id,
                             section_heading=goals.get(slide["gid"], {}).get("goal", chapter.title),
                             kg_concepts=[pk["name"] for pk in built["packets"] if pk["name"]][:10],
                             slide_id=digest([chapter.id, slide["gid"], n, slide["title"]])[:20])
                slide["slide_id"] = spec.slide_id
                if assets["formula"]:
                    spec.latex_block_formula = clean_ocr_latex(assets["formula"][0]["normalized"])
                    spec.latex_formulas = [spec.latex_block_formula]
                if assets["table"]:
                    tables = [Table(t["normalized"], t.get("caption") or "", t.get("structured")) for t in assets["table"]]
                    spec.table, spec.more_tables = tables[0], tables[1:]
                spec.tables_not_drawn = list(assets.get("not_drawn") or [])
                if assets["code"] and not (assets["formula"] or assets["table"] or assets.get("images")):
                    spec.code_block = {"code": assets["code"][0]["text"], "language": assets["code"][0]["language"]}
                spec.grounding = {"facts": [], "scope_root_id": chapter.id,
                                  "unit_id": None, "goal": goals.get(slide["gid"], {}).get("goal", ""),
                                  "visual_plan": built["visual_plan"], "intent": {"kind": "content", "ideas": []}}
                packet = {"slide_title": slide["title"], "slide_id": spec.slide_id, "kg_node_id": chapter.id, "scope_root_id": chapter.id, "block_ids": [slide["gid"]],
                          "goal": spec.goal, "chapter_goal": spec.grounding["goal"], "questions": built["questions"],
                          "packets": built["packets"], "pool": built["pool"],
                          "assets": {k: v for k, v in assets.items() if k not in ("image", "images")},
                          "visual_plan": built["visual_plan"],
                          "section_assets": list(assets.get("images") or [])}
                self._evidence(spec, packet, built)
                specs.append(spec)
                packets.append(packet)
                jobs.append({"spec": spec, "built": built, "chapter": chapter.id})
        self.specs, self.packets, self.jobs = specs, packets, jobs
        self._agenda()
        self._number()

    @staticmethod
    def _opener(specs: List[Slide], packets: List[dict], hid: int, title: str, goal: str, root=None,
                notes: str = "") -> None:
        """A section divider (a split heading's, a chapter's, or a part of a chapter whose heading only introduces it)
        whose agenda `_agenda` fills; `root` is the chapter it belongs to (None for a split heading); `notes` its
        heading's own text too short for a slide, kept for the presenter."""
        opener = Slide(title, SlideType.CONTENT, goal, kg_node_id=hid, is_section_opener=True, section_heading=title,
                       slide_id=f"heading-{hid}-divider")
        opener.grounding = {"agenda": [], "scope_root_id": hid if root is None else root, "unit_id": None,
                            "visual_plan": {"type": "native_concept_map", "source": "structure"}}
        specs.append(opener)
        packets.append({"slide_title": title, "section_opener": True, "agenda_items": [], "slide_id": opener.slide_id,
                        "kg_node_id": hid, "scope_root_id": hid, "layout_hint": "agenda_layout", "visual_plan": opener.grounding["visual_plan"],
                        "source_pages": [], "section_assets": [], "evidence": "", "goal": goal, "notes": notes})

    @staticmethod
    def _evidence(spec: Slide, packet: dict, built: dict) -> None:
        """The slide's source facts (every pool sentence), pages and evidence text, from its current pool."""
        facts = [{"fact_id": sid, "item_id": pid, "kind": "text", "text": text, "concept": spec.slide_title,
                  "source_refs": [built["refs"][sid]], "origin": "sentence", "source_owned": True,
                  "grounding_mode": "anchored", "role": "evidence"}
                 for pid, row in built["pool"].items() for sid, text in row["sentences"] if sid in built["refs"]]
        facts += [{"fact_id": a["sid"], "item_id": a["pid"], "kind": kind, "text": a["text"],   # citable visuals
                   "concept": spec.slide_title, "source_refs": [built["refs"][a["sid"]]], "origin": kind,
                   "source_owned": True, "grounding_mode": "anchored", "role": "evidence"}
                  for kind in ("formula", "code") for a in built["assets"].get(kind) or [] if a["sid"] in built["refs"]]
        pages = sorted({int(row["page"]) for row in built["pool"].values() if str(row["page"]).isdigit()})
        spec.grounding["facts"] = facts
        packet.update({"pool": built["pool"], "facts": facts, "source_pages": pages, "home_pages": pages,
                       "evidence": "\n".join(t for row in built["pool"].values() for _, t in row["sentences"])})

    def refresh(self, slide_id: str) -> None:
        """Re-read one slide's pool into its spec and downstream packet (after Content QA added facts)."""
        n = next(i for i, s in enumerate(self.specs) if s.slide_id == slide_id)
        job = next(j for j in self.jobs if j["spec"].slide_id == slide_id)
        self._evidence(self.specs[n], self.packets[n], job["built"])

    def _agenda(self) -> None:
        """Each opener lists its chapter's goals that still have slides; a chapter without slides loses its opener."""
        present = {s.slide_id for s in self.specs}
        keep_specs, keep_packets = [], []
        for spec, packet in zip(self.specs, self.packets):
            if spec.is_section_opener and spec.kg_node_id in self.part_titles:    # its sections that still have slides
                part = spec.kg_node_id
                live = lambda c: any(s.get("slide_id") in present for s in self.state[c.id]["slides"])
                agenda = []
                for c in self.chapters:
                    chain = self.parts.get(c.id) or []
                    if part not in chain or c.id == part or not live(c):    # its own text follows it, unlisted
                        continue
                    below = chain[chain.index(part) + 1:]           # the section right under it on the way to c
                    item = self.part_titles[below[0]] if below else c.title
                    if item not in agenda:
                        agenda.append(item)
                if not agenda:
                    continue
                spec.grounding["agenda"] = packet["agenda_items"] = agenda
                packet["evidence"] = "\n".join(agenda)
            elif spec.is_section_opener:
                state = self.state[spec.grounding["scope_root_id"]]
                live = {s["gid"] for s in state["slides"] if s.get("slide_id") in present}
                opener = state.get("openers", {}).get(spec.kg_node_id)
                if opener:                                  # its subheadings' goals that still have slides
                    agenda = [it["text"] for it in opener["agenda"] if any(s.get("slide_id") in present for s in it["slides"])]
                else:
                    agenda = [g["goal"] for g in state["goals"] if g["gid"] in live]     # whole: the deck fits them
                if not agenda:
                    continue
                spec.grounding["agenda"] = agenda
                packet["agenda_items"] = agenda
                packet["evidence"] = "\n".join(agenda)
            keep_specs.append(spec)
            keep_packets.append(packet)
        self.specs, self.packets = keep_specs, keep_packets

    def _number(self) -> None:
        for number, (spec, packet) in enumerate(zip(self.specs, self.packets), 1):
            spec.slide_number = number
            packet["slide_number"] = number

    def drop(self, slide_ids: Iterable[str], reason: str) -> None:
        """Remove content slides from the deck (never merged elsewhere), then renumber and refresh the agendas."""
        gone = set(slide_ids)
        if not gone:
            return
        lost_figures = []
        for chapter_id, state in self.state.items():
            leaving = [s for s in state["slides"] if s.get("slide_id") in gone]
            state["dropped"] += [{"title": s["title"], "slide_id": s.get("slide_id"), "reason": reason} for s in leaving]
            state["slides"] = [s for s in state["slides"] if s.get("slide_id") not in gone]
            if any((s.get("built") or {}).get("assets", {}).get("images") for s in leaving):
                lost_figures.append(chapter_id)
        keep = [(s, p) for s, p in zip(self.specs, self.packets) if s.slide_id not in gone]
        self.specs, self.packets = [s for s, _ in keep], [p for _, p in keep]
        for chapter_id in lost_figures:
            self._place_lost_figures(chapter_id)
        self.jobs = [j for j in self.jobs if j["spec"].slide_id not in gone]
        self._agenda()
        self._number()

    def _place_lost_figures(self, chapter_id) -> None:
        """The figures of a slide that went take a place on the chapter's other slides, and their packets show them."""
        slides = self.state[chapter_id]["slides"]
        used = {str(a["id"]) for s in slides for a in s["built"]["assets"].get("images") or []}
        self.assembler(chapter_id).place_left_figures(slides, used)
        by_id = {s.get("slide_id"): s for s in slides}
        for spec, packet in zip(self.specs, self.packets):
            slide = by_id.get(spec.slide_id)
            if slide:
                packet["section_assets"] = list(slide["built"]["assets"].get("images") or [])
                packet["visual_plan"] = spec.grounding["visual_plan"] = slide["built"]["visual_plan"]

    # ── after writing: where every paragraph and KG item ended up ─────────────
    def ledger(self, contents: Dict[str, object]) -> Dict[str, dict]:
        """{chapter id: {paragraphs, nodes, links}}. `contents` maps slide_id -> SlideContent."""
        out = {}
        for chapter in self.chapters:
            state = self.state[chapter.id]
            cited, pooled, covered, left = set(), set(), set(), dict(state["left_kg"])
            for slide in state["slides"]:
                content = contents.get(slide.get("slide_id"))
                pooled.update(s for row in slide["built"]["pool"].values() for s, _ in row["sentences"])
                if content is None:
                    continue
                cited.update(s for group in content.fact_ids for s in group)
                structure = content.layout_structure or {}
                covered.update(k for pk in slide["built"]["packets"] if not pk.get("notes")
                               and pk["id"] not in structure.get("uncovered", []) and pk["id"] not in structure.get("left", {})
                               for k in pk.get("teaches") or [])
                left.update({k: r for k, r in (structure.get("left") or {}).items() if k != "ctx"})
            para, text = Counter(), Counter()
            agenda = set(state.get("agenda") or [])
            for p in chapter.paragraphs:
                if not chapter.teachable(p) or p["kind"] == "code":
                    continue
                sids = set(p["sentences"])
                fate_of = ("on_slide" if sids & cited else "in_packet" if sids & pooled
                           else "agenda" if p["pid"] in agenda else "none")
                para[fate_of] += 1
                if p["kind"] == "text":
                    text[fate_of] += chapter.size(p["pid"])
            inv = state["inventory"]
            def fate(item_id, sentences):
                if item_id in covered or set(sentences) & cited:
                    return "on_slide"
                if item_id in left:
                    return "left"
                return "in_packet" if set(sentences) & pooled else "none"
            nodes = Counter(fate(k, n["sentences"]) for k, n in inv["nodes"].items())
            links = Counter(fate(r, x["sentences"]) for r, x in inv["relations"].items())
            total = lambda c: max(1, sum(v for k, v in c.items() if k != "agenda"))   # an opener's text is not taught
            out[str(chapter.id)] = {
                "chapter": chapter.title, "chapter_id": chapter.id,
                "paragraphs": dict(para), "paragraph_slide_rate": round(para["on_slide"] / total(para), 4),
                "text_chars": dict(text), "text_slide_rate": round(text["on_slide"] / total(text), 4),
                "nodes": dict(nodes), "node_slide_rate": round(nodes["on_slide"] / total(nodes), 4),
                "links": dict(links), "link_slide_rate": round(links["on_slide"] / total(links), 4)}
        return out

    def report(self) -> List[dict]:
        """Per chapter: inventory, goals and their slides with each slide's packets (for review)."""
        out = []
        for chapter in self.chapters:
            state = self.state[chapter.id]
            out.append({
                "chapter": chapter.title, "chapter_id": chapter.id, "kg_inventory": summary(state["inventory"]),
                "left_kg": state["left_kg"], "unplaced": state["unplaced"], "planner_calls": state["calls"],
                "dropped": state["dropped"],
                "goals": [{"gid": g["gid"], "goal": g["goal"], "questions": g["questions"],
                           "slides": [{"title": s["title"], "message": s.get("message", ""),
                                       "teaches": s["teaches"], "links": s["links"], "paragraphs": s["paragraphs"],
                                       "key": s.get("key"),
                                       "named": (s.get("built") or {}).get("named"),
                                       "size": (s.get("built") or {}).get("size")}
                                      for s in state["slides"] if s["gid"] == g["gid"]]}
                          for g in state["goals"]]})
        return out

    def manifest(self, ledger: Dict[str, dict] = None) -> dict:
        return {**self.base, "ledger": ledger or {}}
