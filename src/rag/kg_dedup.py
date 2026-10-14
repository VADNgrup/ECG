"""NDAKG-inspired post-hoc KG cleanup (enrich sections, prune chunk-bleed, dedup) for
Config.KG_CONTENT_MODE. Runs on the finished graph, no LLM/re-parse.

Run standalone:  python -m src.rag.kg_dedup --kb data/cache/kb/<stem> [--threshold 0.8] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
from nano_vectordb import NanoVectorDB
from rapidfuzz import fuzz

from src.rag.kg_schema import is_semantic_node, normalize_asset_records, normalize_graph_schema, validate_graph_schema
from src.rag.structural import HEAD_MARK
from src.utils.config import Config

GRAPHML = "graph_chunk_entity_relation.graphml"
SEP = "<SEP>"
_ALIAS_SEP = " | "
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)

# surface-variant dedup (content-word-set equality; see merge_surface_variants)
_STOPWORDS = {"the", "a", "an", "of", "for", "and", "to", "in", "on", "with", "by", "from",
              "as", "at", "or", "its", "de", "s"}
_ACRONYM_RE = re.compile(r"\s*\(([^)]{1,10})\)\s*$")   # trailing "(ABC)" acronym group


def _singular(w: str) -> str:
    """Naive singularisation so plural/singular names share a token set (trees->tree, classes->class)."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("ses", "xes", "ches", "shes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _content_tokens(name: str) -> list:
    """Ordered normalised tokens (case/acronym/plural/punctuation stripped) so two names merge only
    when their token TUPLE is identical — an extra real word keeps them distinct. Stopwords kept
    (meaningful in names like "Logical AND" vs "Logical OR")."""
    base = name.lstrip(HEAD_MARK)
    m = _ACRONYM_RE.search(base)
    if m:
        core = m.group(1).replace(".", "")
        prefix_words = _TOKEN_RE.findall(base[: m.start()])
        initials = "".join(w[0] for w in prefix_words).casefold()
        core_norm = core.casefold().rstrip("s")          # "(SVMs)" -> svm
        is_acr = core.isascii() and " " not in core and 2 <= len(core) <= 7 and (
            core_norm == initials or any(c.isupper() for c in core)   # "(mse)" via initials; "(ReLU)" via case
        )
        if is_acr:
            base = base[: m.start()].rstrip()            # drop the acronym, keep the expansion
    return [_singular(t) for t in _TOKEN_RE.findall(base.casefold())]


def _canon(name: str) -> str:
    return " ".join(_content_tokens(name))


def _rep_score(graph, node: str) -> tuple:
    """Canonical-form ranking: not ending in a dangling stopword, then acronym > mixed-case > longer > degree."""
    disp = node.lstrip(HEAD_MARK)
    words = _TOKEN_RE.findall(disp.casefold())
    clean_end = 0 if (words and words[-1] in _STOPWORDS) else 1
    has_acr = 1 if re.search(r"\([A-Za-z0-9.]{1,7}\)\s*$", disp) else 0
    mixed = 1 if any(c.isupper() for c in disp) and not disp.isupper() else 0
    return (clean_end, has_acr, mixed, len(disp), graph.degree(node))


def _is_affixed(x: str, y: str) -> bool:
    """True when the longer word is the shorter one plus a short prefix/suffix (un-, non-, -ing, ...): a
    morphological change (negation, tense, degree), not a spelling variant -- must never fuzzy-merge."""
    short, long_ = sorted((x, y), key=len)
    return len(long_) - len(short) <= 4 and (long_.startswith(short) or long_.endswith(short))


