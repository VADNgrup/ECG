"""Evidence store of ONE document's KB: every sentence of every chapter, split into two tiers.

- `scope`    : sentences under the headings the deck presents. The ONLY tier slide facts may come from.
- `document` : the rest of the same document. Used for verification, resolving cross-references, background speaker
               notes and prerequisite detection; never as slide facts.

Retrieval goes through the KB's MiniRAG stores (`MiniRAGRetriever`: chunk vector store, entity vector stores, entity
graph): MiniRAG nominates chunks and entities, the sentences inside them are ranked by embedding cosine and fused (reciprocal
rank) with the chunk rank and the KG mentions. The returned text is always a source sentence with its ID and offsets.
"""
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

from src.rag import minirag_retriever
from src.rag.embedder import embed_transient, Embedder
from src.rag.idea_candidates import normalize_name, scope_sentences
from src.rag.kg_schema import is_semantic_node
from src.rag.retrieve_content import load_kg_graph, load_structural_index, scope_ids
from src.rag.unit_roles import role_of
from src.utils import lang
from src.utils.config import Config
from src.utils.llm import log_retrieval

SCOPE, DOCUMENT = "scope", "document"
_NON_IDEA_SEMANTIC_TYPES = {"code", "person", "organization"}
_RRF_K = 60
_BM25_K1, _BM25_B = 1.2, 0.75


def _informative(clean: str) -> bool:
    """A sentence that can carry information: not a lead-in ("...:"), not a question, not a fragment."""
    clean = (clean or "").strip()
    return lang.length_units(clean) >= 6 and not clean.endswith((":", "?", "：", "？"))


def sayable(clean: str) -> bool:
    """An informative sentence a presenter can say aloud: it ends like a statement (a code line or a label does not)."""
    return _informative(clean) and clean.rstrip().endswith(tuple(lang._TERMINATORS))


def _contains(low: str, name: str) -> bool:
    """`name` occurs in `low` as a term: word boundaries for spaced scripts (an inflection of up to 2 letters is tolerated),
    plain containment for scripts without spaces."""
    if lang._NO_SPACE_SCRIPTS.search(name):
        return name in low
    end = len(name)
    at = low.find(name)
    while at != -1:
        tail = re.match(r"\w{0,2}(?!\w)", low[at + end:])
        if (at == 0 or not low[at - 1].isalnum()) and tail:
            return True
        at = low.find(name, at + 1)
    return False


