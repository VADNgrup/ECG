from __future__ import annotations
import argparse
import json
import logging
import shutil
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
import sys
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from src.generator.slide_pick_and_merge import SlidePickMerge
from src.utils.config import Config
logger = logging.getLogger(__name__)

def _load_lecture_meta(json_path: Path) -> tuple[str, str]:
    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        data = data[0] if data and isinstance(data[0], dict) else {}
    if not isinstance(data, dict):
        return (json_path.stem, json_path.stem)
    lecture_id: str = data.get('lecture_id', json_path.stem)
    title: str = data.get('title') or data.get('lecture_title') or lecture_id
    return (title, lecture_id)

def run_pipeline(lecture_json_path: str, lecture_title: str | None = None, speaker_information: str = '', institution: str = '') -> None:
    json_path = Path(lecture_json_path).resolve()
    if not json_path.exists():
        raise FileNotFoundError(f'Lecture JSON not found: {json_path}')
    (inferred_title, lecture_id) = _load_lecture_meta(json_path)
    title = lecture_title or inferred_title
    print(f"\n{'=' * 60}")
    print(f'Phase 4: Slide Generation')
    print(f"{'=' * 60}\n")
    print(f'[slide_gen] Lecture  : {lecture_id}')
    print(f'[slide_gen] Title    : {title}')
    print(f"[slide_gen] Speaker  : {speaker_information or 'Slide Generation System'}")

    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    out_dir = _PROJECT_ROOT / 'output' / lecture_id / model_name
    html_path = out_dir / f'{lecture_id}.html'   # sole HTML output = Fabric canvas editor

    rebuild_needed = True
    if html_path.exists():
        input_mtime = json_path.stat().st_mtime
        html_mtime = html_path.stat().st_mtime
        rebuild_needed = input_mtime > html_mtime

    pptx_path = out_dir / f'{lecture_id}.pptx'
    layout_log_path = json_path.parent / f'{lecture_id}_layout_distribution.json'

    if rebuild_needed:
        print('[slide_gen] === Step 1: Building slide layout ===')
        if not title:
            title = json.load(open(json_path))['lecture_title']
        out_dir.mkdir(parents=True, exist_ok=True)
        picker = SlidePickMerge(lecture_json_path=str(json_path), lecture_title=title, speaker_information=speaker_information, deck_dir=out_dir, institution=institution)
        picker.build()
        print('[slide_gen] === Step 2: Slide construction complete ===')

        print(f"  Open in a browser: file:///{html_path}")
    else:
        print(f'[slide_gen] Deck HTML is up to date: {html_path}')

    # Always regenerate PPTX — fast (no LLM calls), layout_log is the source of truth
    pptx_needs_rebuild = True
    screenshots: list[bytes] = []
    if pptx_needs_rebuild:
        print('[slide_gen] === Step 3: Exporting to PPTX ===')
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            from src.generator.deck_pptx_exporter import export_pptx_from_layout_log
            n_pptx = export_pptx_from_layout_log(
                json_path, layout_log_path, pptx_path,
                html_path=None, speaker=speaker_information,
            )
            print(f'[slide_gen] PPTX: {n_pptx} slides → {pptx_path}')
        except Exception as err:
            print(f'[slide_gen] PPTX export failed ({err})')

    if rebuild_needed:
        try:
            screenshots = _capture_screenshots(html_path)   # {id}.html = Fabric canvas
            print(f'[slide_gen] Playwright: {len(screenshots)} slide previews captured.')
        except ImportError:
            print('[slide_gen] playwright not installed — slide previews skipped.')
        except Exception as err:
            print(f'[slide_gen] Playwright failed ({err}) — slide previews skipped.')

    if rebuild_needed or pptx_needs_rebuild:
        _package_output(
            lecture_id=lecture_id,
            pptx_path=pptx_path,
            screenshots=screenshots,
            lecture_json_path=json_path,
            lecture_title=title,
            speaker_information=speaker_information,
        )

    print(f"\n{'=' * 60}")
    print(f'End Phase 4: Deck HTML written to {html_path}')
    print(f"{'=' * 60}\n")