def merge_surface_variants(graph, *, fuzzy: bool = True):
    """Cluster nodes that are surface variants of one entity (case/acronym/plural/spacing/spelling).
    Returns [(keep, [drops])] (read-only; apply with the shared _merge_nodes)."""

    nodes = [n for n, d in graph.nodes(data=True) if is_semantic_node(d)]
    if len(nodes) < 2:
        return []
    toks = {n: _content_tokens(n) for n in nodes}
    etype = {n: (graph.nodes[n].get("entity_type") or "").strip().lower() for n in nodes}
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    # Pass A — identical content-word tuple, cross-type.
    by_seq = defaultdict(list)
    for n in nodes:
        t = tuple(toks[n])
        if t:
            by_seq[t].append(n)
    for grp in by_seq.values():
        for x in grp[1:]:
            union(grp[0], x)

    # Pass A2 — concatenated token equals another's tokens (ScikitLearn == Scikit-Learn).
    concat = {}
    for n in nodes:
        if len(toks[n]) >= 2:
            concat.setdefault("".join(toks[n]), n)
    for n in nodes:
        if len(toks[n]) == 1 and toks[n][0] in concat:
            union(concat[toks[n][0]], n)

    # Pass A3 — full concatenation ignoring hyphen/space split (Semi-supervised == Semisupervised).
    by_concat = defaultdict(list)
    for n in nodes:
        key = "".join(toks[n])
        if key:
            by_concat[key].append(n)
    for grp in by_concat.values():
        for x in grp[1:]:
            union(grp[0], x)

    # Pass B — same word count, per-word spelling-close (Regressor≈Regresser), within a type.
    if fuzzy:
        block = defaultdict(list)
        for n in nodes:
            t = toks[n]
            if t:
                block[(len(t), min(t)[:4])].append(n)
        for grp in block.values():
            for i in range(len(grp)):
                for j in range(i + 1, len(grp)):
                    a, b = grp[i], grp[j]
                    if etype[a] != etype[b] or find(a) == find(b):
                        continue
                    ta, tb = toks[a], toks[b]
                    # require length>=6 so short distinct words (mode/model) don't fuzzy-merge.
                    if len(ta) == len(tb) and all(
                        x == y or (min(len(x), len(y)) >= 6 and fuzz.ratio(x, y) >= 88
                                  and not _is_affixed(x, y))
                        for x, y in zip(ta, tb)
                    ):
                        union(a, b)

    clusters = defaultdict(list)
    for n in nodes:
        clusters[find(n)].append(n)
    merges = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        keep = max(members, key=lambda n: _rep_score(graph, n))
        merges.append((keep, [m for m in members if m != keep]))
    return merges


def _resolve_type(graph, members: list) -> str:
    """Schema standardization for a cross-type surface cluster: the most common entity_type among
    members (ties resolved toward a non-generic type over 'concept')."""
    cnt = Counter((graph.nodes[m].get("entity_type") or "").strip().lower()
                  for m in members if graph.has_node(m))
    cnt.pop("", None)
    if not cnt:
        return ""
    top = cnt.most_common()
    best = top[0][1]
    tied = [t for t, c in top if c == best]
    if len(tied) > 1:                       # prefer a specific type over the generic "concept"
        non_generic = [t for t in tied if t != "concept"]
        return sorted(non_generic or tied)[0]
    return tied[0]


def _merge_desc_field(graph, keep: str, members: list) -> None:
    """Combine members' descriptions onto `keep`: dedup sentence-ish parts, keep <=6, join."""
    parts = []
    for m in members:
        d = graph.nodes[m].get("description") or ""
        for p in re.split(r"(?<=[.!?])\s+|" + re.escape(SEP), d):
            p = p.strip()
            if p and p not in parts:
                parts.append(p)
    if parts:
        graph.nodes[keep]["description"] = " ".join(parts[:6])


def _drop_garbage_nodes(graph, *, verbose: bool = True) -> list:
    """Remove malformed extraction artifacts: a leaked '<|...|>' token in the name, or an 'unknown'
    stub whose name is a prose sentence (>=8 words). Typed entities (e.g. long `work` titles) are untouched."""
    drop = [n for n, d in graph.nodes(data=True)
            if "<|" in n or ((d.get("entity_type") or "").strip().lower() == "unknown"
                             and len(n.split()) >= 8)]
    for n in drop:
        graph.remove_node(n)
    if verbose and drop:
        print(f"[kg_dedup] dropped {len(drop)} garbage nodes")
        for n in drop[:8]:
            print(f"    drop garbage: {n[:52]!r}")
    return drop


_CROSSREF_NAME_RE = re.compile(r"^\S+\s+\d+\s*[-–.]\s*\d+$")
_IDENTIFIER_NAME_RE = re.compile(r"^[^\W\d]\w*_\w+$")


def _fix_structural_types(graph, *, verbose: bool = True) -> tuple[list, int]:
    """Drop cross-reference labels the extractor turned into entities.

    A label like "Figure 3-3" names the document's pointer to a figure, not a thing the document is
    about; the figure itself already has a structural node carrying its caption and page. The shape
    (one token + a compound numeric designator) is the same in any language, so no word list."""
    drop = [n for n, d in graph.nodes(data=True)
            if _CROSSREF_NAME_RE.match(n) and (d.get("entity_type") or "").strip().lower() != "heading"]
    for n in drop:
        graph.remove_node(n)
    if verbose and drop:
        print(f"[kg_dedup] dropped {len(drop)} cross-reference labels")
        for n in drop[:6]:
            print(f"    drop crossref: {n[:52]!r}")
    return drop, 0


