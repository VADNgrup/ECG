import argparse
import asyncio
import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time as _t
import unicodedata
from pathlib import Path

import fitz
import psutil
import torch
from tqdm import tqdm

from src.rag import build_kg as B, llm_mode, ocr as _ocr, view_kg
from src.rag.structural import _MIN_RELIABLE_TOC_ENTRIES
from src.utils.config import Config
from src.utils.llm import end_llm_run, llm_run_active, set_llm_phase, start_llm_run


def _setup_progress() -> None:
    """Route the minirag/mineru library loggers to the root logger at INFO. (Previously also stripped
    every StreamHandler off the root logger and replaced it with a handler matching a "Chunk N of M"
    log pattern left over from the pre-MiniRAG RAGAnything/LightRAG pipeline -- that pattern no longer
    appears anywhere, so the handler never fired, while the StreamHandler removal silently swallowed
    ALL logging.*-based output (including library warnings/errors from minirag/openai/httpx) for the
    rest of the run. That combination made a real, slow-but-running build look like a total freeze,
    especially with `--llm local` where a queued Ollama request is the main source of delay.)"""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for name in ("minirag", "mineru"):
        lg = logging.getLogger(name)
        lg.propagate = True
        lg.setLevel(logging.INFO)



ASSETS_DIR = B.DATA_DIR / "assets"
PARSE_DIR = B.DATA_DIR / "cache" / "parse"


def _reset_usage() -> None:
    B.LLM_USAGE.prompt = B.LLM_USAGE.completion = B.LLM_USAGE.total = B.LLM_USAGE.calls = 0
    B.EMBED_USAGE["tokens"] = 0
    B.EMBED_USAGE["calls"] = 0


def _find_content_list(parse_dir: Path) -> Path | None:
    if not parse_dir.exists():
        return None
    cands = sorted(parse_dir.rglob("*_content_list.json"))
    primary = [c for c in cands if not c.name.endswith("_v2.json")]
    picks = primary or cands
    return picks[0] if picks else None


def _page_tag(pages: tuple[int, int] | None) -> str:
    return f"__p{pages[0]}-{pages[1]}" if pages else ""


def _pdf_pages(pdf: Path) -> int:
    try:
        with fitz.open(str(pdf)) as d:
            return d.page_count
    except Exception:
        return 0


_PARSE_OPTS: dict = {"parser": "mineru", "ocr": "auto", "method": "smart", "formula": None, "table": None,
                     "jobs": 1, "device": "auto"}

_WIN_BAR = None


async def _with_heartbeat(coro, label: str):
    start = _t.time()
    task = asyncio.ensure_future(coro)
    while True:
        done, _ = await asyncio.wait({task}, timeout=12)
        if done:
            break
        el = int(_t.time() - start)
        msg = f"{label} · {el // 60}m{el % 60:02d}s"
        if _WIN_BAR is not None:
            _WIN_BAR.set_postfix_str(msg)
            _WIN_BAR.refresh()
        else:
            tqdm.write(f"[parse] {msg} (still running)")
    return await task

_KEEP_ON_REBUILD = {"kv_store_llm_response_cache.json"}
# What a KB holds depends on this code: raise it when the KG build changes (a KB of another version is rebuilt once,
# reusing the page reading and the LLM extraction cache).
KB_VERSION = "kb-2026-10-06-no-cooccurrence"
_KB_STAMP = "kb_version.json"


