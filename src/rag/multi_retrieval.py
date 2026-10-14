"""Retrieval inside one chapter over every channel the KB has, returning whole paragraphs for an agent to read.

One query is scored by `EvidenceStore.retrieve_scoped`, which fuses five signals by reciprocal rank: sentence embedding
cosine, BM25 keyword match, KG `mentions` of the query's concepts, MiniRAG chunk hits and MiniRAG entity hits expanded
through their source chunks and graph neighbours. The display gate is off (`gated=False`): colon lead-ins, list items
and sentences with inline maths are content for a reader that paraphrases.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Sequence, Tuple

from src.rag.chapter_map import Chapter
from src.rag.idea_candidates import normalize_name


class ChapterRetrieval:
    def __init__(self, store, chapter: Chapter):
        self.store, self.chapter = store, chapter

    def keywords(self, text: str) -> List[str]:
        return [self.store._concept_names.get(n, n) for n in self.store.query_concepts(text)]

    def sentences(self, query: str, keywords: Sequence[str] = (), k: int = 8, exclude: Iterable[str] = ()) -> List[dict]:
        """Top sentences of the chapter for `query`, each with its paragraph id."""
        hits = self.store.retrieve_scoped(query, self.chapter.heading_ids, keywords=list(keywords) or self.keywords(query),
                                          k=k, exclude=exclude, role="chapters", gated=False)
        return [{**h, "pid": self.chapter.pid_of.get(h["id"])} for h in hits if h["id"] in self.chapter.pid_of]

    def paragraphs(self, query: str, keywords: Sequence[str] = (), k: int = 4) -> List[Tuple[str, float]]:
        """Paragraphs ranked by their best sentences for `query` (sum of the two best fused scores)."""
        scores: Dict[str, List[float]] = defaultdict(list)
        for hit in self.sentences(query, keywords, k=k * 4):
            scores[hit["pid"]].append(hit["score"])
        ranked = sorted(((pid, sum(sorted(v, reverse=True)[:2])) for pid, v in scores.items()), key=lambda r: -r[1])
        return ranked[:k]

    def mentioning(self, concepts: Sequence[str]) -> Dict[str, int]:
        """Paragraphs naming any of `concepts` (KG mention index), with how many of their sentences do."""
        out: Dict[str, int] = defaultdict(int)
        for name in {normalize_name(c) for c in concepts}:
            for i in self.store.mentions.get(name, ()):
                pid = self.chapter.pid_of.get(self.store.sentences[i]["id"])
                if pid:
                    out[pid] += 1
        return dict(out)
