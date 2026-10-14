"""The Outline agent's query tool (code, 0 LLM): what each section of a chapter is about, from the KG and the text.

The table of contents gives the sections and their content (the teachable paragraphs under each heading). The KG
inventory gives each section's ideas: a node belongs to the section holding most of its anchor paragraphs, and it is a
CENTRAL idea when the KG says this chapter teaches it (no other section names it most, and more than a passing
mention). A section's EDGES are the relations the extractor drew from a chunk of this chapter whose stating sentence is
in the section (its own words: what is what, made of what, used for what, as the edge says), in reading order, with
their ends wherever those are at home; a relation of its ideas stated elsewhere only counts as a reference. Retrieval over the section's own sentences (the five fused
signals of `EvidenceStore.retrieve_scoped`: embeddings, keywords, KG mentions, MiniRAG chunk and entity hits) gives the
few sentences that state what the section says, so a slide's title and message come from the source, not from names.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, List, Optional

from src.rag.retrieve_content import load_asset_headings
from src.workflow.packet_assembly import cells_key
from src.utils.config import Config


def central(node: dict) -> bool:
    """The chapter teaches this idea: it is at home here and named in more than a passing mention."""
    return not node.get("home") and node.get("span", 0) > Config.LEAVE_INCIDENTAL_SPAN


class SectionQuery:
    def __init__(self, chapter, inventory: dict, titles: Dict[int, str], retrieval=None, kb_dir=None):
        self.chapter, self.inventory, self.titles = chapter, inventory, titles
        self.retrieval, self.kb_dir = retrieval, kb_dir

    def home_section(self, node: dict) -> Optional[int]:
        """The heading holding most of the node's anchor paragraphs (the earliest on a tie)."""
        by_pid = self.chapter.by_pid
        counts = Counter(by_pid[p]["heading"] for p in node.get("pids") or [] if p in by_pid)
        if not counts:
            return None
        order = {h: n for n, h in enumerate(self.chapter.heading_ids)}
        return max(counts, key=lambda h: (counts[h], -order.get(h, 0)))

    def _figures(self) -> Dict[int, List[str]]:
        """The figures under each heading by their own caption ("" for an uncaptioned one the vision model calls
        content): each counts in its block's slides."""
        out: Dict[int, List[str]] = {}
        for a in (load_asset_headings(self.kb_dir) or []) if self.kb_dir else []:
            if a.get("type") == "image" and a.get("classification") != "decorative":
                try:
                    out.setdefault(int(a.get("node")), []).append(" ".join(str(a.get("real_caption") or "").split()))
                except (TypeError, ValueError):
                    continue
        return out

    def _table_label(self, pid: str) -> str:
        """What a table is about: the document's caption, else its header row."""
        unit = self.chapter.sentences[self.chapter.by_pid[pid]["sentences"][0]]
        structured = unit.get("structured_table") or {}
        for a in (load_asset_headings(self.kb_dir) or []) if self.kb_dir else []:
            if a.get("type") == "table" and a.get("real_caption") and cells_key(a.get("structured_table")) == cells_key(structured):
                return " ".join(str(a["real_caption"]).split())
        return " | ".join(str(h) for h in structured.get("headers") or [])

    def sections(self) -> List[dict]:
        """[{hid, title, pids, chars, assets, tables, figures, ideas, central, links, edges, ends, elsewhere}] in reading
        order, for every heading of the chapter that holds teachable paragraphs. `ideas` / `central` are K# whose home
        section it is; `links` are R# between two of its ideas; `edges` the local R# stated in it (reading order),
        `ends` their K# at home in another section, `elsewhere` how many relations of its ideas other sections state."""
        ch, nodes = self.chapter, self.inventory["nodes"]
        figures = self._figures()
        out: Dict[int, dict] = {}
        for p in ch.paragraphs:
            if not ch.teachable(p):
                continue
            hid = p["heading"]
            row = out.setdefault(hid, {"hid": hid, "title": self.titles.get(hid) or ch.title, "pids": [], "chars": 0,
                                       "assets": Counter(), "tables": [], "ideas": [], "central": [], "links": [],
                                       "edges": [], "ends": [], "elsewhere": 0})
            row["pids"].append(p["pid"])
            if p["kind"] == "text":
                row["chars"] += ch.size(p["pid"])
            else:
                row["assets"][p["kind"]] += 1
                if p["kind"] == "table":
                    row["tables"].append(self._table_label(p["pid"]))
        for hid, row in out.items():
            row["figures"] = figures.get(hid, [])
        section_of = {}
        for k, node in nodes.items():
            hid = self.home_section(node)
            if hid in out:
                section_of[k] = hid
                out[hid]["ideas"].append(k)
                if central(node):
                    out[hid]["central"].append(k)
        position = {sid: n for n, sid in enumerate(sid for p in ch.paragraphs for sid in p["sentences"])}
        stated_in = lambda rel: next((ch.by_pid[ch.pid_of[sid]]["heading"] for sid in rel["sentences"]
                                      if sid in ch.pid_of), None)
        relations = self.inventory["relations"]
        for r in sorted(relations, key=lambda r: min((position.get(s, 0) for s in relations[r]["sentences"]), default=0)):
            rel = relations[r]
            a, b = section_of.get(rel["kid_a"]), section_of.get(rel["kid_b"])
            if a is not None and a == b:
                out[a]["links"].append(r)
            here = stated_in(rel) if rel.get("local") else None
            if here in out:
                out[here]["edges"].append(r)
                out[here]["ends"] += [k for k in (rel["kid_a"], rel["kid_b"])
                                      if section_of.get(k) != here and k not in out[here]["ends"]]
            for k in {rel["kid_a"], rel["kid_b"]}:
                if section_of.get(k) in out and section_of[k] != here:
                    out[section_of[k]]["elsewhere"] += 1
        order = {h: n for n, h in enumerate(ch.heading_ids)}
        return sorted(out.values(), key=lambda row: order.get(row["hid"], 0))

    def gist(self, hids: List[int], query: str, keywords: List[str], k: int) -> List[dict]:
        """The k sentences of these headings retrieval finds closest to `query`, each {id, pid, text}; [] without a
        retriever. A failure (the embedding API still unanswered after its client's retries) stops the run."""
        if self.retrieval is None or k <= 0:
            return []
        try:
            hits = self.retrieval.store.retrieve_scoped(query, hids, keywords=keywords, k=k, role="outline",
                                                        gated=False)
        except Exception as exc:
            raise RuntimeError(f"[outline] retrieval failed for '{query[:60]}' after {Config.EMBED_MAX_RETRIES} "
                               f"retries: {type(exc).__name__}: {str(exc)[:160]}") from exc
        pid_of = self.chapter.pid_of
        return [{"id": h["id"], "pid": pid_of[h["id"]], "text": h["text"]} for h in hits if h["id"] in pid_of][:k]