def _capture_screenshots(canvas_path: Path) -> list[bytes]:
    """Capture each slide as a static, full-res (1920×1080) PNG from the Fabric
    canvas editor ({id}_canvas.html).

    Uses FabricEditor.captureAllPNG(), which renders every slide in EDIT mode
    (no present-mode entrance animations / transitions) only AFTER the deck has
    finished building — so captures never catch mid-animation frames."""
    import base64
    from playwright.sync_api import sync_playwright
    screenshots: list[bytes] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            page.goto(canvas_path.as_uri())
            # Wait until the editor has FINISHED building every slide (json + thumb set
            # per slide during buildAllSlides) — i.e. slides are fully generated.
            page.wait_for_function(
                "() => window.FabricEditor && FabricEditor.getSlides && "
                "FabricEditor.getSlides().length > 0 && "
                "FabricEditor.getSlides().every(function(s){ return !!s.json && !!s.thumb; })",
                timeout=30000,
            )
            # Let webfonts settle so text renders in the correct face.
            try:
                page.evaluate("() => (document.fonts && document.fonts.ready) || true")
            except Exception:
                pass
            page.wait_for_timeout(300)
            # captureAllPNG() returns Promise<string[]> of PNG data-URLs; Playwright awaits it.
            data_urls = page.evaluate("() => window.FabricEditor.captureAllPNG()")
            for du in (data_urls or []):
                if not du or ',' not in du:
                    continue
                screenshots.append(base64.b64decode(du.split(',', 1)[1]))
            if not screenshots:
                raise RuntimeError('captureAllPNG returned no images')
        finally:
            browser.close()
    return screenshots


def _capture_screenshots_deck(html_path: Path) -> list[bytes]:
    """Fallback: capture slides from the DOM deck ({id}.html) via deck-stage.
    Used only when {id}_canvas.html is missing. deck-stage.js skips transitions
    under navigator.webdriver, so these captures are also animation-free."""
    from playwright.sync_api import sync_playwright
    screenshots: list[bytes] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            page.goto(html_path.as_uri())
            page.wait_for_function(
                "() => { const el = document.querySelector('deck-stage'); return el && el._total > 0; }",
                timeout=15000,
            )
            total: int = page.evaluate("() => document.querySelector('deck-stage')._total")
            if not total:
                raise RuntimeError('deck-stage reports 0 slides')
            for i in range(total):
                page.evaluate(f"() => document.querySelector('deck-stage')._show({i})")
                page.wait_for_timeout(80)
                screenshots.append(page.screenshot(full_page=False))
        finally:
            browser.close()
    return screenshots

def _package_output(lecture_id: str, pptx_path: Path, screenshots: list[bytes],
                    lecture_json_path: Path, lecture_title: str = '',
                    speaker_information: str = '') -> None:
    from src.utils.config import Config
    model_name = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    out_dir = _PROJECT_ROOT / 'output' / lecture_id / model_name
    images_dir = out_dir / 'slide_images'
    if images_dir.exists():
        shutil.rmtree(images_dir)
    images_dir.mkdir(parents=True, exist_ok=True)

    if pptx_path.exists():
        print(f'[package_output] PPTX at → {pptx_path}')
    else:
        print(f'[package_output] (PPTX not found: {pptx_path})')

    json_dst = out_dir / lecture_json_path.name
    with open(lecture_json_path, encoding='utf-8') as f:
        lecture_json = json.load(f)
    lecture_json.setdefault('metadata', {})
    if speaker_information:
        lecture_json['metadata']['speaker_information'] = speaker_information
    if lecture_title:
        lecture_json['metadata']['presentation_title'] = lecture_title
    with open(json_dst, 'w', encoding='utf-8') as f:
        json.dump(lecture_json, f, ensure_ascii=False, indent=2)
    print(f'[package_output] JSON copied → {json_dst}')

    for suffix in ('_table_distribution', '_image_distribution'):
        sibling = lecture_json_path.parent / f'{lecture_id}{suffix}.json'
        if sibling.exists():
            shutil.copy2(sibling, out_dir / sibling.name)
            print(f'[package_output] JSON copied → {out_dir / sibling.name}')
        else:
            print(f'[package_output] (skipped, not found) {sibling.name}')

    if screenshots:
        import io
        from PIL import Image
        for i, png_bytes in enumerate(screenshots):
            img = Image.open(io.BytesIO(png_bytes))
            # Canvas toDataURL('png') yields RGBA — JPEG can't hold alpha, so flatten
            # onto a white background (opaque slide pixels overwrite it anyway).
            if img.mode != 'RGB':
                rgba = img.convert('RGBA')
                bg = Image.new('RGB', rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[3])
                img = bg
            img_name = f'slide_{i + 1:04d}.jpg'
            img.save(str(images_dir / img_name), 'JPEG', quality=90)
        print(f'[package_output] {len(screenshots)} slide image(s) saved → {images_dir}')
    else:
        print(f'[package_output] No slide images (Playwright unavailable)')

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m src.generator.slide_gen',
        description='Lecture slide generation pipeline (deck HTML + PPTX).',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--lecture', required=True, metavar='PATH', help='Path to the lecture JSON file.')
    parser.add_argument('--title', default=None, metavar='STR', help="Override the lecture title.")
    parser.add_argument('--speaker', default='Slide Generation System', metavar='STR', help='Speaker / author shown on cover and end slides.')
    parser.add_argument('--log-level', default='INFO', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'], help='Logging verbosity (default: INFO).')
    return parser

if __name__ == '__main__':
    args = _build_parser().parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level), format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')
    run_pipeline(lecture_json_path=args.lecture, lecture_title=args.title, speaker_information=args.speaker)