def _prune_vdb(kb_dir: Path, graph, *, verbose: bool = True) -> dict:
    """Delete vdb entries for nodes/edges no longer in the graph, so MiniRAG's mini/light query
    modes don't hit stale references. Does NOT re-embed."""
    nodes = set(graph.nodes())
    report: dict = {}

    def _prune(fname: str, is_stale) -> int:
        p = Path(kb_dir) / fname
        if not p.exists():
            return 0
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return 0
        stale = [e.get("__id__") for e in (raw.get("data") or []) if is_stale(e) and e.get("__id__")]
        if not stale:
            return 0
        try:
            dim = int(raw.get("embedding_dim") or 384)
            db = NanoVectorDB(dim, storage_file=str(p))
            db.delete(stale)
            db.save()
        except Exception as e:
            if verbose:
                print(f"[kg_dedup] vdb prune skipped for {fname}: {e}")
            return 0
        return len(stale)

    report["entities"] = _prune("vdb_entities.json", lambda e: e.get("entity_name") not in nodes)
    report["entities_name"] = _prune("vdb_entities_name.json", lambda e: e.get("entity_name") not in nodes)
    report["relationships"] = _prune(
        "vdb_relationships.json",
        lambda e: e.get("src_id") not in nodes or e.get("tgt_id") not in nodes,
    )
    if verbose and any(report.values()):
        print(f"[kg_dedup] vdb pruned: entities={report['entities']} "
              f"names={report['entities_name']} relationships={report['relationships']}")
    return report


def _union_source(graph, keep: str, drop: str) -> None:
    """Union source_id chunk lists so enrich_nodes sees the merged node's full provenance."""
    parts = []
    for n in (keep, drop):
        for p in (graph.nodes[n].get("source_id") or "").split(SEP):
            if p and p not in parts:
                parts.append(p)
    if parts:
        graph.nodes[keep]["source_id"] = SEP.join(parts)


# ── loading helpers ────────────────────────────────────────────────────────
def _load_chunks(kb_dir: Path) -> dict:
    """chunk_id -> {'content': str, 'struct': [[offset,page,heading_id], ...]}"""
    p = Path(kb_dir) / "kv_store_text_chunks.json"
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    out = {}
    for cid, c in (raw.items() if isinstance(raw, dict) else []):
        st = c.get("__struct__")
        if isinstance(st, str):
            try:
                st = json.loads(st)
            except Exception:
                st = []
        out[cid] = {"content": c.get("content") or "", "struct": st or []}
    return out


def _heading_maps(structural_index: dict):
    """node_name(HEAD_MARK+display) -> heading_id(str); node_name -> set(pages)."""
    node_to_id, node_to_pages = {}, {}
    for hid, h in (structural_index.get("headings") or {}).items():
        name = HEAD_MARK + str(h.get("display", ""))
        node_to_id[name] = str(hid)
        node_to_pages[name] = {int(p) for p in (h.get("pages") or [])}
    return node_to_id, node_to_pages


def _struct_section_at(struct: list, pos: int):
    """(page, heading_id) of the __struct__ triple whose offset range covers char `pos`."""
    cur = None
    first = None
    for triple in struct:
        if len(triple) < 3:
            continue
        if first is None:
            first = (int(triple[1]), str(triple[2]))
        off = triple[0]
        if off <= pos:
            cur = (int(triple[1]), str(triple[2]))
        else:
            break
    # content before the first offset marker belongs to the first section
    return cur if cur is not None else first


def _norm_quotes(s: str) -> str:
    """Normalise apostrophes/quotes so entity names match the source text (e.g. "O’Reilly" vs
    "O'Reilly"). Preserves character count so __struct__ offsets stay valid."""
    return (s.replace("’", "'").replace("‘", "'")
             .replace("“", '"').replace("”", '"').replace("⁠", " "))


def _find_all(lc: str, ln: str, max_hits: int) -> list:
    positions, start, n = [], 0, 0
    while n < max_hits:
        pos = lc.find(ln, start)
        if pos < 0:
            break
        positions.append((pos, pos + len(ln)))
        start = pos + max(1, len(ln))
        n += 1
    return positions


