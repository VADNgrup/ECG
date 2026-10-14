import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import fitz
import networkx as nx
import tiktoken
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from minirag import MiniRAG
from minirag.llm.openai import openai_complete_if_cache, openai_embed
from minirag.utils import EmbeddingFunc
from openai import OpenAI
from sentence_transformers import SentenceTransformer
from tqdm import tqdm

from src.rag import evidence_store, llm_mode, outline, view_kg
from src.rag.asset_validation import validate_asset_records
from src.rag.chunking import marker_chunking_func_mini
from src.rag.code_text import (_CODE_END_RE, _CODE_OPEN_RE, _CODE_TOKEN_RE, _has_code_line,
                               _word_ratio)
from src.rag.code_images import render_code_blocks
from src.rag.figure_captions import analyze
from src.rag.kg_dedup import clean_kb
from src.rag.kg_salience import enrich_kb
from src.rag.kg_schema import (normalize_graph_schema, SEMANTIC_ENTITY_TYPES, validate_graph_schema,
                               write_schema_metadata)
from src.rag.retrieve_content import load_asset_headings, load_text_chunks
from src.rag.structural import (_clean_chars as _struct_norm, _families_nesting, _family_key, _numeric_families,
                                add_structure_to_graph, build_heading_tree as _rag_build_heading_tree,
                                build_heading_tree_from_toc as _rag_build_heading_tree_from_toc,
                                build_structural_index as _rag_build_structural_index,
                                build_structural_index_from_toc as _rag_build_structural_index_from_toc,
                                STRUCT_MARK as _STRUCT_MARK, STRUCT_MARK_RE)
from src.utils.config import Config
from src.utils.content_cache import cache_path, cached_json
from src.utils.llm import _write_log

_pydir = Path(sys.executable).parent
os.environ["PATH"] = os.pathsep.join(
    [str(_pydir), str(_pydir / "Scripts"), str(_pydir / "bin"), os.environ.get("PATH", "")]
)

if os.name != "nt":
    _env_lib = str((_pydir.parent / "lib").resolve())
    if os.path.isdir(_env_lib):
        _ld = os.environ.get("LD_LIBRARY_PATH", "")
        if _env_lib not in _ld.split(os.pathsep):
            os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(filter(None, [_env_lib, _ld]))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

llm_mode.apply()                     # `--llm local` points the KG_LLM_*/KG_VLM_* below at a local server
DATA_DIR = BASE_DIR / "data"
CONTEXT_DIR = DATA_DIR / "context"
RAW_DIR = DATA_DIR / "raw"
KB_DIR = DATA_DIR / "cache" / "kb"

