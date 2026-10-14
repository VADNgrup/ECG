"""The lecture pipeline for one deck. Each top-level section of the scope (a chapter) goes through the four agents:
KG inventory -> Lecture Planner (complete slides per chapter part, then the goals) -> Packet Builder (what each slide's
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
from src.rag.kg_inventory import build_inventory, summary
from src.rag.multi_retrieval import ChapterRetrieval
from src.rag.retrieve_content import load_kg_graph, load_structural_index
from src.utils.config import Config
from src.utils.content_cache import digest
from src.workflow.agents.lecture_planner import LecturePlannerAgent
from src.workflow.agents.packet_builder import PacketBuilderAgent
from src.workflow.deck_title import book_title, deck_title
from src.workflow.packet_assembly import PacketAssembler, clean_ocr_latex
from src.workflow.scope import find_kb_dir, ScopeOrchestrator

_AGENDA_CHARS = 90


class ChapterPipeline:
    def __init__(self, model: str):
        self.model = model
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
        self.chapters = load_chapters(kb_dir, self.index, roots, scope.visible)
        self.retrieval = {c.id: ChapterRetrieval(self.store, c) for c in self.chapters}
        try:
            self.graph = load_kg_graph(kb_dir)
        except Exception as exc:
            print(f"[chapters] KG graph unavailable ({type(exc).__name__}); planning without it")
            self.graph = None
        self.planner = LecturePlannerAgent(self.model)
        self.packet_agent = PacketBuilderAgent(self.model)
        self.titles = {int(h): v.get("title", "") for h, v in (self.index.get("headings") or {}).items()
                       if str(h).isdigit()}
        return self._deck_title(context, roots, root_heading, scope)

    def _deck_title(self, context, roots: List[int], root_heading, scope) -> str:
        """Short: the book's title for the whole document, the chapter's title for one, a subject title for several."""
        book = book_title(getattr(context, "source_file", ""))
        if not root_heading:
            return book or scope.display(scope.title_id) or context.document_id.replace("_", " ")
        return deck_title(self.model, [scope.display(r) for r in roots], book)

    def chapter_of(self, chapter_id) -> Any:
        return next(c for c in self.chapters if c.id == chapter_id)

    # ── Lecture Planner: one call per chapter part, then the goals ────────────
    def plan(self) -> None:
        with ThreadPoolExecutor(max_workers=max(1, min(len(self.chapters), Config.PLANNER_MAX_CONCURRENCY))) as pool:
            for chapter_id, state in pool.map(lambda c: (c.id, self._plan_chapter(c)), self.chapters):
                self.state[chapter_id] = state

    def _plan_chapter(self, chapter) -> dict:
        inventory = (build_inventory(chapter, self.store, self.graph, self.index) if self.graph is not None
                     else {"nodes": {}, "relations": {}, "noise": [], "api": [], "ungrounded": []})
        planned = self.planner.plan(chapter, inventory, self.titles)
        print(f"[chapters] {chapter.title}: {len(planned['goals'])} goal(s), {len(planned['slides'])} slide(s) from "
              f"{planned['parts']} part(s) in {planned['calls']} call(s); KG {summary(inventory)['nodes']} node(s), "
              f"{summary(inventory)['relations']} link(s), {len(planned['left_kg'])} left")
        return {"chapter": chapter, "inventory": inventory, "goals": planned["goals"], "slides": planned["slides"],
                "left_kg": planned["left_kg"], "unplaced": planned["unplaced"], "calls": planned["calls"],
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
        owned = {p for s in state["slides"] for p in s["paragraphs"]}
        for slide in state["slides"]:
            slide["built"] = assembler.build(slide, goals.get(slide["gid"], {}), used, owned)
            used.update(slide["built"]["pool"])
            used.update(p for kind in ("formula", "table", "code") for a in slide["built"]["assets"][kind]
                        for p in a.get("pids") or [a["pid"]])
            used.update(str(a["id"]) for a in slide["built"]["assets"].get("images") or [])
        ChapterPipeline.known(state["inventory"], state["slides"])

    @staticmethod
    def known(inventory: dict, slides: List[dict], limit: int = 10) -> None:
        """Each slide's `known`: names of the nodes its passages state that another slide teaches, so the Writer uses
        them by name instead of teaching them again."""
        nodes = inventory["nodes"]
        home = {k: s["title"] for s in slides for k in s["teaches"]}
        for slide in slides:
            built = slide["built"]
            shown = {sid for row in built["pool"].values() for sid, _ in row["sentences"]}
            mine = {k for pk in built["packets"] for k in pk.get("teaches") or []}
            built["known"] = [nodes[k]["name"] for k in nodes
                              if k in home and k not in mine and home[k] != slide["title"]
                              and shown & set(nodes[k]["sentences"])][:limit]

    # ── the deck: specs, downstream packets and Writer jobs in deck order ─────
    def _deck(self) -> None:
        specs, packets, jobs = [], [], []
        for chapter in self.chapters:
            state = self.state[chapter.id]
            opener = Slide(chapter.title, SlideType.CONTENT, "Chapter overview", kg_node_id=chapter.id,
                           is_section_opener=True, section_heading=chapter.title, slide_id=f"heading-{chapter.id}-divider")
            opener.grounding = {"agenda": [], "scope_root_id": chapter.id, "unit_id": None,
                                "packet_version": "chapters-v2",
                                "visual_plan": {"type": "native_concept_map", "source": "structure"}}
            specs.append(opener)
            packets.append({"slide_title": chapter.title, "section_opener": True, "agenda_items": [],
                            "slide_id": opener.slide_id, "kg_node_id": chapter.id, "scope_root_id": chapter.id,
                            "packet_version": "chapters-v2", "layout_hint": "agenda_layout",
                            "visual_plan": opener.grounding["visual_plan"], "source_pages": [], "section_assets": [],
                            "evidence": "", "goal": "Chapter overview"})
            goals = {g["gid"]: g for g in state["goals"]}
            for n, slide in enumerate(state["slides"]):
                built = slide["built"]
                assets = built["assets"]
                kind = (SlideType.HAVE_FORMULA if assets["formula"] else SlideType.HAVE_TABLE if assets["table"]
                        else SlideType.CONTENT)
                spec = Slide(slide["title"], kind, slide.get("message") or slide["title"], kg_node_id=chapter.id,
                             section_heading=goals.get(slide["gid"], {}).get("goal", chapter.title),
                             kg_concepts=[pk["name"] for pk in built["packets"] if pk["name"]][:10],
                             slide_id=digest(["chapters-v2", chapter.id, slide["gid"], n, slide["title"]])[:20])
                slide["slide_id"] = spec.slide_id
                if assets["formula"]:
                    spec.latex_block_formula = clean_ocr_latex(assets["formula"][0]["normalized"])
                    spec.latex_formulas = [spec.latex_block_formula]
                if assets["table"]:
                    spec.table = Table(assets["table"][0]["normalized"], "", assets["table"][0].get("structured"))
                if assets["code"] and not (assets["formula"] or assets["table"] or assets.get("images")):
                    spec.code_block = {"code": assets["code"][0]["text"], "language": assets["code"][0]["language"]}
                spec.grounding = {"packet_version": "chapters-v2", "facts": [], "scope_root_id": chapter.id,
                                  "unit_id": None, "goal": goals.get(slide["gid"], {}).get("goal", ""),
                                  "visual_plan": built["visual_plan"], "intent": {"kind": "content", "ideas": []}}
                packet = {"slide_title": slide["title"], "slide_id": spec.slide_id, "packet_version": "chapters-v2",
                          "kg_node_id": chapter.id, "scope_root_id": chapter.id, "block_ids": [slide["gid"]],
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
            if spec.is_section_opener:
                state = self.state[spec.kg_node_id]
                live = {s["gid"] for s in state["slides"] if s.get("slide_id") in present}
                if not live:
                    continue
                agenda = [g["goal"] if len(g["goal"]) <= _AGENDA_CHARS
                          else g["goal"][:_AGENDA_CHARS].rsplit(" ", 1)[0] + " …"
                          for g in state["goals"] if g["gid"] in live]
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
        for state in self.state.values():
            state["dropped"] += [{"title": s["title"], "slide_id": s.get("slide_id"), "reason": reason}
                                 for s in state["slides"] if s.get("slide_id") in gone]
            state["slides"] = [s for s in state["slides"] if s.get("slide_id") not in gone]
        keep = [(s, p) for s, p in zip(self.specs, self.packets) if s.slide_id not in gone]
        self.specs, self.packets = [s for s, _ in keep], [p for _, p in keep]
        self.jobs = [j for j in self.jobs if j["spec"].slide_id not in gone]
        self._agenda()
        self._number()

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
            para = Counter()
            for p in chapter.paragraphs:
                if not chapter.teachable(p) or p["kind"] == "code":
                    continue
                sids = set(p["sentences"])
                para["on_slide" if sids & cited else "in_packet" if sids & pooled else "none"] += 1
            inv = state["inventory"]
            def fate(item_id, sentences):
                if item_id in covered or set(sentences) & cited:
                    return "on_slide"
                if item_id in left:
                    return "left"
                return "in_packet" if set(sentences) & pooled else "none"
            nodes = Counter(fate(k, n["sentences"]) for k, n in inv["nodes"].items())
            links = Counter(fate(r, x["sentences"]) for r, x in inv["relations"].items())
            total = lambda c: max(1, sum(c.values()))
            out[str(chapter.id)] = {
                "chapter": chapter.title, "chapter_id": chapter.id,
                "paragraphs": dict(para), "paragraph_slide_rate": round(para["on_slide"] / total(para), 4),
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
                                       "key": s.get("key"), "notes": s.get("notes"),
                                       "named": (s.get("built") or {}).get("named"),
                                       "size": (s.get("built") or {}).get("size")}
                                      for s in state["slides"] if s["gid"] == g["gid"]]}
                          for g in state["goals"]]})
        return out

    def manifest(self, ledger: Dict[str, dict] = None) -> dict:
        return {**self.base, "mode": "chapters-v2", "ledger": ledger or {}}