def _entity_sections_in_chunk(content: str, struct: list, name: str, *, max_hits: int = 20):
    """set of (page, heading_id) at each occurrence of `name` in the chunk content. Two 0-LLM fallbacks for a
    generated entity name that never appears verbatim: (1) markdown escapes the parser leaves in code identifiers
    ("cross\\_val\\_score" for "cross_val_score") -- matched via regex, offsets untouched so __struct__ stays valid;
    (2) a distinguishing suffix a split multi-definition concept was given ("Term (Author's definition)") -- retried
    on the bare term."""
    if not name or not content or not struct:
        return set()
    lc, ln = _norm_quotes(content.lower()), _norm_quotes(name.lower())
    positions = _find_all(lc, ln, max_hits)
    if not positions and re.search(r"[_*]", ln):
        pattern = re.escape(ln).replace(r"\_", r"\\?_").replace(r"\*", r"\\?\*")
        positions = [(m.start(), m.end()) for m in re.finditer(pattern, lc)][:max_hits]
    if not positions:
        base = re.sub(r"\s*\([^()]*\)\s*$", "", ln).strip()
        if base and base != ln:
            positions = _find_all(lc, base, max_hits)
    hits = set()
    for start, _end in positions:
        sec = _struct_section_at(struct, start)
        if sec:
            hits.add(sec)
    return hits


# ── step 1: enrich ─────────────────────────────────────────────────────────
def enrich_nodes(graph, chunks: dict) -> int:
    """Annotate entity nodes with page/section derived from where their name actually appears
    in their source chunk(s). Idempotent (overwrites)."""
    n = 0
    for node, data in graph.nodes(data=True):
        if not is_semantic_node(data):
            continue
        src = data.get("source_id") or ""
        pages, secs = set(), set()
        for cid in (c for c in src.split(SEP) if c):
            ch = chunks.get(cid)
            if not ch:
                continue
            for pg, hid in _entity_sections_in_chunk(ch["content"], ch["struct"], node):
                pages.add(pg)
                secs.add(hid)
        if pages:
            data["page"] = ",".join(str(p) for p in sorted(pages))
            data["section"] = ",".join(sorted(secs))
            n += 1
    return n


# ── step 2: prune chunk-bleed mentions edges ───────────────────────────────
def find_bleed_edges(graph, node_to_id: dict, node_to_pages: dict):
    """Return [(u, v, ent, head)] `mentions` edges whose entity's real section/page (from
    enrich_nodes) doesn't match the heading — a chunk-boundary leak. Unlocated entities are kept."""
    bleed = []
    for u, v, ed in graph.edges(data=True):
        if "mentions" not in (ed.get("keywords") or ""):
            continue
        if u in node_to_id:
            head, ent = u, v
        elif v in node_to_id:
            head, ent = v, u
        else:
            continue
        hid = node_to_id[head]
        hpages = node_to_pages.get(head, set())
        secs = graph.nodes[ent].get("section") or ""
        pgs = graph.nodes[ent].get("page") or ""
        ent_secs = {s for s in secs.split(",") if s}
        ent_pages = {int(p) for p in pgs.split(",") if p.strip().isdigit()}
        if not ent_secs:
            continue                      # unlocated -> conservatively keep
        if hid in ent_secs:
            continue                      # correctly anchored to this heading
        if hpages and (ent_pages & hpages):
            continue                      # entity's real page falls in the heading's range -> keep
        bleed.append((u, v, ent, head))
    return bleed


# ── step 3: rule-based node dedup (CRA) ────────────────────────────────────
def _tokens(graph, node: str) -> set:
    base = node.lstrip(HEAD_MARK).lower()
    al = (graph.nodes[node].get("aliases") or "").lower()
    return set(_TOKEN_RE.findall(base + " " + al))


def _pages_of(graph, node: str) -> set:
    pg = graph.nodes[node].get("page") or ""
    return {p for p in pg.split(",") if p}