class EvidenceStore:
    def __init__(self, kb_dir: Path, scope_root_ids: Sequence[int], embedder: Optional[Embedder] = None,
                 retriever: Optional["minirag_retriever.MiniRAGRetriever"] = None):
        self.kb_dir = Path(kb_dir)
        self.retriever = retriever if retriever is not None else minirag_retriever.MiniRAGRetriever(self.kb_dir)
        self.index = load_structural_index(self.kb_dir)
        headings = self.index.get("headings") or {}
        self.scope: Set[int] = scope_ids(self.index, list(scope_root_ids)) if scope_root_ids else set(map(int, headings))
        by_heading = scope_sentences(self.kb_dir, self.index, set(map(int, headings)))
        self.embedder = embedder or Embedder(cache_dir=self.kb_dir)
        self.sentences: List[dict] = []
        self.by_id: Dict[str, dict] = {}
        self.by_chunk: Dict[str, List[int]] = {}
        for hid, data in sorted(by_heading.items(), key=lambda kv: kv[1]["order"]):
            chapter = self._chapter(hid)
            for s in data["sentences"]:
                if s["kind"] != "text":
                    continue
                row = {"id": s["id"], "text": s["clean"], "heading": hid, "heading_path": self._path(hid), "chapter": chapter,
                       "tier": SCOPE if hid in self.scope else DOCUMENT, "order": len(self.sentences), "ref": s.get("ref"),
                       "sentence": s}
                self.sentences.append(row)
                self.by_id[row["id"]] = row
                self.by_chunk.setdefault((s.get("ref") or {}).get("chunk_id"), []).append(len(self.sentences) - 1)
        self.idf = lang.build_idf(s["text"] for s in self.sentences)
        self._mentions: Optional[Dict[str, List[int]]] = None
        self._neighbors: Optional[Dict[str, Set[str]]] = None
        self._neighbor_weights: Optional[Dict[Tuple[str, str], float]] = None
        self._concept_names: Dict[str, str] = {}

    # ── structure ────────────────────────────────────────────────────────────────────────────────────────────────────
    def _chain(self, hid: int) -> List[dict]:
        headings, out, cur = self.index["headings"], [], hid
        while cur is not None and str(cur) in headings:
            out.append(headings[str(cur)])
            cur = headings[str(cur)].get("parent")
            cur = int(cur) if cur is not None else None
        return list(reversed(out))

    def _path(self, hid: int) -> List[str]:
        return [h.get("title", "") for h in self._chain(hid)]

    def _chapter(self, hid: int) -> str:
        chain = [h for h in self._chain(hid) if h.get("level", 1) >= 1]
        return (chain[0] if chain else {}).get("title", "")

    def tier_of(self, sentence_id: str) -> Optional[str]:
        row = self.by_id.get(sentence_id)
        return row["tier"] if row else None

    def leaks(self, sentence_ids: Iterable[str]) -> List[str]:
        """IDs among `sentence_ids` that belong to the document tier: never allowed as slide facts."""
        return [i for i in sentence_ids if self.tier_of(i) == DOCUMENT]

    # ── indexes (built lazily) ───────────────────────────────────────────────────────────────────────────────────────
    def _build_graph_index(self) -> None:
        graph = load_kg_graph(self.kb_dir)
        names = {}
        for key, data in graph.nodes(data=True):
            if not is_semantic_node(data) or str(data.get("entity_type", "")).lower() in _NON_IDEA_SEMANTIC_TYPES:
                continue
            name = normalize_name(key)
            if len(name) >= 2:
                names.setdefault(name, key)
        lows = [s["text"].casefold() for s in self.sentences]
        self._mentions = {}
        for name in names:
            hits = [i for i, low in enumerate(lows) if name in low and _contains(low, name)]
            if hits:
                self._mentions[name] = hits
        key_to_name = {names[n]: n for n in self._mentions}
        self._neighbors = defaultdict(set)
        self._neighbor_weights = {}
        for u, v, edge in graph.edges(data=True):
            if u in key_to_name and v in key_to_name and u != v:
                left, right = key_to_name[u], key_to_name[v]
                self._neighbors[left].add(right)
                self._neighbors[right].add(left)
                try:
                    raw_weight = max(0.0, float(edge.get("weight", 1.0)))
                except (TypeError, ValueError):
                    raw_weight = 1.0
                weight = min(1.0, math.log1p(raw_weight) / math.log(10.0))
                self._neighbor_weights[left, right] = weight
                self._neighbor_weights[right, left] = weight
        self._concept_names = {n: str(names[n]) for n in self._mentions}

    @property
    def mentions(self) -> Dict[str, List[int]]:
        if self._mentions is None:
            self._build_graph_index()
        return self._mentions

    @property
    def neighbors(self) -> Dict[str, Set[str]]:
        if self._neighbors is None:
            self._build_graph_index()
        return self._neighbors

    def neighbor_weight(self, left: str, right: str) -> float:
        if self._neighbors is None:
            self._build_graph_index()
        return (getattr(self, "_neighbor_weights", None) or {}).get((left, right), 1.0)

    # ── retrieval ────────────────────────────────────────────────────────────────────────────────────────────────────
    def query_concepts(self, text: str) -> List[str]:
        low = text.casefold()
        return [n for n in self.mentions if n in low and _contains(low, n)]

    def _candidates(self, query: str, names: List[str], vector=None) -> Tuple[Dict[int, float], Dict[int, float]]:
        """Sentence indexes MiniRAG points at, with the rank score of their chunk and their KG-mention score."""
        chunk_score: Dict[int, float] = {}
        if hasattr(self.retriever, "hybrid_hits"):
            chunk_hits, entity_hits = self.retriever.hybrid_hits(
                query, Config.RETRIEVE_CHUNKS, Config.RETRIEVE_ENTITIES, vector=vector,
                vector_model=getattr(self.embedder, "model", None))
        else:  # compatibility with custom/test retrievers
            chunk_hits = self.retriever.chunk_hits(query, Config.RETRIEVE_CHUNKS)
            entity_hits = self.retriever.entity_hits(query, Config.RETRIEVE_ENTITIES)
        for rank, (chunk_id, _) in enumerate(chunk_hits):
            for i in self.by_chunk.get(chunk_id, ()):
                chunk_score[i] = 1.0 / (_RRF_K + rank + 1)
        entities = [normalize_name(n) for n, _ in entity_hits]
        graph: Dict[int, float] = {}
        for name in dict.fromkeys(names + [e for e in entities if e in self.mentions]):
            for i in self.mentions.get(name, ()):
                graph[i] = graph.get(i, 0.0) + 1.0
            for other in self.neighbors.get(name, ()):
                for i in self.mentions.get(other, ()):
                    graph[i] = graph.get(i, 0.0) + 0.4 * self.neighbor_weight(name, other)
        return chunk_score, graph

    def retrieve(self, query: str, tier: Optional[str] = None, k: int = 5, concepts: Sequence[str] = (),
                 exclude: Iterable[str] = (), role: str = "agent") -> List[dict]:
        """Top-k source sentences for `query` (one tier when given). MiniRAG proposes the chunks and entities; the sentences
        inside are ranked by cosine to the query and fused with the chunk rank and the KG mentions. When MiniRAG is
        unavailable every allowed sentence is a candidate (cosine only)."""
        log_retrieval(role)
        skip = set(exclude)
        names = [normalize_name(c) for c in concepts] or self.query_concepts(query)
        query_vector = embed_transient(self.embedder, [query])[0]
        chunk_score, graph = self._candidates(query, names, query_vector) if self.retriever.available else ({}, {})
        pool = set(chunk_score) | set(graph)
        if not pool:
            pool = set(range(len(self.sentences)))
        pool = [i for i in sorted(pool) if (tier is None or self.sentences[i]["tier"] == tier) and self.sentences[i]["id"] not in skip]
        if not pool:
            return []
        sentence_vectors = self._vectors(pool)
        cosine = dict(zip(pool, (sentence_vectors @ query_vector).tolist()))
        fused = {i: 0.0 for i in pool}
        for signal in (cosine, {i: chunk_score[i] for i in pool if i in chunk_score}, {i: graph[i] for i in pool if i in graph}):
            for rank, i in enumerate(sorted(signal, key=lambda j: -signal[j])):
                fused[i] += 1.0 / (_RRF_K + rank + 1)
        top = sorted(pool, key=lambda i: -fused[i])[:k]
        return [{**self.sentences[i], "score": fused[i], "cosine": cosine[i], "via": "minirag" if i in chunk_score or i in graph else "cosine"}
                for i in top]

    def _minirag_failed(self, signal: str, exc: Exception) -> None:
        """A MiniRAG signal that fails is dropped from the fusion, but counted and reported, never silently."""
        stats = getattr(self.retriever, "stats", None)
        if stats is not None:
            stats[f"{signal}_errors"] = stats.get(f"{signal}_errors", 0) + 1
        message = f"{signal} failed, retrieval continues without it: {type(exc).__name__}: {exc}"
        warn = getattr(self.retriever, "warn", None)
        if warn is not None:
            warn(signal, message)
        else:
            print(f"[EvidenceStore] {message}")

    def _vectors(self, pool: List[int]) -> np.ndarray:
        """Embedding rows of the pooled sentences, kept per store once computed."""
        cache = self.__dict__.setdefault("_vector_cache", {})
        missing = [i for i in pool if i not in cache]
        if missing:
            cache.update(zip(missing, self.embedder.embed([self.sentences[i]["text"] for i in missing])))
        return np.stack([cache[i] for i in pool])

    def _terms_of(self, i: int) -> List[str]:
        """A sentence's BM25 terms, tokenised once per store (every query scores the same sentences)."""
        cache = self.__dict__.setdefault("_term_cache", {})
        if i not in cache:
            cache[i] = lang.terms(self.sentences[i]["text"])
        return cache[i]

    def _admitted(self, i: int, gated: bool) -> bool:
        """Whether sentence `i` may be retrieved (`gated`: reads aloud verbatim; else: has some content), memoised."""
        cache = self.__dict__.setdefault("_admit_cache", {})
        key = (i, gated)
        if key not in cache:
            text = self.sentences[i]["text"]
            cache[key] = sayable(text) if gated else lang.length_units(text) >= 3
        return cache[key]

    def _bm25(self, pool: List[int], query_terms: List[str]) -> Dict[int, float]:
        """Okapi BM25 of each pooled sentence for the query terms (document-wide IDF)."""
        if not pool or not query_terms:
            return {}
        docs = {i: self._terms_of(i) for i in pool}
        avg = sum(len(d) for d in docs.values()) / len(docs) or 1.0
        wanted = set(query_terms)
        scores = {}
        for i, words in docs.items():
            tf: Dict[str, int] = {}
            for w in words:
                if w in wanted:
                    tf[w] = tf.get(w, 0) + 1
            score = sum(self.idf.get(w, 1.0) * f * (_BM25_K1 + 1) / (f + _BM25_K1 * (1 - _BM25_B + _BM25_B * len(words) / avg))
                        for w, f in tf.items())
            if score > 0:
                scores[i] = score
        return scores

    def retrieve_scoped(self, query: str, heading_ids: Iterable[int], keywords: Sequence[str] = (), k: int = 10,
                        exclude: Iterable[str] = (), role: str = "retriever", gated: bool = True) -> List[dict]:
        """Top-k informative sentences of the given headings only, for a slide goal and its keywords.

        Five signals are fused by reciprocal rank: meaning (cosine), exact keyword match (BM25), explicit KG mentions,
        MiniRAG chunk hits, and MiniRAG entity/name-description hits expanded through source chunks + graph neighbours."""
        log_retrieval(role)
        scope, skip = set(heading_ids), set(exclude)
        # `gated`: only sentences that read aloud verbatim. An agent that reads and paraphrases takes every sentence
        # with some content (colon lead-ins, list items, inline maths), so the gate is off for it.
        pool = [i for i, s in enumerate(self.sentences)
                if s["heading"] in scope and s["id"] not in skip and self._admitted(i, gated)]
        if not pool:
            return []
        in_pool = set(pool)
        query_vector = embed_transient(self.embedder, [query])[0]
        sentence_vectors = self._vectors(pool)
        cosine = dict(zip(pool, (sentence_vectors @ query_vector).tolist()))
        bm25 = self._bm25(pool, lang.terms(" ".join([query, *keywords])))
        named: Dict[int, float] = {}
        for name in dict.fromkeys(normalize_name(c) for c in keywords):
            for i in self.mentions.get(name, ()):
                if i in in_pool:
                    named[i] = named.get(i, 0.0) + 1.0
        chunks: Dict[int, float] = {}
        entities: Dict[int, float] = {}
        if self.retriever.available:
            try:
                if hasattr(self.retriever, "hybrid_hits"):
                    chunk_hits, entity_hits = self.retriever.hybrid_hits(
                        query, Config.RETRIEVE_CHUNKS, Config.RETRIEVE_ENTITIES, vector=query_vector,
                        vector_model=getattr(self.embedder, "model", None))
                else:  # compatibility with custom/test retrievers
                    chunk_hits = self.retriever.chunk_hits(query, Config.RETRIEVE_CHUNKS)
                    entity_hits = self.retriever.entity_hits(query, Config.RETRIEVE_ENTITIES)
                for rank, (chunk_id, _) in enumerate(chunk_hits):
                    for i in self.by_chunk.get(chunk_id, ()):
                        if i in in_pool:
                            chunks[i] = 1.0 / (_RRF_K + rank + 1)
            except Exception as exc:
                self._minirag_failed("chunk_hits", exc)
                chunks = {}
                entity_hits = []
            try:
                for rank, (raw_name, _) in enumerate(entity_hits):
                    name = normalize_name(raw_name)
                    strength = 1.0 / (_RRF_K + rank + 1)
                    for i in self.mentions.get(name, ()):
                        if i in in_pool:
                            entities[i] = entities.get(i, 0.0) + strength
                    for chunk_id in self.retriever.entity_chunks(raw_name):
                        for i in self.by_chunk.get(chunk_id, ()):
                            if i in in_pool:
                                entities[i] = entities.get(i, 0.0) + 0.8 * strength
                    for neighbour in self.neighbors.get(name, ()):
                        for i in self.mentions.get(neighbour, ()):
                            if i in in_pool:
                                entities[i] = (entities.get(i, 0.0)
                                               + 0.4 * self.neighbor_weight(name, neighbour) * strength)
            except Exception as exc:
                self._minirag_failed("entity_hits", exc)
                entities = {}
        fused = {i: 0.0 for i in pool}
        for signal in (cosine, bm25, named, chunks, entities):
            for rank, i in enumerate(sorted(signal, key=lambda j: -signal[j])):
                fused[i] += 1.0 / (_RRF_K + rank + 1)
        top = sorted(pool, key=lambda i: -fused[i])[:k]
        return [{**self.sentences[i], "score": fused[i], "cosine": cosine[i], "keyword": bm25.get(i, 0.0),
                 "role": role_of(self.sentences[i]["text"]),
                 "signals": {"cosine": cosine.get(i, 0.0), "bm25": bm25.get(i, 0.0),
                             "mention": named.get(i, 0.0), "chunk": chunks.get(i, 0.0),
                             "entity": entities.get(i, 0.0)}} for i in top]

    def definition_of(self, concept: str, tier: Optional[str] = None, refine: bool = False, role: str = "agent") -> Optional[dict]:
        """Earliest informative sentence that introduces the concept: it mentions it in its first half and is not a
        question or lead-in (no "is a"/"là" patterns, so any language). `refine` first asks MiniRAG for the sentences
        closest to the concept and takes the earliest of those that qualify."""
        name = normalize_name(concept)
        def introduces(row: dict) -> bool:
            if tier and row["tier"] != tier or not _informative(row["text"]):
                return False
            return row["text"].casefold().find(name) in range(0, len(row["text"]) // 2 + 1)
        if refine:
            hits = [h for h in self.retrieve(concept, tier, k=8, concepts=[concept], role=role) if introduces(h)]
            if hits:
                return min(hits, key=lambda h: h["order"])
        return next((self.sentences[i] for i in self.mentions.get(name, ()) if introduces(self.sentences[i])), None)

    # ── scores ───────────────────────────────────────────────────────────────────────────────────────────────────────
    def support(self, bullet: str, source_ids: Sequence[str]) -> float:
        """Best cosine between a written bullet and its source sentences (0 when there is no source)."""
        texts = [self.by_id[i]["text"] for i in source_ids if i in self.by_id]
        if not texts or not bullet.strip():
            return 0.0
        bullet_vector = embed_transient(self.embedder, [bullet])[0]
        return float(np.max(self.embedder.embed(texts) @ bullet_vector))

    def completeness(self, bullet: str, source_ids: Sequence[str], top: int = 6) -> float:
        """Share of the source's salient terms (numbers, then rarest terms in the document) that the bullet keeps."""
        source = " ".join(self.by_id[i]["text"] for i in source_ids if i in self.by_id)
        wanted = lang.salient_terms(source, self.idf, top)
        if not wanted:
            return 1.0
        have = set(lang.terms(bullet))
        return sum(1 for t in wanted if t in have) / len(wanted)

    def prereq_gaps(self, limit: int = 12, role: str = "agent") -> List[dict]:
        """Concepts used inside the scope whose introducing sentence lies in the rest of the document and which the scope
        never introduces itself: candidates for a background note (never a slide fact)."""
        gaps = []
        for name, hits in self.mentions.items():
            in_scope = [i for i in hits if self.sentences[i]["tier"] == SCOPE]
            if not in_scope or self.definition_of(name, SCOPE):
                continue
            outside = self.definition_of(name, DOCUMENT)
            if outside:
                gaps.append({"concept": self._concept_names.get(name, name), "used_in": self.sentences[in_scope[0]]["id"],
                             "defined_in": outside["id"], "chapter": outside["chapter"], "uses": len(in_scope), "name": name})
        gaps = sorted(gaps, key=lambda g: -g["uses"])[:limit]
        for gap in gaps:                                 # MiniRAG picks the introducing sentence of each kept gap
            better = self.definition_of(gap["name"], DOCUMENT, refine=True, role=role)
            if better:
                gap.update(defined_in=better["id"], chapter=better["chapter"])
        return gaps