OPENAI_API_KEY = os.getenv("KG_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
LLM_BASE_URL = os.getenv("KG_LLM_BASE_URL") or os.getenv("LLM_BASE_URL") or "https://api.openai.com/v1"
KG_LLM_MODEL = os.getenv("KG_LLM_MODEL", "gpt-4o-mini")
VLM_API_KEY = os.getenv("KG_VLM_API_KEY") or os.getenv("VLM_API_KEY") or OPENAI_API_KEY
VLM_BASE_URL = os.getenv("KG_VLM_BASE_URL") or os.getenv("VLM_BASE_URL") or LLM_BASE_URL
VLM_MODEL = os.getenv("KG_VLM_MODEL") or os.getenv("VLM_MODEL_NAME") or KG_LLM_MODEL
KG_EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")   # the pipeline's embedding model
KG_EMBED_DIM = 1536
KG_LOCAL_EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"      # the offline (hash-backend) test build
KG_CHUNK_SIZE = 1200                 # tokens per chunk: bigger chunks cut tokens but extract fewer entities
KG_MM_ASYNC = 8                      # concurrent table-summary calls
KG_MAX_OUTPUT = 4000                 # output cap of a KG call: an extraction answer is ~1k; a model that loops stops here

_PAGE_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->", re.IGNORECASE)
_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


KG_ENTITY_TYPES = list(SEMANTIC_ENTITY_TYPES)


class _Usage:
    def __init__(self):
        self.prompt = self.completion = self.total = self.calls = self.cached = 0

    def add_usage(self, tc):
        p = tc.get("prompt_tokens", 0) or 0
        c = tc.get("completion_tokens", 0) or 0
        self.prompt += p
        self.completion += c
        self.total += tc.get("total_tokens", p + c) or (p + c)
        self.cached += tc.get("cached_tokens", 0) or 0
        self.calls += 1


LLM_USAGE = _Usage()
EMBED_USAGE = {"tokens": 0, "calls": 0, "backend": "?"}


async def _with_extract_heartbeat(coro, label: str = "KG extraction"):
    """Prints a newline-terminated progress line every ~15s while `coro` runs, so a slow/redirected
    build doesn't look like a silent freeze (MiniRAG's own progress uses `\\r`, which may not show at
    all when stdout is redirected to a log file)."""
    start = time.perf_counter()
    task = asyncio.ensure_future(coro)
    while True:
        done, _ = await asyncio.wait({task}, timeout=15)
        if done:
            break
        el = int(time.perf_counter() - start)
        print(f"[extract] {label} still running... {el // 60}m{el % 60:02d}s elapsed", flush=True)
    return await task


def _make_llm_func():

    async def llm_model_func(prompt, system_prompt=None, history_messages=None, **kwargs):
        call_usage = _Usage()
        kwargs.setdefault("token_tracker", call_usage)
        cap = "max_completion_tokens" if KG_LLM_MODEL.startswith(("gpt-5", "o1", "o3", "o4")) else "max_tokens"
        kwargs.setdefault(cap, KG_MAX_OUTPUT)
        t0 = time.perf_counter()
        result = await openai_complete_if_cache(
            KG_LLM_MODEL,
            prompt,
            system_prompt=system_prompt,
            history_messages=history_messages or [],
            api_key=OPENAI_API_KEY,
            base_url=LLM_BASE_URL,
            **kwargs,
        )
        if call_usage.calls:
            usage = {"prompt_tokens": call_usage.prompt, "completion_tokens": call_usage.completion,
                    "total_tokens": call_usage.total, "prompt_tokens_details": {"cached_tokens": call_usage.cached}}
            LLM_USAGE.add_usage(usage)
            _write_log(KG_LLM_MODEL, usage, time.perf_counter() - t0, "rag_llm",
                      len(str(prompt)) + len(str(system_prompt or "")), len(result or ""))
        return result

    return llm_model_func


def _count_tokens(texts) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return sum(len(enc.encode(t)) for t in texts)
    except Exception:
        return sum(len(str(t)) // 4 for t in texts)


def _make_embedding_func(backend: str):
    EMBED_USAGE["backend"] = backend

    if backend == "local":

        model = SentenceTransformer(KG_LOCAL_EMBED_MODEL)
        dim = model.get_sentence_embedding_dimension()

        async def embed(texts):
            texts = list(texts)
            EMBED_USAGE["tokens"] += _count_tokens(texts)
            EMBED_USAGE["calls"] += 1
            return await asyncio.to_thread(
                lambda: model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            )

        print(f"[embed] local sentence-transformers '{KG_LOCAL_EMBED_MODEL}' (dim={dim}, FREE)")
        return EmbeddingFunc(embedding_dim=dim, max_token_size=512, func=embed)


    async def embed(texts):
        texts = list(texts)
        EMBED_USAGE["tokens"] += _count_tokens(texts)
        EMBED_USAGE["calls"] += 1
        return await openai_embed(          # the pipeline's embedding endpoint, also with `--llm local`
            texts, model=KG_EMBED_MODEL,
            api_key=Config.EMBED_API_KEY, base_url=Config.EMBED_BASE_URL,
        )

    print(f"[embed] openai '{KG_EMBED_MODEL}' (dim={KG_EMBED_DIM})")
    return EmbeddingFunc(embedding_dim=KG_EMBED_DIM, max_token_size=8192, func=embed)


def _resolve_document(doc: str) -> tuple[str, Path | None]:
    stem = Path(doc).stem
    pdf = RAW_DIR / f"{stem}.pdf"
    return stem, (pdf if pdf.exists() else None)


def _context_to_content_list(document_id: str, include_images: bool) -> list[dict]:
    ctx_path = CONTEXT_DIR / f"{document_id}.json"
    if not ctx_path.exists():
        raise FileNotFoundError(
            f"No parsed context at {ctx_path}. Run the gpt-4o-mini extraction (Phase 1) first."
        )
    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    markdown = ctx.get("text_content", {}).get("markdown", "")

    caption_by_file: dict[str, str] = {}
    for alt, path in _IMG_MD_RE.findall(markdown):
        caption_by_file[Path(path).name] = alt.strip()

    content_list: list[dict] = []
    pages = _split_pages(markdown)
    for page_num, page_md in pages:
        text = _IMG_MD_RE.sub(" ", page_md).strip()
        if text:
            content_list.append({"type": "text", "text": text, "page_idx": page_num - 1})

    for tbl in ctx.get("tables", []) or []:
        body = tbl.get("markdown") or ""
        if body.strip():
            content_list.append({
                "type": "table",
                "table_body": body,
                "table_caption": [tbl.get("table_caption", "")],
                "page_idx": 0,
            })

    if include_images:
        for img in ctx.get("assets", {}).get("images", []) or []:
            rel = img.get("file_path", "")
            abs_path = (DATA_DIR / rel)
            if not abs_path.exists():
                continue
            fname = Path(rel).name
            content_list.append({
                "type": "image",
                "img_path": str(abs_path),
                "image_caption": [caption_by_file.get(fname, img.get("caption", ""))],
                "page_idx": 0,
            })

    return content_list


def _mineru_content_list_to_text(path: Path) -> list[dict]:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    out: list[dict] = []
    for it in items:
        t = it.get("type")
        page = it.get("page_idx", 0)
        page = int(page) if str(page).isdigit() else 0
        if t == "text":
            txt = (it.get("text") or "").strip()
        elif t == "code":
            body = (it.get("code_body") or "").strip()
            txt = f"```\n{body}\n```" if body else ""
        elif t == "table":
            cap = " ".join(it.get("table_caption") or [])
            body = (it.get("table_body") or "").strip()
            txt = (cap + "\n" + body).strip()
        elif t == "equation":
            txt = (it.get("text") or "").strip()
        else:
            continue
        if txt:
            out.append({"type": "text", "text": txt, "page_idx": page})
    return out


def _measure_indent_levels(toc_text: str) -> dict[str, int] | None:
    """Rank each TOC line's title by its leading-whitespace depth, keyed by normalized title. Returns
    None when indentation isn't a trustworthy signal (fewer than 2 distinct indent amounts each backed
    by several lines) -- many TOCs (e.g. OCR'd flush-left) carry no indentation at all, so callers must
    treat this as optional, not assume it's always available."""
    lines = [ln for ln in toc_text.splitlines() if ln.strip()]
    raw: list[tuple[int, str]] = []
    for ln in lines:
        stripped = ln.lstrip()
        indent = len(ln) - len(stripped)
        raw.append((indent, stripped))
    counts = Counter(indent for indent, _ in raw)
    distinct = sorted(ind for ind, c in counts.items() if c >= 3)
    if len(distinct) < 2:
        return None
    rank = {ind: i for i, ind in enumerate(distinct)}
    out: dict[str, int] = {}
    for indent, text in raw:
        if indent not in rank:
            continue
        t = re.sub(r"\.{2,}.*$", "", text).strip()
        t = re.sub(r"\s+\d{1,4}\s*$", "", t).strip("-•*·.# _\t")
        nt = _struct_norm(t)
        if nt:
            out[nt] = rank[indent]
    return out


def _robust_toc_levels(entries: list[tuple[str, int, str]],
                       indent_by_title: dict[str, int] | None = None) -> dict[str, int]:
    """Assign each TOC entry a final level using, in order of trust: (1) numbered-family membership,
    with order-containment nesting between families when >=1 are detected (generic -- only member
    counts + position spans, never titles/keywords); (2) measured indentation rank, when reliable;
    (3) the LLM's own per-line level guess as the last resort. Rationale + real-document evidence for
    this design: src/rag/PROGRESS.md."""
    if not entries:
        return {}

    # TOC entries are a clean heading list, so a 2-member series (e.g. "Part I"/"Part II") is enough.
    fams = _numeric_families([{"title": t, "level": lv} for (_, lv, t) in entries], min_members=2)

    members_by_family: dict[tuple[str, ...], list[int]] = {}
    for i, (_, _, t) in enumerate(entries):
        lead, has_num = _family_key(t)
        if has_num and lead in fams:
            members_by_family.setdefault(lead, []).append(i)

    # Families _families_nesting can't relate to any other are left unassigned here and fall through
    # to each entry's own raw LLM-guessed level below.
    family_level_of: dict[tuple[str, ...], int] = {}
    if len(members_by_family) >= 2:
        nesting_depth = _families_nesting(members_by_family)
        for lead in members_by_family:
            if lead in nesting_depth:
                family_level_of[lead] = 1 + nesting_depth[lead]
    elif len(members_by_family) == 1:
        family_level_of[next(iter(members_by_family))] = 1

    levels: dict[str, int] = {}
    for lead, idxs in members_by_family.items():
        if lead not in family_level_of:
            continue
        for i in idxs:
            levels[entries[i][0]] = family_level_of[lead]

    first_family_pos = min((i for lead, idxs in members_by_family.items() if lead in family_level_of
                            for i in idxs), default=len(entries))
    for i, (nt, lvl, _) in enumerate(entries):
        if nt in levels:
            continue
        if family_level_of and i < first_family_pos:
            levels[nt] = 1
        elif indent_by_title is not None and nt in indent_by_title:
            levels[nt] = max(2, indent_by_title[nt] + 1)
        elif family_level_of:
            levels[nt] = max(2, lvl)
        else:
            levels[nt] = lvl
    return levels


# Folded into the cache key (which stores the processed `levels`); bump when the prompt or the
# _robust_toc_levels/_measure_indent_levels logic changes so stale cached results are invalidated.
_TOC_PROMPT_VERSION = 5


def _toc_levels(content_list_path: str) -> dict[str, int]:
    toc = outline._extract_printed_toc(content_list_path)
    # The OCR-cloud sidecar (src.rag.ocr._split_toc) is usually more complete than this content-list
    # heuristic; prefer whichever TOC text is longer.
    sidecar = Path(f"{content_list_path}.printed_toc.txt")
    if sidecar.exists():
        sidecar_toc = sidecar.read_text(encoding="utf-8")
        if len(sidecar_toc) > len(toc):
            toc = sidecar_toc
    if not toc or len(toc) < 120:
        return {}
    cache_path = f"{content_list_path}.toclevels.json"
    key = hashlib.md5(f"{_TOC_PROMPT_VERSION}\n{toc}".encode("utf-8")).hexdigest()
    if Path(cache_path).exists():
        try:
            c = json.loads(Path(cache_path).read_text(encoding="utf-8"))
            if c.get("key") == key:
                return {k: int(v) for k, v in c.get("levels", {}).items()}
        except Exception:
            pass
    try:
        client = OpenAI(api_key=OPENAI_API_KEY, base_url=LLM_BASE_URL)
        prompt = (
            "You are given a document's printed TABLE OF CONTENTS in Markdown (one heading per line, each "
            "ending with its page number). Reconstruct the heading HIERARCHY and output ONE line per "
            "entry as '<level>\\t<title>' (level = an integer depth, 1 = shallowest).\n\n"
            "Infer each entry's level from objective TYPOGRAPHIC/STRUCTURAL cues in the line itself — "
            "NEVER from the meaning of the words (works for any language):\n"
            "1. Markdown emphasis marks the TOP divisions: ONLY a line in **bold** or prefixed with '#' "
            "may be level 1. A line with NO emphasis (no '*', no leading '#') is NEVER level 1 — it is a "
            "subsection nested under the most recent emphasized division above it, so give it that "
            "division's level + 1 (deeper still if further indented or with a deeper numeric prefix).\n"
            "2. Numeric prefixes give depth by their dotted scheme: '1'/'I'/'A' = one level; '1.2'/'1.2.3' "
            "or an indented plain title = one level deeper per extra component.\n"
            "3. Keep levels CONSISTENT: entries with the same emphasis/indentation get the SAME level. "
            "IMPORTANT: OCR of the printed TOC often applies emphasis (bold) INCONSISTENTLY across "
            "otherwise-equal entries -- e.g. chapter 1 not bold, chapter 3 not bold, chapter 9 bold. "
            "When several entries share the same top-level numbering sequence (1, 2, 3, ... or a "
            "consistent scheme), treat them as the SAME level even if some of them lack the emphasis "
            "markup the others have -- numbering-sequence consistency overrides an inconsistent bold cue.\n"
            "4. If an entry starts with a numbering/lettering marker (e.g. '1.', 'Chapter 3', 'A.', "
            "'IV.'), KEEP that marker as part of the output title text (needed to detect the numbering "
            "sequence downstream) -- do not strip it.\n\n"
            "Rules: output EVERY entry, in order — never drop, merge, translate, reorder or invent lines "
            "(division/part/chapter headers are entries too). Clean each title: remove the trailing page "
            "number and dot leaders, and strip Markdown emphasis markers ('*', '#', '_') but KEEP any "
            "numbering marker per rule 4, and the full remaining title text with its original language "
            "and diacritics unchanged. Output only the lines, nothing else."
            f"\n\n{toc}"
        )
        resp = client.chat.completions.create(model=KG_LLM_MODEL, temperature=0, max_tokens=8000,
                                              messages=[{"role": "user", "content": prompt}])
        entries: list[tuple[str, int, str]] = []
        for ln in (resp.choices[0].message.content or "").splitlines():
            m = re.match(r"\s*(\d+)\s*[\t:.\-]\s*(.+)", ln)
            if not m:
                continue
            lvl = int(m.group(1))
            t = re.sub(r"\s*\.{2,}.*$", "", m.group(2).strip()).strip("-•*·.# \t")
            nt = _struct_norm(t)
            if nt and lvl >= 1:
                entries.append((nt, lvl, t))
        levels = _robust_toc_levels(entries, indent_by_title=_measure_indent_levels(toc))
        Path(cache_path).write_text(json.dumps({"key": key, "levels": levels}), encoding="utf-8")
        print(f"[toc_levels] {len(levels)} headings with TOC levels")
        return levels
    except Exception as e:
        print(f"[toc_levels] none ({e})")
        return {}


def _heading_tree(content_list_path: str, toc_levels: dict[str, int] | None = None,
                  toc_entries: list[tuple[int, str, int]] | None = None,
                  page_offset: int = 0) -> tuple[list[dict], list[dict]]:
    items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    if toc_entries:
        return _rag_build_heading_tree_from_toc(items, toc_entries, page_offset)
    return _rag_build_heading_tree(items, toc_levels)


def _structure_index(content_list_path: str, toc_levels: dict[str, int] | None = None,
                     toc_entries: list[tuple[int, str, int]] | None = None,
                     page_offset: int = 0) -> list[dict]:
    return _heading_tree(content_list_path, toc_levels, toc_entries, page_offset)[1]


def _document_title(document_id: str, content_list_path: str) -> str:
    try:
        items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
        cover: list[str] = []
        first_l1: str | None = None
        for it in items:
            if it.get("type") == "text" and str(it.get("text_level")) == "1":
                t = re.sub(r"\s+", " ", it.get("text") or "").strip()
                if len(t) < 3:
                    continue
                if first_l1 is None:
                    first_l1 = t
                pg = it.get("page_idx", 0)
                if (int(pg) if str(pg).isdigit() else 99) <= 2:
                    cover.append(t)
        if cover:
            return max(cover, key=len)[:120]
        if first_l1:
            return first_l1[:120]
    except Exception:
        pass
    return document_id.replace("_", " ").strip()[:120]


def _pdf_metadata_title(pdf_path: Path | None) -> str | None:
    """PDF's own metadata /Title -- unlike `_document_title`'s cover-page content scan, this can't
    collide with a real heading that happens to sit on page 1-2."""
    if not pdf_path:
        return None
    try:
        t = (fitz.open(str(pdf_path)).metadata or {}).get("title", "")
        t = re.sub(r"\s+", " ", t or "").strip()
        return t[:120] or None
    except Exception:
        return None


def _embedded_outline_entries(pdf_path: Path | None) -> list[tuple[int, str, int]] | None:
    """Ordered embedded PDF outline (fitz.get_toc): [(level, title, page1based), ...]. Physical
    page numbers -> usable as the authoritative heading backbone. None if <3 entries / no outline."""
    if not pdf_path:
        return None
    try:
        toc = fitz.open(str(pdf_path)).get_toc()
    except Exception:
        return None
    if len(toc) < 3:
        return None
    entries = [(int(level), title, int(page)) for level, title, page in toc if (title or "").strip()]
    return entries or None


def _embedded_outline_levels(pdf_path: Path | None) -> dict[str, int] | None:
    entries = _embedded_outline_entries(pdf_path)
    if not entries:
        return None
    levels = {_struct_norm(title): int(level) for level, title, _ in entries if _struct_norm(title)}
    return levels or None


def _build_structural_index(content_list_path: str, document_id: str,
                            toc_levels: dict[str, int] | None = None,
                            pdf_path: Path | None = None,
                            toc_entries: list[tuple[int, str, int]] | None = None,
                            page_offset: int = 0) -> dict:
    items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    if toc_entries:
        # Title must not collide with any real heading, since a trimmed doc can start at a chapter heading.
        title = _pdf_metadata_title(pdf_path) or document_id.replace("_", " ").strip()[:120]
        idx = _rag_build_structural_index_from_toc(items, title, toc_entries, page_offset=page_offset)
        print(f"[structural] title='{title}' | TOC-backbone: {len(toc_entries)} outline entries -> "
              f"{len(idx.get('headings', {}))} heading nodes (incl. level-0 title)")
        return idx
    title = _document_title(document_id, content_list_path)
    if toc_levels is None:
        toc_levels = _embedded_outline_levels(pdf_path) or _toc_levels(content_list_path)
    idx = _rag_build_structural_index(items, title, toc_levels=toc_levels)
    print(f"[structural] title='{title}' | {len(idx.get('headings', {}))} heading nodes "
          f"(incl. level-0 title)")
    return idx


def _persist_image(src: Path | None, working_dir: Path | None) -> str | None:
    """Copy an extracted image into the KB's own images/ dir and return a path RELATIVE to the
    project root, so the KB is self-contained + portable and data/cache/parse can be deleted.
    working_dir=None keeps the legacy absolute behavior (callers that don't relocate). Returns
    None if the source doesn't exist."""
    if not src or not src.exists():
        return None
    if working_dir is None:
        return str(src)
    img_dir = Path(working_dir) / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    dst = img_dir / src.name
    if not dst.exists():
        try:
            shutil.copy2(src, dst)
        except Exception:
            return str(src)
    try:
        return str(dst.relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        return str(dst).replace("\\", "/")


def _mineru_content_list_to_multimodal(path: Path, pages: tuple[int, int] | None = None,
                                       structural: bool = False,
                                       toc_levels: dict[str, int] | None = None,
                                       toc_entries: list[tuple[int, str, int]] | None = None,
                                       page_offset: int = 0,
                                       working_dir: Path | None = None) -> list[dict]:
    base = Path(path).parent
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    per = _structure_index(str(path), toc_levels, toc_entries, page_offset) if structural else None
    figures = analyze(items)

    def _mark(i: int) -> str:
        if not per:
            return ""
        s = per[i]
        return _STRUCT_MARK.format(page=s["page"], node=s["node"])

    out: list[dict] = []
    for i, it in enumerate(items):
        t = it.get("type")
        page = it.get("page_idx", 0)
        page = int(page) if str(page).isdigit() else 0
        if pages and not (pages[0] <= page <= pages[1]):
            continue
        if t == "text":
            txt = (it.get("text") or "").strip()
            if txt:
                out.append({"type": "text", "text": _mark(i) + txt, "page_idx": page})
        elif t == "code":
            before, code, after = _clean_code_body((it.get("code_body") or "").strip())
            for txt in (before, f"```\n{code}\n```" if code else "", after):
                if txt:
                    out.append({"type": "text", "text": _mark(i) + txt, "page_idx": page})
        elif t in ("image", "chart"):
            rel = it.get("img_path") or ""
            if figures.get(Path(rel).name, {}).get("decorative"):
                continue
            abs_path = base / rel if rel else None
            img_path = _persist_image(abs_path, working_dir)
            if not img_path:
                continue
            cap = it.get("image_caption") or it.get("chart_caption") or []
            out.append({
                "type": "image",
                "img_path": img_path,
                "image_caption": cap if isinstance(cap, list) else [cap],
                "page_idx": page,
            })
        elif t == "table":
            body = (it.get("table_body") or "").strip()
            if not body:
                continue
            cap = it.get("table_caption") or []
            out.append({
                "type": "table",
                "table_body": body,
                "table_caption": cap if isinstance(cap, list) else [cap],
                "page_idx": page,
            })
        elif t == "equation":
            latex = (it.get("text") or it.get("latex") or "").strip()
            if latex:
                out.append({"type": "equation", "latex": latex, "text": latex, "page_idx": page})
    return out


def _build_asset_headings(content_list_path: str, toc_levels: dict[str, int] | None = None,
                          toc_entries: list[tuple[int, str, int]] | None = None,
                          page_offset: int = 0, working_dir: Path | None = None,
                          asset_captions: dict[int, str] | None = None,
                          code_renders: dict[int, dict] | None = None) -> list[dict]:
    """Page-anchored asset->heading map, from the SAME per_item node map used for text markers.
    Each image/table/equation block inherits the heading whose span contains it (correct heading
    by page), fixing the concept-mediated misattribution. Written to asset_headings.json and read
    by retrieve_content.select_content_for_heading for a slide's section_assets. img_path is stored
    relative to the project root (image copied into working_dir/images/ via _persist_image).

    A figure is an asset when it carries its own caption (the document's "Figure N-M. ..."); its caption is that one.
    `asset_captions[item_index]` supplies the LLM table summary."""
    base = Path(content_list_path).parent
    items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    per = _structure_index(str(content_list_path), toc_levels, toc_entries, page_offset)
    caps = asset_captions or {}
    figures = analyze(items)
    out: list[dict] = []
    for i, it in enumerate(items):
        t = it.get("type")
        if t not in ("image", "chart", "table", "equation", "code"):
            continue
        node = per[i]["node"] if i < len(per) else -1
        page = per[i]["page"] if i < len(per) else 0
        if t in ("image", "chart"):
            rel = it.get("img_path") or ""
            info = figures.get(Path(rel).name, {})
            real = info.get("real_caption", "")
            if info.get("decorative") or not real:
                continue        # page furniture, or a picture the document gives no caption: never a slide asset
            abs_path = base / rel if rel else None
            img_path = _persist_image(abs_path, working_dir)
            if not img_path:
                continue
            out.append({"node": node, "page": page, "type": "image", "img_path": img_path,
                        "caption": real, "real_caption": real, "classification": "informative",
                        "classification_reason": "caption",
                        "area_frac": info.get("area_frac"), "bbox": info.get("bbox"), "kind": info.get("kind", "image")})
        elif t == "table":
            body = (it.get("table_body") or "").strip()
            if not body:
                continue
            doc_cap = it.get("table_caption") or []
            doc_cap = " ".join(doc_cap) if isinstance(doc_cap, list) else str(doc_cap)
            out.append({"node": node, "page": page, "type": "table",
                        "table_body": (_html_table_to_md(body) or body)[:2000],  # markdown for readability
                        "caption": caps.get(i) or doc_cap})  # LLM table summary preferred
        elif t == "equation":
            latex = (it.get("text") or it.get("latex") or "").strip()
            if latex:
                out.append({"node": node, "page": page, "type": "formula", "latex": latex})
        elif t == "code":
            render = (code_renders or {}).get(i) or {}
            # The PDF crop is the faithful listing (colour + indentation); the OCR body is the fallback.
            code = render.get("code") or _clean_code_body((it.get("code_body") or "").strip())[1]
            # A parser `code` block is sometimes a quotation or a formula. Without a PDF crop to
            # prove it was typeset as a listing, require at least one line that looks like code.
            if code and not render.get("img_path") and not _has_code_line(code):
                code = ""
            if code:
                doc_cap = it.get("code_caption") or []
                out.append({"node": node, "page": page, "type": "code_block", "code_body": code,
                            "img_path": render.get("img_path", ""),
                            "caption": " ".join(doc_cap) if isinstance(doc_cap, list) else str(doc_cap),
                            "language": it.get("sub_type") or ""})
    return out


def _split_pages(markdown: str) -> list[tuple[int, str]]:
    matches = list(_PAGE_RE.finditer(markdown))
    if not matches:
        return [(1, markdown)]
    out = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
        out.append((int(m.group(1)), markdown[start:end].strip()))
    return out


def _make_rag(working_dir: Path, embed_backend: str, structural_index: dict | None = None):
    """Construct a MiniRAG instance (replaces RAGAnything/LightRAG). Multimodal captioning is handled
    by the pipeline itself (see _content_list_to_text_stream) since MiniRAG only has insert(text);
    semantic entity types are passed through addon_params from the repo-wide KG schema. The structural
    layer (heading nodes + contains/mentions) is added post-build via
    structural.add_structure_to_graph, not a per-chunk hook."""

    embedding_func = _make_embedding_func(embed_backend)  # already a minirag EmbeddingFunc
    addon: dict = {"entity_types": list(SEMANTIC_ENTITY_TYPES)}
    if structural_index:
        addon["structural_index"] = structural_index
    chunk_size = KG_CHUNK_SIZE
    # extract.py sets KG_MAX_ASYNC=4 for --llm local (Ollama serializes requests) -- must be
    # wired through explicitly, MiniRAG otherwise defaults to 16 concurrent calls.
    max_async = int(os.getenv("KG_MAX_ASYNC", "16"))
    print(f"[mini] MiniRAG · chunk_token_size={chunk_size} · llm_model_max_async={max_async} · "
          f"marker-aware chunking (clean, cacheable)")
    return MiniRAG(
        working_dir=str(working_dir),
        llm_model_func=_make_llm_func(),
        llm_model_name=KG_LLM_MODEL,
        llm_model_max_async=max_async,
        embedding_func=embedding_func,
        chunking_func=marker_chunking_func_mini,
        chunk_token_size=chunk_size,
        graph_storage="NetworkXStorage",
        addon_params=addon,
    )


def _html_table_to_md(html: str) -> str:
    """Convert a MinerU HTML table to a clean markdown table (bs4) — far more readable to the KG LLM
    than raw <table> markup. Returns "" if it can't be parsed."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        rows = []
        for tr in soup.find_all("tr"):
            cells = [" ".join(c.get_text(" ", strip=True).split()) for c in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)
        if not rows:
            return ""
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows]
        md = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * ncol) + " |"]
        md += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(md)
    except Exception:
        return ""


_MD_ESCAPE_RE = re.compile(r"\\([_*`#\[\]()~])")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)\w*\s*$")
def _clean_code_body(body: str) -> tuple[str, str, str]:
    """Split a parser `code` block into (prose before, code, prose after).

    OCR merges the paragraphs around an example into `code_body`, so that prose never reaches the
    sentence pool and the code block carries unsayable narration. The split is decided from syntax
    shape alone (operator/identifier density), never a word list, so it holds for any language."""
    text = _MD_ESCAPE_RE.sub(r"\1", body)
    lines = [ln.rstrip() for ln in text.splitlines() if not _FENCE_RE.match(ln)]
    sure = [i for i, ln in enumerate(lines) if ln.strip()
            and (_word_ratio(ln) < 0.5
                 or (_CODE_END_RE.search(ln.strip()) and _CODE_OPEN_RE.search(ln)
                     and _CODE_TOKEN_RE.search(ln)))]
    # Only split when a wrapped prose line is actually present; otherwise every line is the listing.
    prose = [ln for ln in lines if _word_ratio(ln) >= 0.75 and len(ln.split()) >= 8]
    if not sure or not prose:
        return "", "\n".join(lines).strip(), ""
    lo, hi = min(sure), max(sure)
    while lo > 0 and lines[lo - 1].strip() and _CODE_TOKEN_RE.search(lines[lo - 1]):
        lo -= 1
    while hi + 1 < len(lines) and lines[hi + 1].strip() and _CODE_TOKEN_RE.search(lines[hi + 1]):
        hi += 1
    return ("\n".join(lines[:lo]).strip(), "\n".join(lines[lo:hi + 1]).strip(),
            "\n".join(lines[hi + 1:]).strip())


_TABLE_SUMMARY_CACHE = "kg_table_summary_v1"


async def _caption_table(llm_func, html: str, existing_caption: str = "") -> tuple[str, str]:
    """Return (markdown_table, one_line_summary). Markdown via bs4; summary via the KG text LLM so the
    table's meaning (columns compared + key finding) feeds entity extraction and the slide caption."""
    md = _html_table_to_md(html)
    body_for_prompt = md or html[:1500]
    prompt = ("Summarize in ONE factual sentence what this table shows for a knowledge graph: the "
              "variables/columns being compared and the key finding or contrast. Do not invent numbers.\n\n"
              + (f"Caption: {existing_caption}\n" if existing_caption else "")
              + f"Table:\n{body_for_prompt[:2000]}")
    # The summary is part of the text stream that is cut into chunks: kept across rebuilds (even --force), so a reworded
    # summary never shifts every later chunk boundary and invalidates the whole extraction cache.
    stored = cache_path(_TABLE_SUMMARY_CACHE, KG_LLM_MODEL, prompt)
    try:
        return md, json.loads(stored.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        summary = (await llm_func(prompt) or "").strip()
    except Exception as e:
        print(f"[mini-mm] table summary failed: {e}")
        return md, existing_caption
    cached_json(_TABLE_SUMMARY_CACHE, KG_LLM_MODEL, prompt, lambda: summary)
    return md, summary


async def _content_list_to_text_stream(content_list_path: str, llm_func, *, structural: bool,
                                       toc_levels: dict | None, toc_entries: list | None,
                                       working_dir: Path,
                                       code_renders: dict[int, dict] | None = None
                                       ) -> tuple[str, dict[int, str], dict[int, dict]]:
    """Flatten a MinerU/OCR content_list into ONE markdown text stream for MiniRAG's insert(text):
    text/code kept verbatim, a figure by its own caption (no model: a figure without one is decoration), tables
    converted to markdown + a one-line LLM summary, equations inlined. Every block is prefixed with its `[[p|n]]` struct marker (same
    _structure_index node map) so the structural layer can attribute concepts to headings;
    marker_chunking_func_mini strips the markers before the LLM sees the text.

    Returns ``(text_stream, asset_captions, image_decisions)``; a figure is ``informative`` exactly when the document
    captions it."""
    base = Path(content_list_path).parent
    items = json.loads(Path(content_list_path).read_text(encoding="utf-8"))
    per = _structure_index(str(content_list_path), toc_levels, toc_entries, 0) if structural else None

    def _mark(i: int) -> str:
        if not per:
            return ""
        s = per[i]
        return _STRUCT_MARK.format(page=s["page"], node=s["node"])

    def _cap(v) -> str:
        return " ".join(v) if isinstance(v, list) else str(v or "")

    # Summarise tables concurrently, bounded by KG_MM_ASYNC; a tqdm bar shows progress.
    sem = asyncio.Semaphore(KG_MM_ASYNC)

    async def _tbl_task(i: int, it: dict, bar):
        cap = _cap(it.get("table_caption"))
        async with sem:
            md, summary = await _caption_table(llm_func, (it.get("table_body") or "").strip(), cap)
        bar.update(1)
        return i, (md, summary)

    figures = analyze(items)

    image_decisions: dict[int, dict] = {}
    for i, it in enumerate(items):
        if it.get("type") not in ("image", "chart"):
            continue
        info = figures.get(Path(it.get("img_path") or "").name, {}) if it.get("img_path") else {}
        if not it.get("img_path"):
            image_decisions[i] = {"classification": "decorative", "caption": "", "reason": "image_path_missing"}
        elif info.get("decorative"):
            image_decisions[i] = {"classification": "decorative", "caption": "", "reason": "geometry_decorative"}
        elif info.get("real_caption"):
            image_decisions[i] = {"classification": "informative", "caption": info["real_caption"], "reason": "caption"}
        else:
            image_decisions[i] = {"classification": "decorative", "caption": "", "reason": "no_caption"}

    tbl_items = [(i, it) for i, it in enumerate(items)
                 if it.get("type") == "table" and (it.get("table_body") or "").strip()]
    tbl_res = {}
    if tbl_items:
        with tqdm(total=len(tbl_items), desc="[mini-mm] captioning tables", unit="tbl") as bar:
            tbl_res = dict(await asyncio.gather(*(_tbl_task(i, it, bar) for i, it in tbl_items)))

    parts: list[str] = []
    asset_captions: dict[int, str] = {}
    for i, it in enumerate(items):
        t = it.get("type")
        mk = _mark(i)
        if t == "text":
            txt = (it.get("text") or "").strip()
            if txt:
                parts.append(mk + txt)
        elif t == "code":
            body = (it.get("code_body") or "").strip()
            if body:
                before, code, after = _clean_code_body(body)
                code = ((code_renders or {}).get(i) or {}).get("code") or code
                if before:
                    parts.append(mk + before)
                if code:
                    parts.append(f"{mk}```\n{code}\n```")
                if after:
                    parts.append(mk + after)
        elif t in ("image", "chart"):
            decision = image_decisions.get(i) or {
                "classification": "uncertain", "caption": "", "reason": "decision_missing"
            }
            text = str(decision.get("caption") or "").strip()
            if decision.get("classification") == "informative" and text:
                # the document's own caption: links the figure to the concepts it names
                parts.append(f"{mk}[Figure] {text}")
                asset_captions[i] = text
        elif t == "table":
            r = tbl_res.get(i)
            if r:
                md, summary = r
                body = (it.get("table_body") or "").strip()
                # Preserve the cache-stable retrieval stream. source_units() splits this exact
                # paragraph into a derived summary unit and a source-owned markdown-table unit.
                parts.append(f"{mk}[Table] {summary}\n{md or body}".strip())
                if summary:
                    asset_captions[i] = summary
        elif t == "equation":
            latex = (it.get("text") or it.get("latex") or "").strip()
            if latex:
                parts.append(f"{mk}$$ {latex} $$")
    return "\n\n".join(parts), asset_captions, image_decisions


async def build(doc: str, mode: str, embed_backend: str, include_images: bool,
                multimodal: bool = True, content_list_path: str | None = None,
                kb_suffix: str = "", multimodal_content: bool = False,
                mm_pages: tuple[int, int] | None = None, structural: bool = False,
                kb_dir: Path | None = None, pdf_path: Path | None = None) -> Path:
    document_id, resolved_pdf_path = _resolve_document(doc)
    # `_resolve_document` only looks under RAW_DIR -- callers whose source PDF lives elsewhere (e.g.
    # `extract_one` for a page-range-trimmed file under RAW_DIR/_trimmed/) must pass the real path
    # explicitly, or TOC-backbone silently falls back to the less-reliable heuristic (pdf_path=None ->
    # `_embedded_outline_entries` returns None).
    if pdf_path is None:
        pdf_path = resolved_pdf_path
    working_dir = Path(kb_dir) if kb_dir else KB_DIR / f"{document_id}__{mode}{kb_suffix}"
    working_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 64}\nBuilding KG  |  doc='{document_id}'  mode='{mode}'  embed='{embed_backend}'  multimodal={multimodal}")
    print(f"working_dir = {working_dir}\n{'=' * 64}")

    t0 = time.perf_counter()
    struct_idx = None
    image_filter_report: dict = {}
    if content_list_path and multimodal_content:
        toc_levels = None
        toc_entries = None  # embedded outline -> TOC-backbone (authoritative headings)
        if structural:
            toc_entries = _embedded_outline_entries(pdf_path)
            if toc_entries:
                print(f"[structural] embedded PDF outline found ({len(toc_entries)} entries) "
                      f"-> TOC-backbone heading extraction")
            else:
                toc_levels = _toc_levels(content_list_path)
            struct_idx = _build_structural_index(content_list_path, document_id, toc_levels=toc_levels,
                                                 pdf_path=pdf_path, toc_entries=toc_entries)
        # MiniRAG only inserts text -> flatten the content_list (figures by their own caption, tables with a
        # one-line summary) into one stream.
        # Code listings are cropped from the PDF: the crop keeps the typeset colour + indentation the
        # OCR text layer loses, and its text layer gives back the indented source.
        code_renders = render_code_blocks(
            json.loads(Path(content_list_path).read_text(encoding="utf-8")),
            pdf_path, working_dir / "images")
        if code_renders:
            print(f"[assets] code listings rendered from PDF: {len(code_renders)}")
        text_stream, asset_captions, image_decisions = await _content_list_to_text_stream(
            content_list_path, _make_llm_func(), structural=structural,
            toc_levels=toc_levels, toc_entries=toc_entries, working_dir=working_dir,
            code_renders=code_renders)
        image_filter_report = dict(Counter(
            str(d.get("classification") or "uncertain") for d in image_decisions.values()
        ))
        image_filter_report["total"] = len(image_decisions)
        image_filter_report["reasons"] = dict(Counter(
            str(d.get("reason") or "unspecified") for d in image_decisions.values()
        ))
        print(f"[images] informative={image_filter_report.get('informative', 0)} "
              f"decorative={image_filter_report.get('decorative', 0)} "
              f"uncertain={image_filter_report.get('uncertain', 0)}")
        print(f"[mini] multimodal text stream: {len(text_stream)} chars, "
              f"{len(asset_captions)} captioned assets, from {content_list_path}")
        if structural:
            # Part 2: page-anchored asset->heading sidecar (same node map as text markers), now with the
            # VLM/LLM captions filled in.
            try:
                assets = _build_asset_headings(content_list_path, toc_levels=toc_levels,
                                               toc_entries=toc_entries, working_dir=working_dir,
                                               asset_captions=asset_captions,
                                               code_renders=code_renders)
                # Persist the validated form once.  Existing KBs receive the same
                # normalisation lazily in ``load_asset_headings`` without touching
                # embeddings or rebuilding the graph.
                assets = validate_asset_records(assets)
                (working_dir / "asset_headings.json").write_text(
                    json.dumps(assets, ensure_ascii=False), encoding="utf-8")
                capd = sum(1 for a in assets if a.get("caption"))
                print(f"[assets] asset_headings.json: {len(assets)} assets ({capd} with caption)")
            except Exception as e:
                print(f"[assets] asset_headings.json skipped: {e}")
            (working_dir / "structural_index.json").write_text(
                json.dumps(struct_idx, ensure_ascii=False), encoding="utf-8")
        rag = _make_rag(working_dir, embed_backend=embed_backend, structural_index=struct_idx)
        await _with_extract_heartbeat(rag.ainsert(text_stream))
    elif content_list_path:
        content_list = _mineru_content_list_to_text(Path(content_list_path))
        text_stream = "\n\n".join((c.get("text") or "").strip() for c in content_list if (c.get("text") or "").strip())
        print(f"[mini] text-only stream: {len(text_stream)} chars ({len(content_list)} blocks)")
        rag = _make_rag(working_dir, embed_backend=embed_backend)
        await _with_extract_heartbeat(rag.ainsert(text_stream))
    else:
        raise NotImplementedError(
            f"mode='{mode}' without content_list_path is not supported after the MiniRAG migration "
            f"(RAGAnything process_document_complete / insert_content_list were removed). Build via "
            f"extract.py, which always passes a parsed content_list.")

    # MiniRAG.ainsert persists to graphml itself (index_done_callback); no finalize_storages needed.
    # Consumers use this sidecar to invalidate pre-provenance KB artefacts and caches.
    (working_dir / "evidence_schema.json").write_text(json.dumps({
        "version": "evidence-unit-v2",
        "source_owned": ["text", "code", "table", "formula"],
        "derived_metadata": ["figure_caption", "llm_table_summary"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    write_schema_metadata(working_dir)
    elapsed = time.perf_counter() - t0

    graphml = working_dir / "graph_chunk_entity_relation.graphml"
    print(f"\n{'=' * 64}")
    if graphml.exists():
        print(f"DONE. Knowledge graph: {graphml}")
        try:
            g = nx.read_graphml(graphml)
            # Structural layer: add heading nodes + contains/mentions/assets on the finished graph.
            if structural and struct_idx:
                try:
                    chunks = load_text_chunks(working_dir)
                except Exception:
                    chunks = {}
                assets = load_asset_headings(working_dir) or []
                n_head = add_structure_to_graph(g, struct_idx, chunks, file_path=f"{document_id}.pdf",
                                                assets=assets)
                print(f"[structural] post-build pass: +{n_head} heading nodes (contains/mentions/assets)")
            schema_report = normalize_graph_schema(g)
            validate_graph_schema(g)
            nx.write_graphml(g, graphml)
            print(f"[schema] {schema_report}")
        except Exception as e:
            # Schema violations must not be hidden: an invalid graph would contaminate slide generation.
            raise RuntimeError(f"KG schema finalization failed: {e}") from e
        # Deterministic KG enrichment (no LLM): salience.json + prerequisite_of edges, used by
        # Only meaningful with the structural layer.
        if structural:
            # NDAKG-style cleanup FIRST (prune chunk-bleed `mentions` edges so a chunk straddling a
            # section boundary can't leak entities into the wrong heading), THEN salience on the
            # cleaned graph.
            try:
                clean_kb(working_dir, threshold=Config.KG_DEDUP_THRESHOLD, dedup=Config.KG_DEDUP_ENABLED)
            except Exception as e:
                print(f"[kg_dedup] cleanup skipped: {e}")
            try:
                enrich_kb(working_dir)
            except Exception as e:
                print(f"[kg_salience] enrichment skipped: {e}")
            # The slide Evidence Service ranks exact source sentences, while
            # MiniRAG itself stores coarser chunk/entity vectors.  Precompute
            # the sentence layer now so the first deck run does not pay that
            # cost lazily.  Dynamic teaching-point queries are still embedded
            # later, once, then reused across every MiniRAG index.
            try:
                evidence = evidence_store.EvidenceStore(working_dir, [])
                sentence_texts = [row["text"] for row in evidence.sentences]
                if sentence_texts:
                    evidence.embedder.embed(sentence_texts)
                    evidence.embedder.save()
                print(f"[evidence] precomputed {len(sentence_texts)} sentence embedding(s)")
            except Exception as e:
                # Local/offline KG builds remain usable; a later deck run can
                # warm the same persistent cache when its backend is available.
                print(f"[evidence] sentence embedding precompute skipped: {e}")
        # Always render the self-contained viewer next to the graphml.
        try:
            view_kg.render(graphml, working_dir / "kg_view.html")
            print(f"[view] kg_view.html rendered -> {working_dir / 'kg_view.html'}")
        except Exception as e:
            print(f"[view] kg_view.html skipped: {e}")
    else:
        print(f"DONE, but graphml not found at {graphml} — check working dir contents:")
        for p in sorted(working_dir.glob('*')):
            print("   ", p.name)
    # Include deterministic schema/enrichment and the sentence-embedding
    # precompute in the build timing reported to benchmark users.
    elapsed = time.perf_counter() - t0
    _print_run_report(elapsed, working_dir, image_filter_report=image_filter_report)
    print(f"{'=' * 64}\n")
    return working_dir


def _print_run_report(elapsed: float, working_dir: Path, *, image_filter_report: dict | None = None) -> None:
    mins, secs = divmod(int(elapsed), 60)
    emb_free = EMBED_USAGE.get("backend") == "local"

    nodes = edges = "?"
    try:
        g = nx.read_graphml(working_dir / "graph_chunk_entity_relation.graphml")
        nodes, edges = g.number_of_nodes(), g.number_of_edges()
    except Exception:
        pass

    print(f"{'─' * 64}")
    print("RUN REPORT")
    print(f"  Time elapsed : {mins}m {secs}s  ({elapsed:.1f}s)")
    print(f"  Graph        : {nodes} entities, {edges} relations")
    print(f"  LLM ({KG_LLM_MODEL}) : calls={LLM_USAGE.calls}  total_tokens={LLM_USAGE.total:,}")
    print(f"  Embeddings ({KG_EMBED_MODEL if not emb_free else KG_LOCAL_EMBED_MODEL}) : "
          f"calls={EMBED_USAGE['calls']}  tokens={EMBED_USAGE['tokens']:,}"
          + ("  (local, FREE)" if emb_free else ""))
    if image_filter_report:
        print("  Images       : "
              f"informative={image_filter_report.get('informative', 0)}  "
              f"decorative={image_filter_report.get('decorative', 0)}  "
              f"uncertain={image_filter_report.get('uncertain', 0)}")
    try:
        (working_dir / "run_report.json").write_text(json.dumps({
            "elapsed_seconds": round(elapsed, 1),
            "graph": {"entities": nodes, "relations": edges},
            "llm_model": KG_LLM_MODEL,
            "llm": {"calls": LLM_USAGE.calls, "total_tokens": LLM_USAGE.total},
            "images": image_filter_report or {},
            "embeddings": {"backend": EMBED_USAGE.get("backend"), "calls": EMBED_USAGE["calls"],
                           "tokens": EMBED_USAGE["tokens"],
                           "model": KG_LOCAL_EMBED_MODEL if emb_free else KG_EMBED_MODEL,
                           "dim": None if emb_free else KG_EMBED_DIM},
        }, indent=2), encoding="utf-8")
    except Exception:
        pass