def _kb_version(kb: Path) -> str:
    try:
        return json.loads((kb / _KB_STAMP).read_text(encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        return ""


def _clear_for_rebuild(kb: Path) -> None:
    if not kb.exists():
        return
    kept = False
    for p in kb.iterdir():
        if p.is_file() and p.name not in _KEEP_ON_REBUILD:
            p.unlink()
        elif p.is_file():
            kept = True
    print(f"[rebuild] cleared derived stores in {kb}"
          + ("  (kept LLM extraction cache -> rebuild reuses it)" if kept else ""))


_METHOD_CACHE: dict = {}


def _resolve_method(pdf: Path) -> str:
    req = _PARSE_OPTS.get("method", "smart")
    if req != "smart":
        return req
    key = str(pdf)
    if key in _METHOD_CACHE:
        return _METHOD_CACHE[key]
    m = "auto"
    try:
        with fitz.open(str(pdf)) as d:
            n = d.page_count
            idxs = list(range(0, n, max(1, n // 8))) or [0]
            chars = [len(d[p].get_text("text").strip()) for p in idxs]
            digital = sum(1 for c in chars if c > 200)
            m = "txt" if digital >= len(chars) - 1 else ("ocr" if digital <= len(chars) // 3 else "auto")
    except Exception:
        m = "auto"
    _METHOD_CACHE[key] = m
    tqdm.write(f"[parse] auto method='{m}' for {pdf.name}")
    return m


def _resolve_ocr(pdf: Path) -> str:
    """--ocr auto: skip the costly vision-LLM OCR pass when the PDF already has a reliable embedded
    outline (real bookmarks, not a scan) AND a clean digital text layer -- OCR was only ever needed for
    scanned pages or garbled font/ToUnicode maps, not every book. Reuses _resolve_method's own digital/
    scanned sample (and its cache), so an explicit --parse-method override is respected automatically."""
    toc = B._embedded_outline_levels(pdf)
    n = len(toc) if toc else 0
    if n >= _MIN_RELIABLE_TOC_ENTRIES and _resolve_method(pdf) == "txt":
        tqdm.write(f"[ocr] auto: embedded TOC ({n} entries) + digital text layer -> --ocr none")
        return "none"
    reason = "no reliable embedded TOC" if n < _MIN_RELIABLE_TOC_ENTRIES else "scanned/mixed text layer"
    tqdm.write(f"[ocr] auto: {reason} -> --ocr cloud")
    return "cloud"


_EXTRA_MATH = set("√∑∫∬∮≤≥≠≈≡±×÷∞∂∇∏∈∉⊂⊆⊇∪∩∀∃⇒⇔→←↔°′″"
                  "⁰¹²³⁴⁵⁶⁷⁸⁹₀₁₂₃₄₅₆₇₈₉")
_FT_CACHE: dict = {}


def _detect_ft(pdf: Path) -> tuple[bool, bool]:
    key = str(pdf)
    if key in _FT_CACHE:
        return _FT_CACHE[key]
    technical = True
    try:
        with fitz.open(str(pdf)) as d:
            n = d.page_count
            idxs = list(range(0, n, max(1, n // 12))) or [0]
            tot = digit = math = imgs = 0
            for p in idxs:
                page = d[p]
                txt = page.get_text("text")
                tot += len(txt)
                digit += sum(1 for ch in txt if ch.isdigit())
                math += sum(1 for ch in txt
                            if ch in _EXTRA_MATH or (ord(ch) > 127 and unicodedata.category(ch) == "Sm"))
                imgs += len(page.get_images(full=True))
            digit_ratio = digit / max(1, tot)
            imgs_pp = imgs / max(1, len(idxs))
            technical = digit_ratio >= 0.01 or imgs_pp >= 0.25 or math >= 4
    except Exception:
        technical = True
    _FT_CACHE[key] = (technical, technical)
    tqdm.write(f"[parse] smart profile={'technical' if technical else 'prose'} "
               f"-> formula/table={technical} for {pdf.name}")
    return technical, technical


def _resolve_ft(pdf: Path) -> tuple[bool, bool]:
    fo, to = _PARSE_OPTS.get("formula"), _PARSE_OPTS.get("table")
    if fo is not None and to is not None:
        return bool(fo), bool(to)
    df, dt = _detect_ft(pdf) if _PARSE_OPTS.get("method") == "smart" else (True, True)
    return (df if fo is None else bool(fo), dt if to is None else bool(to))


def _resolve_device(req: str) -> str:
    if req == "cpu":
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    if req == "cuda":
        print("[parse] --device cuda requested but no CUDA torch available -> falling back to CPU "
              "(install a CUDA build of torch into the 'slide' env to enable GPU)")
    return "cpu"


async def _run_mineru(pdf: Path, out_dir: Path, *, method: str, formula: bool, table: bool,
                      backend: str, pages: tuple[int, int] | None, device: str | None,
                      timeout: int) -> None:
    """Shell out to the `mineru` CLI to parse a PDF -> content_list (replaces RAGAnything's MinerU
    wrapper, which was removed with the MiniRAG migration). Same command RAGAnything built:
    `mineru -p <in> -o <out> -m <method> [-b <backend>] [-s <start> -e <end>] [-f false] [-t false]`
    (omit -f/-t = enabled; page range is 0-based). This mineru build has NO `-d` flag — device/VRAM
    come from the MINERU_DEVICE_MODE / MINERU_VIRTUAL_VRAM_SIZE env vars set in main()."""
    cmd = ["mineru", "-p", str(pdf), "-o", str(out_dir), "-m", method]
    if backend:
        cmd += ["-b", backend]
    if pages is not None:
        cmd += ["-s", str(pages[0]), "-e", str(pages[1])]
    if not formula:
        cmd += ["-f", "false"]
    if not table:
        cmd += ["-t", "false"]

    def _run():
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              env={**os.environ})
    r = await asyncio.to_thread(_run)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip()[-500:]
        raise RuntimeError(f"mineru rc={r.returncode}: {tail}")


async def _parse_range(pdf: Path, embed: str, parse_dir: Path, pages: tuple[int, int] | None,
                       formula: bool | None = None, table: bool | None = None) -> Path | None:
    parse_dir.mkdir(parents=True, exist_ok=True)
    rf, rt = _resolve_ft(pdf)
    use_formula = rf if formula is None else formula
    use_table = rt if table is None else table
    npages = (pages[1] - pages[0] + 1) if pages else 60
    light = not use_formula and not use_table
    per, lo, hi = (30, 300, 1800) if light else (60, 600, 3600)
    timeout = max(lo, min(hi, npages * per))
    await _run_mineru(pdf, parse_dir, method=_resolve_method(pdf), formula=use_formula,
                      table=use_table, backend="pipeline", pages=pages,
                      device=_PARSE_OPTS.get("device"), timeout=timeout)
    return _find_content_list(parse_dir)


async def _parse_one_window(pdf: Path, embed: str, stem: str, s: int, e: int) -> list[tuple[Path, int]]:
    wdir = PARSE_DIR / f"_mineru_{stem}__w{s}-{e}"
    cl = _find_content_list(wdir)
    if cl is None:
        rf0, rt0 = _resolve_ft(pdf)
        tqdm.write(f"[parse] window {s}-{e} · method={_resolve_method(pdf)} formula={rf0} table={rt0} — running…")
        try:
            cl = await _with_heartbeat(_parse_range(pdf, embed, wdir, (s, e)), f"w{s}-{e}")
        except Exception as ex:
            tqdm.write(f"[parse] pages {s}-{e} FAILED ({str(ex)[:80]})")
            cl = None
        rf, rt = _resolve_ft(pdf)
        if cl is None and (rf or rt):
            tqdm.write(f"[parse] pages {s}-{e}: auto-recovery retry without formula/table")
            shutil.rmtree(wdir, ignore_errors=True)
            try:
                cl = await _with_heartbeat(
                    _parse_range(pdf, embed, wdir, (s, e), formula=False, table=False), f"w{s}-{e} (light)")
            except Exception as ex:
                tqdm.write(f"[parse] pages {s}-{e} still FAILED ({str(ex)[:60]})")
                cl = None
    if cl is not None:
        return [(cl, s)]
    if s >= e:
        tqdm.write(f"[parse] page {s}: UNPARSEABLE — skipping this single page")
        return []
    mid = (s + e) // 2
    tqdm.write(f"[parse] splitting {s}-{e} -> {s}-{mid} + {mid + 1}-{e}")
    return (await _parse_one_window(pdf, embed, stem, s, mid)
            + await _parse_one_window(pdf, embed, stem, mid + 1, e))


async def _parse_windows(pdf: Path, embed: str, total: int, window: int) -> Path:
    stem = pdf.stem
    merged_dir = PARSE_DIR / f"_mineru_{stem}"
    merged_cl = merged_dir / f"{stem}_content_list.json"
    if merged_cl.exists():
        print(f"[parse] cached merged content_list: {merged_cl}")
        return merged_cl
    (merged_dir / "images").mkdir(parents=True, exist_ok=True)
    jobs = max(1, int(_PARSE_OPTS.get("jobs", 1)))
    sem = asyncio.Semaphore(jobs)
    ranges = [(s, min(s + window - 1, total - 1)) for s in range(0, total, window)]
    bar = tqdm(total=len(ranges), desc="parse windows", unit="win")
    global _WIN_BAR
    _WIN_BAR = bar

    async def _run(s: int, e: int) -> list[tuple[Path, int]]:
        async with sem:
            r = await _parse_one_window(pdf, embed, stem, s, e)
        bar.update(1)
        return r

    print(f"[parse] {len(ranges)} windows × {window} pages · {jobs} parallel · method={_PARSE_OPTS['method']}")
    results = await asyncio.gather(*(_run(s, e) for s, e in ranges))
    bar.close()
    segments: list[tuple[Path, int]] = [seg for r in results for seg in r]
    if not segments:
        raise SystemExit("all MinerU parse windows failed")
    all_items: list[dict] = []
    for cl, start in sorted(segments, key=lambda x: x[1]):
        items = json.loads(cl.read_text(encoding="utf-8"))
        pgs = [it["page_idx"] for it in items if isinstance(it.get("page_idx"), int)]
        wmin = min(pgs) if pgs else 0
        img_src = cl.parent / "images"
        if img_src.exists():
            for img in img_src.iterdir():
                dst = merged_dir / "images" / img.name
                if img.is_file() and not dst.exists():
                    shutil.copy2(img, dst)
        for it in items:
            if isinstance(it.get("page_idx"), int):
                it["page_idx"] = start + (it["page_idx"] - wmin)
            all_items.append(it)
    merged_cl.write_text(json.dumps(all_items, ensure_ascii=False), encoding="utf-8")
    print(f"[parse] merged {len(segments)} segment(s) -> {merged_cl}  ({len(all_items)} blocks)")
    return merged_cl


async def _parse_ocr(pdf: Path, pages: tuple[int, int] | None) -> Path:
    """Every rendered page read by the vision LLM (VLM_*)."""
    stem = pdf.stem
    odir = PARSE_DIR / (f"_ocrcloud_{stem}{_page_tag(pages)}" if pages else f"_ocrcloud_{stem}")
    rng = f" pages {pages[0]}-{pages[1]}" if pages else ""
    print(f"[parse] OCR (cloud) parsing '{pdf.name}'{rng}…")
    bar = tqdm(desc=f"ocr-cloud {stem}", unit="pg")

    def _cb(done: int, total: int) -> None:
        if bar.total != total:
            bar.total = total
        bar.n = done
        bar.refresh()

    try:
        cl = await asyncio.to_thread(_ocr.ocr_pdf, pdf, odir, pages, _cb)
    finally:
        bar.close()
    if not cl or not cl.exists():
        raise SystemExit(f"OCR produced no content_list under {odir}")
    n = len(json.loads(cl.read_text(encoding="utf-8")))
    print(f"[parse] OCR (cloud) -> {cl}  ({n} blocks)")
    return cl


async def _parse(pdf: Path, embed: str, pages: tuple[int, int] | None = None, window: int = 40) -> Path:
    stem = pdf.stem
    ocr = _PARSE_OPTS.get("ocr")
    if ocr == "auto":
        ocr = _resolve_ocr(pdf)
    if ocr == "cloud":
        return await _parse_ocr(pdf, pages)
    # non-OCR path always uses MinerU.
    if pages:
        parse_dir = PARSE_DIR / f"_mineru_{stem}{_page_tag(pages)}"
        cached = _find_content_list(parse_dir)
        if cached:
            print(f"[parse] cached content_list: {cached}")
            return cached
        print(f"[parse] MinerU parsing pages {pages[0]}-{pages[1]}…")
        cl = await _parse_range(pdf, embed, parse_dir, pages)
        if not cl:
            raise SystemExit(f"parse produced no *_content_list.json under {parse_dir}")
        return cl
    total = _pdf_pages(pdf)
    if total and total > window:
        print(f"[parse] {total} pages > window {window} -> parsing in windows (memory-safe)")
        return await _parse_windows(pdf, embed, total, window)
    parse_dir = PARSE_DIR / f"_mineru_{stem}"
    cached = _find_content_list(parse_dir)
    if cached:
        print(f"[parse] cached content_list: {cached}")
        return cached
    print(f"[parse] MinerU parsing '{pdf}' ({total or '?'} pages)…")
    cl = await _parse_range(pdf, embed, parse_dir, None)
    if not cl:
        raise SystemExit(f"parse produced no *_content_list.json under {parse_dir}")
    return cl


def configure(ocr: str = "auto") -> None:
    """Parse options: `ocr` = auto | none | cloud | local (the vision step); the device is chosen automatically."""
    _PARSE_OPTS["ocr"] = ocr
    device = _resolve_device("auto")
    _PARSE_OPTS["device"] = device
    os.environ["MINERU_DEVICE_MODE"] = device
    if device == "cuda":
        os.environ.setdefault("MINERU_VIRTUAL_VRAM_SIZE", "3")
        print(f"[parse] GPU enabled (cuda · VRAM cap {os.environ['MINERU_VIRTUAL_VRAM_SIZE']}GB)")


def _embed_backend() -> str:
    """The KB's vectors use the pipeline's embedding model, so its retrieval shares one vector space."""
    return "openai" if Config.EMBED_BACKEND == "openai" else "local"


def forget(pdf: Path) -> list[Path]:
    """Delete everything the KG step of `pdf` would reuse -- its KB (LLM extraction cache included) and its parse
    caches (OCR pages, MinerU output and windows) -- so the next build starts from the PDF."""
    stem = pdf.stem
    own = re.compile(rf"_(?:mineru|ocr[a-z]+)_{re.escape(stem)}(?:__w\d+-\d+)?")
    targets = [B.KB_DIR / stem] + ([p for p in PARSE_DIR.iterdir() if own.fullmatch(p.name)] if PARSE_DIR.exists() else [])
    removed = []
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path)
    return removed


async def extract_one(pdf: Path, force: bool = False, pages: tuple[int, int] | None = None,
                      window: int = 40) -> Path:
    """Parse one PDF, build its KG (skipped when the KB exists, unless `force`) and render its viewer."""
    stem = pdf.stem
    tag = _page_tag(pages)
    embed = _embed_backend()
    kb = B.KB_DIR / f"{stem}{tag}"
    graphml = kb / "graph_chunk_entity_relation.graphml"
    built = False
    stale = graphml.exists() and _kb_version(kb) != KB_VERSION
    if graphml.exists() and not force and not stale:
        print(f"[skip] KG already exists: {kb}  (use --force to rebuild)")
    else:
        if stale and not force:
            print(f"[rebuild] the KG of {stem} was built by an older version of the KG step: rebuilding it "
                  f"(page reading and LLM extraction reused from cache)")
        if kb.exists():
            _clear_for_rebuild(kb)
        _reset_usage()
        own_run = not llm_run_active()          # inside main.py the KG build is one phase of the caller's run
        if own_run:
            start_llm_run(document_id=stem)
        status = "completed"
        llm_mode.ensure_ready()
        try:
            set_llm_phase("rag_parse")
            cl = await _parse(pdf, embed, pages, window)
            set_llm_phase("rag_extract")
            await B.build(doc=stem, mode="mineru", embed_backend=embed, include_images=True,
                          multimodal=True, content_list_path=str(cl),
                          multimodal_content=True, structural=True, kb_dir=kb, pdf_path=pdf)
            (kb / _KB_STAMP).write_text(json.dumps({"version": KB_VERSION}), encoding="utf-8")
        except Exception:
            status = "failed"
            raise
        finally:
            if own_run:
                summary = end_llm_run(status=status, output_path=str(kb))
                print(f"[llm summary] calls={summary.get('total_calls', 0)} "
                      f"total_tokens={summary.get('total_tokens', 0)} "
                      f"avg_tokens_per_call={summary.get('avg_total_tokens_per_call', 0)} "
                      f"path={summary.get('summary_path', '')}")
        built = True
    if graphml.exists():
        view_kg.render(graphml, kb / "kg_view.html")
    _save_assets(stem, kb, tag)
    return kb


def _save_assets(stem: str, kb: Path, tag: str = "") -> Path:
    dst = ASSETS_DIR / stem
    dst.mkdir(parents=True, exist_ok=True)
    html_name = f"kg_view{tag}.html" if tag else "kg_view.html"
    for src, name in [(kb / "graph_chunk_entity_relation.graphml", f"{stem}{tag}.graphml"),
                      (kb / "kg_view.html", html_name)]:
        if src.exists():
            shutil.copy2(src, dst / name)
            print(f"[assets] {dst / name}")
    return dst


def _pdfs(args) -> list[Path]:
    if args.filepath:
        p = Path(args.filepath)
        if not p.exists():
            raise SystemExit(f"file not found: {p}")
        return [p]
    if args.all:
        pdfs = sorted(B.RAW_DIR.glob("*.pdf"))
        if not pdfs:
            raise SystemExit(f"no PDFs in {B.RAW_DIR}")
        return pdfs
    raise SystemExit("provide --filepath <pdf> OR --all")


def _kill_descendants(timeout: float = 5.0) -> None:
    try:
        kids = psutil.Process().children(recursive=True)
    except Exception:
        return
    if not kids:
        return
    for p in kids:
        try:
            p.terminate()
        except Exception:
            pass
    _, alive = psutil.wait_procs(kids, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except Exception:
            pass
    tqdm.write(f"[cleanup] stopped {len(kids)} MinerU child process(es)")


def main():
    ap = argparse.ArgumentParser(
        description="Extract a document into its OWN structural knowledge graph + viewer HTML.")
    ap.add_argument("--filepath", help="path to a PDF to extract into a KG")
    ap.add_argument("--all", action="store_true", help="extract every PDF in data/raw/ that has no KB yet")
    ap.add_argument("--pages", default=None,
                    help="only extract a page range 'START-END' (0-based inclusive, e.g. '0-25')")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if the KB exists (keeps the LLM extraction cache -> near-free)")
    ap.add_argument("--ocr", default="cloud", choices=["auto", "none", "cloud"],
                    help="reading the pages: 'cloud' (default) = a vision LLM (.env VLM_*) OCRs every rendered "
                         "page; 'auto' = MinerU on the text layer when the PDF has an embedded outline and a "
                         "digital text layer, else 'cloud'; 'none' = always MinerU")
    ap.add_argument("--llm", default="cloud", choices=["cloud", "local"],
                    help="KG entity extraction and table captions: 'cloud' (default) = KG_LLM_*/OPENAI_API_KEY; "
                         "'local' = a local Ollama server (KG_LLM_*/KG_VLM_*, default qwen3.5:9b)")
    args = ap.parse_args()

    if not B.OPENAI_API_KEY:
        raise SystemExit("KG LLM API key not set (KG_LLM_API_KEY or OPENAI_API_KEY in .env)")
    print(f"[llm] {args.llm}: entity-extraction={B.KG_LLM_MODEL} ({B.LLM_BASE_URL}) · "
          f"vision={B.VLM_MODEL} ({B.VLM_BASE_URL})")
    configure(args.ocr)
    _setup_progress()

    pages = None
    if args.pages:
        try:
            s, e = args.pages.split("-")
            pages = (int(s), int(e))
            if pages[0] > pages[1]:
                raise ValueError
        except ValueError:
            raise SystemExit(f"--pages must be 'START-END' with START<=END (0-based), got {args.pages!r}")

    atexit.register(_kill_descendants)

    pdfs = _pdfs(args)
    rng = f" · pages {pages[0]}-{pages[1]}" if pages else ""
    print(f"[extract] {len(pdfs)} document(s){rng} · structural KG per file")
    try:
        for pdf in pdfs:
            print(f"\n{'#' * 64}\n# {pdf.name}\n{'#' * 64}")
            try:
                kb = asyncio.run(extract_one(pdf, args.force, pages))
                print(f"[done] KG: {kb}")
                html = kb / "kg_view.html"
                if html.exists():
                    print(f"[open] {html}")
            except Exception as e:
                print(f"[error] {pdf.name}: {type(e).__name__}: {e}")
                if args.filepath:
                    raise
    except KeyboardInterrupt:
        print("\n[interrupted] Ctrl+C — stopping MinerU child processes…")
        _kill_descendants()
        raise SystemExit(130)
    finally:
        _kill_descendants()


if __name__ == "__main__":
    main()
