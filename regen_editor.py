"""Quick script to regenerate *_canvas.html from the current fabric_editor.js/css.
Usage: python regen_editor.py <path_to_canvas.html>
   or: python regen_editor.py  (defaults to 14-Math-1)
Also accepts legacy *_editor.html paths.
"""
import sys, re, json
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from src.generator.fabric_editor_builder import build_fabric_html, build_slide_spec_json

def regen(html_path: Path):
    src = html_path.read_text(encoding="utf-8")

    # Extract DECK_SPEC JSON from the HTML. Prefer the explicit markers emitted by
    # fabric_editor_builder.py; fall back to the legacy pattern for older files.
    m = re.search(
        r'/\*__DECK_SPEC_START__\*/\s*var DECK_SPEC\s*=\s*(\{.*?\});\s*/\*__DECK_SPEC_END__\*/',
        src, re.DOTALL,
    )
    if not m:
        m = re.search(r'var DECK_SPEC\s*=\s*(\{.*?\});\s*</script>', src, re.DOTALL)
    if not m:
        print("ERROR: could not find DECK_SPEC in", html_path)
        return

    deck_spec_json = m.group(1)
    try:
        spec = json.loads(deck_spec_json)
    except Exception as e:
        print("ERROR parsing DECK_SPEC JSON:", e)
        return

    # Remove _canvasJsons / _slideTables — this is a fresh rebuild; user edits are lost (intentional)
    spec.pop("_canvasJsons", None)
    spec.pop("_slideTables", None)

    page_title = spec.get("meta", {}).get("title", html_path.stem)
    slides_json = json.dumps(spec, ensure_ascii=False, indent=2)

    new_html = build_fabric_html(slides_json, page_title=page_title)
    html_path.write_text(new_html, encoding="utf-8")
    print(f"Regenerated: {html_path}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = ROOT / "output/14-Math-1/gpt-4.1-mini/14-Math-1.html"
    regen(target)