def find_duplicate_clusters(graph, threshold: float):
    """Union-Find clusters of same-type entity nodes with Sid>=theta (or Sneigh>=theta as a
    secondary signal). Returns list of (keep, [drops])."""
    buckets = defaultdict(list)
    for node, data in graph.nodes(data=True):
        et = (data.get("entity_type") or "").strip().lower()
        if not et or not is_semantic_node(data):
            continue
        buckets[et].append(node)

    merges = []
    for et, nodes in buckets.items():
        if len(nodes) < 2:
            continue
        tk = {n: _tokens(graph, n) for n in nodes}
        nb = {n: set(graph.neighbors(n)) for n in nodes}
        pg = {n: _pages_of(graph, n) for n in nodes}
        parent = {n: n for n in nodes}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # block by shared token to avoid O(n^2) over the whole bucket
        by_token = defaultdict(list)
        for n in nodes:
            for t in tk[n]:
                by_token[t].append(n)
        checked = set()
        for t, group in by_token.items():
            if len(group) > 80:            # skip ultra-generic tokens (huge blocks)
                continue
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    key = (a, b) if a < b else (b, a)
                    if key in checked:
                        continue
                    checked.add(key)
                    ta, tb = tk[a], tk[b]
                    if not ta or not tb:
                        continue
                    inter = len(ta & tb)
                    sid = max(inter / len(ta), inter / len(tb))
                    ok = sid >= threshold
                    if not ok and nb[a] and nb[b]:
                        ni = len(nb[a] & nb[b])
                        sneigh = max(ni / len(nb[a]), ni / len(nb[b]))
                        ok = sid >= 0.5 and sneigh >= threshold
                    if not ok:
                        continue
                    # RENAMED_TO safeguard: same name but conflicting (disjoint) pages -> keep distinct
                    if sid < 1.0 and pg[a] and pg[b] and not (pg[a] & pg[b]):
                        continue
                    parent[find(a)] = find(b)

        clusters = defaultdict(list)
        for n in nodes:
            clusters[find(n)].append(n)
        for members in clusters.values():
            if len(members) < 2:
                continue
            keep = max(members, key=lambda n: graph.degree(n))
            merges.append((keep, [m for m in members if m != keep]))
    return merges


def _merge_nodes(graph, keep: str, drop: str) -> None:
    for nbr in list(graph.neighbors(drop)):
        if nbr == keep:
            continue
        ed = graph.get_edge_data(drop, nbr) or {}
        if graph.has_edge(keep, nbr):
            # keep the stronger weight; preserve mentions keyword if either had it
            cur = graph.get_edge_data(keep, nbr) or {}
            try:
                if float(ed.get("weight", 0)) > float(cur.get("weight", 0)):
                    cur["weight"] = ed.get("weight", cur.get("weight"))
            except Exception:
                pass
        else:
            graph.add_edge(keep, nbr, **ed)
    aliases = [graph.nodes[keep].get("aliases") or "", graph.nodes[drop].get("aliases") or "",
               drop.lstrip(HEAD_MARK)]
    merged = _ALIAS_SEP.join(dict.fromkeys(a for a in aliases if a))
    graph.nodes[keep]["aliases"] = merged
    graph.remove_node(drop)


