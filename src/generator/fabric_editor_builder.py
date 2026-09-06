from __future__ import annotations

import json as _json
from pathlib import Path
from typing import List

_HERE = Path(__file__).parent

_FABRIC_CSS   = (_HERE / "fabric_editor.css").read_text(encoding="utf-8") if (_HERE / "fabric_editor.css").exists() else ""
_FABRIC_JS    = (_HERE / "fabric_editor.js").read_text(encoding="utf-8")  if (_HERE / "fabric_editor.js").exists()  else ""
_FABRIC_MIN_JS = (_HERE / "fabric.min.js").read_text(encoding="utf-8")    if (_HERE / "fabric.min.js").exists()     else ""
# Shared save layer (File System Access API + download fallback + autosave).
# Must load BEFORE fabric_editor.js, which uses window.EditorSave.
_EDITOR_SAVE_JS = (_HERE / "editor_save.js").read_text(encoding="utf-8")  if (_HERE / "editor_save.js").exists()  else ""

_FONTS_LINK = (
    "https://fonts.googleapis.com/css2?family=Archivo+Black"
    "&family=Archivo:wght@400;500;600;700;800"
    "&family=Montserrat:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500;1,600;1,700"
    "&family=Open+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&family=Poppins:ital,wght@0,400;0,500;0,600;0,700;1,400;1,600"
    "&family=Raleway:ital,wght@0,400;0,500;0,600;0,700;1,400;1,600"
    "&family=Nunito:ital,wght@0,400;0,600;0,700;1,400"
    "&family=DM+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400"
    "&family=Josefin+Sans:ital,wght@0,300;0,400;0,600;0,700;1,400"
    "&family=Oswald:wght@400;500;600;700"
    "&family=Merriweather:ital,wght@0,400;0,700;1,400"
    "&family=Bebas+Neue"
    "&family=Source+Code+Pro:wght@400;500"
    "&display=swap"
)

_TEXTBASELINE_FIX = """\
<script>
(function () {
  var desc = Object.getOwnPropertyDescriptor(CanvasRenderingContext2D.prototype, 'textBaseline');
  if (desc && desc.set) {
    Object.defineProperty(CanvasRenderingContext2D.prototype, 'textBaseline', {
      set: function (v) { desc.set.call(this, v === 'alphabetical' ? 'alphabetic' : v); },
      get: desc.get, configurable: true,
    });
  }
}());
</script>"""


def build_slide_spec_json(
    slides: List[dict],
    title: str = "",
    author: str = "",
    theme: str = "frankfurt",
) -> str:
    """Return a JSON string describing the slide deck for FabricEditor.loadFromSlideSpec()."""
    spec = {
        "meta": {"title": title, "author": author, "theme": theme},
        "slides": slides,
    }
    return _json.dumps(spec, ensure_ascii=False, indent=2)


def build_fabric_html(slides_json: str, page_title: str = "Presentation") -> str:
    """Generate a self-contained HTML page that boots the Fabric.js canvas editor."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{_esc(page_title)}</title>
<meta name="viewport" content="width=device-width, initial-scale=1" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="{_FONTS_LINK}" rel="stylesheet" />
<style>
html, body {{ margin: 0; padding: 0; background: #12121f; height: 100%; }}
#ed-loading {{
  position: fixed; inset: 0;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  color: rgba(255,255,255,.5);
  font-family: system-ui, sans-serif; font-size: 16px; gap: 16px;
}}
#ed-loading .spinner {{
  width: 36px; height: 36px;
  border: 3px solid rgba(255,255,255,.1);
  border-top-color: #4f8ef7;
  border-radius: 50%;
  animation: spin .8s linear infinite;
}}
@keyframes spin {{ to {{ transform: rotate(360deg); }} }}
#ed-error {{
  display: none; position: fixed; inset: 0;
  align-items: center; justify-content: center;
  flex-direction: column; gap: 12px;
  color: #ff6b6b; font-family: monospace; font-size: 14px;
  background: #1e1e2e; padding: 32px; box-sizing: border-box; text-align: center;
}}
{_FABRIC_CSS}
</style>
</head>
<body>
<div id="ed-loading">
  <div class="spinner"></div>
  <span>Loading editor…</span>
</div>
<div id="ed-error">
  <h2>&#10060; Editor failed to load</h2>
  <pre id="ed-error-detail">Check DevTools → Console for details.</pre>
</div>
{_TEXTBASELINE_FIX}
<script>
{_FABRIC_MIN_JS}
</script>
<script>
/*__DECK_SPEC_START__*/
var DECK_SPEC = {slides_json};
/*__DECK_SPEC_END__*/
</script>
<script>
{_EDITOR_SAVE_JS}
</script>
<script>
{_FABRIC_JS}
</script>
<script>
window.addEventListener('load', function () {{
  var loader = document.getElementById('ed-loading');
  if (loader) loader.style.display = 'none';
  if (window.FabricEditor && typeof DECK_SPEC !== 'undefined') {{
    FabricEditor.loadFromSlideSpec(DECK_SPEC);
  }}
}});
</script>
</body>
</html>"""


def _esc(s: str) -> str:
    import html as _html
    return _html.escape(str(s))