# ── orchestration ──────────────────────────────────────────────────────────
def clean_kb(kb_dir: Path, *, threshold: float = 0.8, dedup: bool = False, surface: bool = True,
             dry_run: bool = False, verbose: bool = True) -> dict:
    """Merge surface variants (`surface`, on by default) + enrich sections + prune chunk-bleed
    edges (always). Loose overlap-based `dedup` stays OFF by default — over-merges distinct
    technical concepts."""
    kb_dir = Path(kb_dir)
    gpath = kb_dir / GRAPHML
    sipath = kb_dir / "structural_index.json"
    if not gpath.exists():
        raise FileNotFoundError(f"no graphml at {gpath}")
    if not sipath.exists():
        raise FileNotFoundError(f"no structural_index.json at {sipath} (needed for section validation)")

    graph = nx.read_graphml(gpath)
    schema_report = normalize_graph_schema(graph)
    # Legacy image rows have no explicit informative decision and are therefore removed under the
    # precision-first policy.  Canonicalise equation/code asset names at the same time.
    asset_path = kb_dir / "asset_headings.json"
    normalized_assets = None
    asset_report = {}
    if asset_path.exists():
        try:
            raw_assets = json.loads(asset_path.read_text(encoding="utf-8"))
            normalized_assets, asset_report = normalize_asset_records(raw_assets, strict_images=True)
        except Exception:
            normalized_assets = None
    chunks = _load_chunks(kb_dir)
    structural_index = json.loads(sipath.read_text(encoding="utf-8"))
    node_to_id, node_to_pages = _heading_maps(structural_index)

    _drop_garbage_nodes(graph, verbose=verbose)
    _crossrefs, _id_retyped = _fix_structural_types(graph, verbose=verbose)

    n0, e0 = graph.number_of_nodes(), graph.number_of_edges()

    # surface-variant merge first, so enrich/bleed reflect the deduped nodes
    surf = merge_surface_variants(graph) if surface else []
    n_surf = sum(len(drops) for _keep, drops in surf)
    if not dry_run:
        for keep, drops in surf:
            members = [keep] + [d for d in drops if graph.has_node(d)]
            _merge_desc_field(graph, keep, members)
            rt = _resolve_type(graph, members)          # schema-std: majority type for the cluster
            if rt:
                graph.nodes[keep]["entity_type"] = rt
            for d in drops:
                if graph.has_node(d) and graph.has_node(keep) and d != keep:
                    _union_source(graph, keep, d)
                    _merge_nodes(graph, keep, d)

    enriched = enrich_nodes(graph, chunks)
    bleed = find_bleed_edges(graph, node_to_id, node_to_pages)
    clusters = find_duplicate_clusters(graph, threshold) if dedup else []
    n_merges = sum(len(drops) for _keep, drops in clusters)

    if not dry_run:
        for u, v, _ent, _head in bleed:
            if graph.has_edge(u, v):
                graph.remove_edge(u, v)
        for keep, drops in clusters:
            for d in drops:
                if graph.has_node(d) and graph.has_node(keep):
                    _merge_nodes(graph, keep, d)
        validate_graph_schema(graph)
        nx.write_graphml(graph, gpath)
        if normalized_assets is not None:
            asset_path.write_text(json.dumps(normalized_assets, ensure_ascii=False), encoding="utf-8")
        _prune_vdb(kb_dir, graph, verbose=verbose)   # keep the nano-vectordb in sync with the graph

    nodes_after = n0 - n_surf - n_merges
    report = {"nodes_before": n0, "edges_before": e0,
              "nodes_after": nodes_after, "edges_after": graph.number_of_edges(),
              "surface_merged": n_surf, "enriched": enriched, "bleed_edges_pruned": len(bleed),
              "nodes_merged": n_merges, "schema": schema_report, "assets": asset_report,
              "dry_run": dry_run}
    if verbose:
        print(f"[kg_dedup] {kb_dir.name}: surface_merged={n_surf} enriched={enriched} "
              f"bleed_edges_pruned={len(bleed)} nodes_merged={n_merges} "
              f"(nodes {n0}->{nodes_after}, edges {e0}->{report['edges_after']})"
              f"{'  [DRY-RUN]' if dry_run else ''}")
        for keep, drops in surf[:12]:
            print(f"    surface: {keep.lstrip(HEAD_MARK)[:30]!r} <- {[d.lstrip(HEAD_MARK)[:24] for d in drops][:4]}")
        for u, v, ent, head in bleed[:8]:
            print(f"    prune mentions: {head.lstrip(HEAD_MARK)[:32]!r} -x-> {ent[:32]!r}")
        for keep, drops in clusters[:8]:
            print(f"    merge: {keep.lstrip(HEAD_MARK)[:30]!r} <- {[d.lstrip(HEAD_MARK)[:24] for d in drops][:4]}")
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="NDAKG-style KG cleanup: enrich + prune chunk-bleed "
                                             "mentions + rule-based node dedup")
    ap.add_argument("--kb", required=True, help="path to data/cache/kb/<stem>")
    ap.add_argument("--threshold", type=float, default=None, help="dedup similarity theta (default: Config.KG_DEDUP_THRESHOLD)")
    ap.add_argument("--dedup", action="store_true", help="also run loose node dedup (OFF by default — over-merges technical concepts)")
    ap.add_argument("--no-surface", action="store_true", help="skip surface-variant merge (case/acronym/plural/spelling); ON by default")
    ap.add_argument("--dry-run", action="store_true", help="report only; do not modify the graphml")
    args = ap.parse_args()
    theta = args.threshold
    if theta is None:
        try:
            theta = float(getattr(Config, "KG_DEDUP_THRESHOLD", 0.8))
        except Exception:
            theta = 0.8
    clean_kb(Path(args.kb), threshold=theta, dedup=args.dedup, surface=not args.no_surface,
             dry_run=args.dry_run)


if __name__ == "__main__":
    main()
