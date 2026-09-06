from __future__ import annotations

import html as _html
import random
import re
from pathlib import Path
from typing import List, Optional

                                                                              
_CSS_FILE = Path(__file__).parent / "_deck_css.css"
_EMBEDDED_CSS: str = _CSS_FILE.read_text(encoding="utf-8") if _CSS_FILE.exists() else ""

# ─────────────────────────────────────────────────────────────────────────────
# IN-BROWSER SLIDE EDITOR — injected into every generated HTML
# ─────────────────────────────────────────────────────────────────────────────
_EDITOR_CSS_FILE = Path(__file__).parent / "deck_editor.css"
_EDITOR_CSS = _EDITOR_CSS_FILE.read_text(encoding="utf-8") if _EDITOR_CSS_FILE.exists() else ""

_EDITOR_JS_FILE = Path(__file__).parent / "deck_editor.js"
_EDITOR_JS = _EDITOR_JS_FILE.read_text(encoding="utf-8") if _EDITOR_JS_FILE.exists() else ""

# Shared save layer (File System Access API + download fallback + autosave).
# Must load BEFORE deck_editor.js, which uses window.EditorSave.
_EDITOR_SAVE_FILE = Path(__file__).parent / "editor_save.js"
_EDITOR_SAVE_JS = _EDITOR_SAVE_FILE.read_text(encoding="utf-8") if _EDITOR_SAVE_FILE.exists() else ""


_FONTS_LINK = (
    "https://fonts.googleapis.com/css2?family=Archivo+Black"
    "&family=Archivo:wght@400;500;600;700;800"
    "&family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600"
    "&family=Crimson+Pro:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600"
    "&family=DM+Sans:wght@400;500;600;700"
    "&family=IBM+Plex+Mono:wght@400;500"
    "&family=IBM+Plex+Sans:wght@400;500;600;700"
    "&family=JetBrains+Mono:wght@400;500"
    "&family=Lora:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600"
    "&family=Montserrat:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500;1,600;1,700"
    "&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;0,6..72,700"
    ";1,6..72,400;1,6..72,500;1,6..72,600"
    "&family=Open+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600"
    "&family=Playfair+Display:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,500;1,600;1,700"
    "&family=Source+Sans+3:wght@400;500;600;700"
    "&family=Space+Grotesk:wght@400;500;600;700"
    "&family=Space+Mono:wght@400;700"
    "&family=Spectral:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500;1,600"
    "&family=Work+Sans:wght@400;500;600;700"
    "&display=swap"
)

                                                                              
_THEMES: dict[str, dict] = {
    "frankfurt": {
        "cover_bg": "linear-gradient(135deg, #0f172a 0%, #172554 46%, #1d4ed8 100%)",
        "end_bg": "radial-gradient(circle at top, #1e3a8a 0%, #0f172a 72%)",
        "accent": "#e2b96f",
        "accent_light": "#b45309",
        "text": "#f8fafc",
        "dim": "rgba(248, 250, 252, 0.55)",
        "cards": ["#5b9bd5", "#e07b6a", "#7b68c8", "#f0a050"],
        "panel": "dark",
        "font": {
            "display": "'Montserrat', system-ui, sans-serif",
            "body": "'Open Sans', system-ui, sans-serif",
            "mono": "'IBM Plex Mono', monospace",
        },
    },
    "umn": {
        "cover_bg": "linear-gradient(135deg, #7a0019 0%, #500014 55%, #330009 100%)",
        "end_bg": "radial-gradient(circle at top, #7a0019 0%, #330009 72%)",
        "accent": "#ffcc33",
        "accent_light": "#7a0019",
        "text": "#ffffff",
        "dim": "rgba(255, 255, 255, 0.55)",
        "cards": ["#900021", "#2c6fad", "#2a7a6f", "#b8860b"],
        "panel": "dark",
        "font": {
            "display": "'Playfair Display', Georgia, serif",
            "body": "'Source Sans 3', system-ui, sans-serif",
            "mono": "'IBM Plex Mono', monospace",
        },
    },
    "seriph": {
        "cover_bg": "linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%)",
        "end_bg": "radial-gradient(circle at top, #16213e 0%, #0a0a1a 72%)",
        "accent": "#e94560",
        "accent_light": "#be123c",
        "text": "#f8fafc",
        "dim": "rgba(248, 250, 252, 0.55)",
        "cards": ["#c0415e", "#4a6fa5", "#7a5c8a", "#5a8a5a"],
        "panel": "light",
        "font": {
            "display": "'Cormorant Garamond', Georgia, serif",
            "body": "'Work Sans', system-ui, sans-serif",
            "mono": "'JetBrains Mono', monospace",
        },
    },
    "scholarly": {
        "cover_bg": "linear-gradient(135deg, #1e2a3a 0%, #2d3e50 50%, #3a5068 100%)",
        "end_bg": "radial-gradient(circle at top, #2d3e50 0%, #0f1a25 72%)",
        "accent": "#f0a500",
        "accent_light": "#92400e",
        "text": "#f0f0f0",
        "dim": "rgba(240, 240, 240, 0.55)",
        "cards": ["#3a7abf", "#bf5a3a", "#5a7a3a", "#7a5abf"],
        "panel": "dark",
        "font": {
            "display": "'Lora', Georgia, serif",
            "body": "'IBM Plex Sans', system-ui, sans-serif",
            "mono": "'IBM Plex Mono', monospace",
        },
    },
    "improving-25": {
        "cover_bg": "linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)",
        "end_bg": "radial-gradient(circle at top, #302b63 0%, #0f0c29 72%)",
        "accent": "#a855f7",
        "accent_light": "#6d28d9",
        "text": "#f8fafc",
        "dim": "rgba(248, 250, 252, 0.55)",
        "cards": ["#9333ea", "#06b6d4", "#f59e0b", "#10b981"],
        "panel": "light",
        "font": {
            "display": "'Crimson Pro', Georgia, serif",
            "body": "'Space Grotesk', system-ui, sans-serif",
            "mono": "'Space Mono', monospace",
        },
    },
    "meetup": {
        "cover_bg": "linear-gradient(135deg, #1a5276 0%, #1b4f72 50%, #1a252f 100%)",
        "end_bg": "radial-gradient(circle at top, #1a5276 0%, #1a252f 72%)",
        "accent": "#5dade2",
        "accent_light": "#1e6ba8",
        "text": "#f8fafc",
        "dim": "rgba(248, 250, 252, 0.55)",
        "cards": ["#2980b9", "#e57373", "#5a9a5a", "#f0a050"],
        "panel": "light",
        "font": {
            "display": "'Newsreader', Georgia, serif",
            "body": "'DM Sans', system-ui, sans-serif",
            "mono": "'JetBrains Mono', monospace",
        },
    },
    "bricks": {
        "cover_bg": "linear-gradient(135deg, #c0392b 0%, #a93226 50%, #922b21 100%)",
        "end_bg": "radial-gradient(circle at top, #c0392b 0%, #6e2016 72%)",
        "accent": "#f1c40f",
        "accent_light": "#c0392b",
        "text": "#ffffff",
        "dim": "rgba(255, 255, 255, 0.6)",
        "cards": ["#c0392b", "#d4891a", "#8a4a2f", "#6a7a8a"],
        "panel": "dark",
        "font": {
            "display": "'Spectral', Georgia, serif",
            "body": "'Archivo', system-ui, sans-serif",
            "mono": "'IBM Plex Mono', monospace",
        },
    },
    "ivory": {
        "cover_bg": "linear-gradient(135deg, #2c2c3e 0%, #1a1a2e 100%)",
        "end_bg": "radial-gradient(circle at top, #2c2c3e 0%, #0f0f1e 72%)",
        "accent": "#c9a96e",
        "accent_light": "#7a4f2e",
        "light_bg": "#f5f3ee",
        "text": "#f8fafc",
        "dim": "rgba(248, 250, 252, 0.55)",
        "cards": ["#7a4f2e", "#4a6fa5", "#5a8a5a", "#7a5c8a"],
        "panel": "dark",
        "light": True,
        "font": {
            "display": "'Cormorant Garamond', Georgia, serif",
            "body": "'Work Sans', system-ui, sans-serif",
            "mono": "'IBM Plex Mono', ui-monospace, monospace",
        },
    },
    "sage": {
        "cover_bg": "linear-gradient(135deg, #1b3a2d 0%, #0f2318 50%, #071a0f 100%)",
        "end_bg": "radial-gradient(circle at top, #1b3a2d 0%, #071a0f 72%)",
        "accent": "#7ecba1",
        "accent_light": "#1d6b45",
        "light_bg": "#eef4ef",
        "text": "#f0faf3",
        "dim": "rgba(240, 250, 243, 0.55)",
        "cards": ["#1d6b45", "#2e6da4", "#7a5c8a", "#8b6914"],
        "panel": "dark",
        "light": True,
        "font": {
            "display": "'Lora', Georgia, serif",
            "body": "'Source Sans 3', system-ui, sans-serif",
            "mono": "'JetBrains Mono', ui-monospace, monospace",
        },
    },
}
_THEME_DEFAULT = _THEMES["frankfurt"]

def _e(text: str) -> str:
    # Strip stray HTML tags (e.g. <br>) that sometimes appear in LLM-generated data fields
    cleaned = re.sub(r'<[^>]+>', ' ', str(text or "")).strip()
    return _html.escape(cleaned, quote=False)

def _li(items: List[str]) -> str:
    return "".join(f"<li>{_e(item)}</li>" for item in items)

class DeckHTMLLayoutManager:

    def __init__(
        self,
        theme: str = "frankfurt",
        title: str = "Main Title",
        author: str = "Slide Generation System",
        font_sans: Optional[str] = None,
        font_serif: Optional[str] = None,
        font_mono: Optional[str] = None,
        heading_color_code: Optional[str] = None,
    ):
        self.theme = theme
        self.title = title
        self.author = author
        t = _THEMES.get(theme, _THEME_DEFAULT)
        self._accent = heading_color_code or t["accent"]
        self._t = t
        self._light: bool = bool(t.get("light", False))



    def _theme_vars(self) -> str:
        t = self._t
        c = t["cards"]
        panel_text = "#0b0d12" if t["panel"] == "dark" else "#ffffff"
        accent_light = t.get("accent_light", "#b45309")
        light_bg = t.get("light_bg", "#f5f3ee")
        return (
            f"--cover-bg:{t['cover_bg']};"
            f"--end-bg:{t['end_bg']};"
            f"--accent:{t['accent']};"
            f"--accent-light:{accent_light};"
            f"--light-bg:{light_bg};"
            f"--text:{t['text']};"
            f"--dim:{t['dim']};"
            f"--c1:{c[0]};--c2:{c[1]};--c3:{c[2]};--c4:{c[3]};"
            f"--panel-text:{panel_text};"
            f"--font-display:{t['font']['display']};"
            f"--font-body:{t['font']['body']};"
            f"--font-mono:{t['font']['mono']};"
        )

    @staticmethod
    def _chrome(short_title: str, page_num: int = 0, date: str = "", presenter: str = "", institution: str = "") -> str:
        label = _e(short_title)
        pn = f"{page_num:02d}" if page_num > 0 else ""
        left_parts = []
        if date:
            left_parts.append(f'<b>{_e(date)}</b>')
        if presenter:
            left_parts.append(f'<b>Speaker: {_e(presenter)}</b>')
        if institution:
            left_parts.append(f'<b>{_e(institution)}</b>')
        left_html = ' &middot; '.join(left_parts)
        pn_html = f'<b>{pn}</b>' if pn else ''
        return (
            f'<div class="chrome-top"><span><span class="dot"></span>{label}</span></div>'
            f'<div class="chrome-bot"><span>{left_html}</span><span class="pn">{pn_html}</span></div>'
        )

    @staticmethod
    def _img_tag(src: str, alt: str = "") -> str:
        return (
            f'<img src="{_e(src)}" alt="{_e(alt)}" '
            'style="position:absolute;inset:0;width:100%;height:100%;object-fit:contain;">'
        )

    @staticmethod
    def _bullet_style(items: List[str]) -> str:
        """Compute inline font-size and gap so bullets fill the content panel."""
        n = len(items)
        if n == 0:
            return "font-size:30px;gap:24px"
        # Available height inside ul for list items (conservative estimate):
        # slide 1080 - body-wrap padding (180+140) - h2 ~100px - gap 40px - ul padding 72px
        avail_h = 500
        # Text width available per line:
        # 1920 - body-wrap lr padding (240) - ul lr padding (96) - li indent (44) = 1540px
        text_w = 1540
        char_w_ratio = 0.55  # avg char width as fraction of font size
        lh_ratio = 1.5       # line-height
        gap_ratio = 0.65     # gap between items as fraction of font size

        lo, hi, best = 18, 52, 18
        while lo <= hi:
            mid = (lo + hi) // 2
            chars_per_line = max(1, int(text_w / (mid * char_w_ratio)))
            line_h = mid * lh_ratio
            gap_px = int(mid * gap_ratio)
            total = 0.0
            for s in items:
                n_lines = max(1, -(-len(s) // chars_per_line))  # ceiling div
                total += n_lines * line_h
            total += (n - 1) * gap_px
            if total <= avail_h:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1

        gap = max(10, int(best * gap_ratio))
        return f"font-size:{best}px;gap:{gap}px"

    @staticmethod
    def _ul(items: List[str]) -> str:
        return "<ul>" + _li(items) + "</ul>"

    @staticmethod
    def _ul_fit(items: List[str]) -> str:
        style = DeckHTMLLayoutManager._bullet_style(items)
        return f'<ul style="{style}">' + _li(items) + "</ul>"

                                                                               
    def _slide_sep(self) -> str:
        return ""

                                                                               
    def config_and_greeting_slide(self, short_title: str = "", institution: str = "") -> str:
        st = _e(short_title or self.title)
        author = _e(self.author)
        inst_html = f'<div class="inst"><b>{_e(institution)}</b></div>' if institution else ''
        return (
            '<section class="slide cover">'
            '<div class="body-wrap">'
            '<div class="eyebrow"></div>'
            f'<h1 data-fit data-fit-lines="2" data-fit-min="60" data-fit-max="100">{st}</h1>'
            '<div class="meta-row">'
            f'<div class="by">Presented by <b>{author}</b></div>'
            f'{inst_html}'
            "</div>"
            "</div>"
            "</section>"
        )

                                                                                
    def toc_layout(
        self,
        toc_content: List[str],
        heading: str = "Table of Contents",
    ) -> str:
        light_cls = " light" if self._light else ""
        rows = ""
        for item in toc_content:
            m = re.match(r"^(\d+)\.\s*(.*)", item)
            if m:
                num = m.group(1).zfill(2)
                text = _e(m.group(2))
            else:
                num = ""
                text = _e(item)
            rows += f'<div class="toc-row"><div class="n">{num}</div><div class="t">{text}</div></div>'
        return (
            f'<section class="slide body toc{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="56" data-fit-max="120">{_e(heading)}.</h2>'
            f'<div class="toc-grid" data-fit-block>{rows}</div>'
            "</div>"
            "</section>"
        )

                                                                                
    def end_layout(self, end_text: str = "Thank you", institution: str = "", acknowledgment: str = "") -> str:
        author = _e(self.author)
        inst_html = f'<div class="item"><div class="k">Institution</div><div class="v">{_e(institution)}</div></div>' if institution else ''
        ack_html = f'<div class="ack">{_e(acknowledgment)}</div>' if acknowledgment else ''
        return (
            '<section class="slide end">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="1" data-fit-min="120" data-fit-max="320">{_e(end_text)}</h2>'
            f'{ack_html}'
            '<div class="end-foot">'
            f'<div class="item"><div class="k">Presented by</div><div class="v">{author}</div></div>'
            f'{inst_html}'
            '<div class="item"><div class="k">Q&amp;A Session</div><div class="v"><em>Open discussion</em></div></div>'
            "</div>"
            "</div>"
            "</section>"
        )

                                                                                
    def only_content(
        self,
        title: str,
        content: List[str],
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else [str(content)]
        return (
            f'<section class="slide body bullets{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f"{self._ul_fit(items)}"
            "</div>"
            "</section>"
        )

                                                                                
    def comparison_layout(
        self,
        title: str,
        table_markdown: str,
        caption: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        table_html = _md_table_to_html(table_markdown)
        cap_html = f'<div class="caption">{_e(caption)}</div>' if caption else ""
        return (
            f'<section class="slide body cmptable{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="tbl-wrap">{table_html}{cap_html}</div>'
            "</div>"
            "</section>"
        )

    def table_above_layout(
        self,
        title: str,
        table_markdown: str,
        content: List[str],
    ) -> str:
        light_cls = " light" if self._light else ""
        table_html = _md_table_to_html(table_markdown)
        items = content if isinstance(content, list) else []
        return (
            f'<section class="slide body tblabove{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="tbl-slot">{table_html}</div>'
            f"{self._ul(items) if items else ''}"
            "</div>"
            "</section>"
        )

                                                                                
    def two_contents_in_a_slide_layout(
        self,
        title: str,
        sub_title_1: str,
        sub_title_2: str,
        sub_content_1,
        sub_content_2,
    ) -> str:
        light_cls = " light" if self._light else ""

        def _block(st: str, items) -> str:
            lst = items if isinstance(items, list) else [str(items)]
            return (
                f'<div class="block">'
                f"<h3>{_e(st)}</h3>"
                f"{self._ul(lst)}"
                "</div>"
            )

        return (
            f'<section class="slide body twocontents{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="pair">{_block(sub_title_1, sub_content_1)}{_block(sub_title_2, sub_content_2)}</div>'
            "</div>"
            "</section>"
        )

                                                                                
    def formula_top_layout(
        self,
        title: str,
        latex_formula_block: str,
        content: List[str],
    ) -> str:
        return self._formula_slide(title, latex_formula_block, content)

    def formula_below_layout(
        self,
        title: str,
        latex_formula_block: str,
        content: List[str],
    ) -> str:
        return self._formula_slide(title, latex_formula_block, content)

    def _formula_slide(
        self,
        title: str,
        latex: str,
        content: List[str],
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        src = (latex or '').strip()
                                                                                    
                                                   
        existing = re.findall(r'\\\[(.*?)\\\]', src, re.DOTALL)
        if existing:
            all_eq: list = []
            for block in existing:
                block = block.strip()
                if not block:
                    continue
                                                                                           
                                                                                              
                if re.match(r'\\begin\{(?:aligned?|gather|eqnarray)', block):
                    inner = re.sub(r'^\\begin\{[^}]+\}\s*', '', block)
                    inner = re.sub(r'\s*\\end\{[^}]+\}$', '', inner)
                    for row in re.split(r'\\\\', inner):
                        row = row.replace('&', '').strip()
                        if row:
                            all_eq.append(f'\\[{_e(row)}\\]')
                else:
                    all_eq.append(f'\\[{_e(block)}\\]')
            eq_blocks = ''.join(all_eq) if all_eq else ''.join(f'\\[{_e(b.strip())}\\]' for b in existing if b.strip())
        else:
            dollar_blocks = re.findall(r'\$\$(.*?)\$\$', src, re.DOTALL)
            if dollar_blocks:
                eq_blocks = ''.join(f'\\[{_e(b.strip())}\\]' for b in dollar_blocks if b.strip())
            else:
                parts = re.split(r'\n\s*\n', src)
                eq_blocks = ''.join(f'\\[{_e(p.strip())}\\]' for p in parts if p.strip())
        eq_count = eq_blocks.count('\\[')
        return (
            f'<section class="slide body formula{light_cls}" data-eq-count="{eq_count}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            # data-eq-count above sets a reasonable starting font-size via CSS tiers, but that's
            # a static guess that doesn't know about tall content (e.g. \frac stacks) — data-fit-block
            # is a dynamic safety net that shrinks the whole block via measured bounding boxes if the
            # actual KaTeX render still overflows its container, so long/tall formulas can no longer
            # spill past the slide edge.
            f'<div class="eq-wrap" data-fit-block><div class="eq">{eq_blocks}</div></div>'
            f"{self._ul(items) if items else ''}"
            "</div>"
            "</section>"
        )

                                                                                
    def image_left_layout(
        self,
        title: str,
        content: List[str],
        img_path: str,
        image_width: str = "40%",
        caption: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        cap_html = (f'<figcaption data-fit data-fit-scope="figure" data-fit-lines="2" data-fit-min="14" data-fit-max="22">{_e(caption)}</figcaption>') if caption else ""
        img = self._img_tag(img_path, caption or title)
        return (
            f'<section class="slide body imgleft{light_cls}">'
            '<div class="body-wrap">'
            f'<figure><div class="img-slot">{img}</div>{cap_html}</figure>'
            '<div class="rhs" data-fit-block>'
            f'<h2 data-fit data-fit-lines="3" data-fit-min="24" data-fit-max="120">{_e(title)}</h2>'
            f"{self._ul(items)}"
            "</div>"
            "</div>"
            "</section>"
        )


    def image_right_layout(
        self,
        title: str,
        content: List[str],
        img_path: str,
        image_width: str = "40%",
        caption: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        cap_html = (f'<figcaption data-fit data-fit-scope="figure" data-fit-lines="2" data-fit-min="14" data-fit-max="22">{_e(caption)}</figcaption>') if caption else ""
        img = self._img_tag(img_path, caption or title)
        return (
            f'<section class="slide body imgright{light_cls}">'
            '<div class="body-wrap">'
            '<div class="lhs" data-fit-block>'
            f'<h2 data-fit data-fit-lines="3" data-fit-min="24" data-fit-max="120">{_e(title)}</h2>'
            f"{self._ul(items)}"
            "</div>"
            f'<figure><div class="img-slot">{img}</div>{cap_html}</figure>'
            "</div>"
            "</section>"
        )

                                                                                
    def image_above_layout(
        self,
        title: str,
        content: List[str],
        img_path: str,
        image_width: str = "90%",
        caption: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        cap_html = (f'<figcaption data-fit data-fit-scope="figure" data-fit-lines="2" data-fit-min="14" data-fit-max="22">{_e(caption)}</figcaption>') if caption else ""
        img = self._img_tag(img_path, caption or title)
        return (
            f'<section class="slide body imgabove{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<figure><div class="img-slot">{img}</div>{cap_html}</figure>'
            f"{self._ul(items) if items else ''}"
            "</div>"
            "</section>"
        )

                                                                                
    def image_below_layout(
        self,
        title: str,
        content: List[str],
        img_path: str,
        image_width: str = "90%",
        caption: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        cap_html = (f'<figcaption data-fit data-fit-scope="figure" data-fit-lines="2" data-fit-min="14" data-fit-max="22">{_e(caption)}</figcaption>') if caption else ""
        img = self._img_tag(img_path, caption or title)
        return (
            f'<section class="slide body imgbelow{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f"{self._ul(items) if items else ''}"
            f'<figure><div class="img-slot">{img}</div>{cap_html}</figure>'
            "</div>"
            "</section>"
        )

                                                                               
    def two_image_right_layout(
        self,
        title: str,
        content: List[str],
        img1_path: str,
        img2_path: str,
        image_width: str = "30%",
        caption1: Optional[str] = None,
        caption2: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        img1 = self._img_tag(img1_path, caption1 or title)
        img2 = self._img_tag(img2_path, caption2 or title)
        cap1 = f"<figcaption>{_e(caption1)}</figcaption>" if caption1 else ""
        cap2 = f"<figcaption>{_e(caption2)}</figcaption>" if caption2 else ""
        return (
            f'<section class="slide body twoimgright{light_cls}">'
            '<div class="body-wrap">'
            '<div class="lhs">'
            f'<h2 data-fit data-fit-lines="3" data-fit-min="24" data-fit-max="120">{_e(title)}</h2>'
            f"{self._ul(items)}"
            "</div>"
            '<div class="imgs">'
            f"<figure><div class=\"img-slot\">{img1}</div>{cap1}</figure>"
            f"<figure><div class=\"img-slot\">{img2}</div>{cap2}</figure>"
            "</div>"
            "</div>"
            "</section>"
        )

                                                                               
    def two_image_left_layout(
        self,
        title: str,
        content: List[str],
        img1_path: str,
        img2_path: str,
        image_width: str = "30%",
        caption1: Optional[str] = None,
        caption2: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        img1 = self._img_tag(img1_path, caption1 or title)
        img2 = self._img_tag(img2_path, caption2 or title)
        cap1 = f"<figcaption>{_e(caption1)}</figcaption>" if caption1 else ""
        cap2 = f"<figcaption>{_e(caption2)}</figcaption>" if caption2 else ""
        return (
            f'<section class="slide body twoimgleft{light_cls}">'
            '<div class="body-wrap">'
            '<div class="imgs">'
            f"<figure><div class=\"img-slot\">{img1}</div>{cap1}</figure>"
            f"<figure><div class=\"img-slot\">{img2}</div>{cap2}</figure>"
            "</div>"
            '<div class="rhs">'
            f'<h2 data-fit data-fit-lines="3" data-fit-min="24" data-fit-max="120">{_e(title)}</h2>'
            f"{self._ul(items)}"
            "</div>"
            "</div>"
            "</section>"
        )

                                                                               
    def two_image_above_layout(
        self,
        title: str,
        content: List[str],
        img1_path: str,
        img2_path: str,
        caption1: Optional[str] = None,
        caption2: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        img1 = self._img_tag(img1_path, caption1 or title)
        img2 = self._img_tag(img2_path, caption2 or title)
        cap1 = f"<figcaption>{_e(caption1)}</figcaption>" if caption1 else ""
        cap2 = f"<figcaption>{_e(caption2)}</figcaption>" if caption2 else ""
        return (
            f'<section class="slide body twoimgabove{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            '<div class="imgs">'
            f"<figure><div class=\"img-slot\">{img1}</div>{cap1}</figure>"
            f"<figure><div class=\"img-slot\">{img2}</div>{cap2}</figure>"
            "</div>"
            f"{self._ul(items) if items else ''}"
            "</div>"
            "</section>"
        )

                                                                               
    def two_image_below_layout(
        self,
        title: str,
        content: List[str],
        img1_path: str,
        img2_path: str,
        caption1: Optional[str] = None,
        caption2: Optional[str] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = content if isinstance(content, list) else []
        img1 = self._img_tag(img1_path, caption1 or title)
        img2 = self._img_tag(img2_path, caption2 or title)
        cap1 = f"<figcaption>{_e(caption1)}</figcaption>" if caption1 else ""
        cap2 = f"<figcaption>{_e(caption2)}</figcaption>" if caption2 else ""
        return (
            f'<section class="slide body twoimgbelow{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f"{self._ul(items) if items else ''}"
            '<div class="imgs">'
            f"<figure><div class=\"img-slot\">{img1}</div>{cap1}</figure>"
            f"<figure><div class=\"img-slot\">{img2}</div>{cap2}</figure>"
            "</div>"
            "</div>"
            "</section>"
        )

                                                                                
    def two_cols_content_layout(
        self,
        title: str,
        content: List[str],
    ) -> str:
        light_cls = " light" if self._light else ""
        mid = (len(content) + 1) // 2
        left, right = content[:mid], content[mid:]
        return (
            f'<section class="slide body twocols{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="grid">{self._ul(left)}{self._ul(right)}</div>'
            "</div>"
            "</section>"
        )

                                                                               
    def steps_horizontal_layout(
        self,
        title: str,
        steps: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        steps_html = ""
        for step in steps[:5]:
            st = _e(step.get("title", ""))
            bd = _e(step.get("body", ""))
            steps_html += (
                f'<div class="step">'
                f'<div class="num">{steps.index(step) + 1}</div>'
                f"<h3>{st}</h3>"
                f"<p>{bd}</p>"
                "</div>"
            )
        return (
            f'<section class="slide body steps{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="track">{steps_html}</div>'
            "</div>"
            "</section>"
        )

                                                                               
    def key_points_layout(
        self,
        title: str,
        points: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        pts_html = ""
        for i, pt in enumerate(points[:6]):
            ix = f"P·{i+1:02d}"
            ttl = _e(pt.get("title", ""))
            body = _e(pt.get("body", ""))
            pts_html += (
                f'<div class="pt">'
                f'<div class="ix">{ix}</div>'
                "<div>"
                f'<h3 class="ttl">{ttl}</h3>'
                f'<p class="body">{body}</p>'
                "</div>"
                "</div>"
            )
        head = f'<div class="head"><h2 data-fit data-fit-lines="3" data-fit-min="24" data-fit-max="112">{_e(title)}</h2></div>'
        return (
            f'<section class="slide body keypoints{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f"{head}"
            f'<div class="list">{pts_html}</div>'
            "</div>"
            "</section>"
        )

                                                                                
    def three_cols_content_layout(
        self,
        title: str,
        cols: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        cols_html = ""
        for i, col in enumerate(cols[:3]):
            tag = _e(col.get("icon", f"#{i+1:02d}"))
            h3 = _e(col.get("title", ""))
            p = _e(col.get("body", ""))
            bullets = col.get("bullets", [])
            bl = f"{self._ul(bullets)}" if bullets else ""
            cols_html += (
                f'<div class="col">'
                f'<div class="tag">{tag}</div>'
                f"<h3>{h3}</h3>"
                f"<p>{p}</p>"
                f"{bl}"
                "</div>"
            )
        return (
            f'<section class="slide body threecol{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="grid">{cols_html}</div>'
            "</div>"
            "</section>"
        )

                                                                               
    def split_contrast_layout(
        self,
        left_title: str,
        left_items: List[str],
        right_title: str,
        right_items: List[str],
    ) -> str:
        def _side(tag: str, h3: str, items: List[str], cls: str) -> str:
            return (
                f'<div class="side {cls}">'
                f'<div class="tag">{_e(tag)}</div>'
                f'<h3 data-fit data-fit-lines="2" data-fit-min="28" data-fit-max="80">{_e(h3)}</h3>'
                f"{self._ul(items)}"
                "</div>"
            )

        return (
            '<section class="slide splitcontrast">'
            '<div class="pair">'
            f'{_side("Before", left_title, left_items, "before")}'
            f'{_side("After", right_title, right_items, "after")}'
            "</div>"
            "</section>"
        )

                                                                               
    def conclusion_cards_layout(
        self,
        title: str,
        conclusions: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        colors = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)"]
        dots_html = ""
        cards_html = ""
        for i, conc in enumerate(conclusions[:4]):
            c = colors[i % len(colors)]
            dots_html += f'<div><div class="dot" style="--card-color:{c};"></div></div>'
            h3 = _e(conc.get("heading", ""))
            p = _e(conc.get("body", ""))
            cards_html += (
                f'<div class="card" style="background:{c};">'
                f'<div class="card-content" data-fit-block style="display:flex;flex-direction:column;gap:10px;height:100%;">'
                f'<div class="num">{i+1:02d}</div>'
                f'<h3 data-fit data-fit-lines="3" data-fit-min="18" data-fit-max="40">{h3}</h3>'
                f"<p>{p}</p>"
                "</div>"
                "</div>"
            )
        return (
            f'<section class="slide body conclcards{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            '<div class="timeline">'
            '<div class="timeline-line"></div>'
            f'<div class="timeline-dots">{dots_html}</div>'
            "</div>"
            f'<div class="cards">{cards_html}</div>'
            "</div>"
            "</section>"
        )

                                                                               
    def numbered_conclusions_layout(
        self,
        title: str,
        conclusions: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        rows_html = ""
        for i, conc in enumerate(conclusions):
            h = _e(conc.get("heading", ""))
            b = _e(conc.get("body", ""))
            rows_html += (
                f'<div class="row">'
                f'<div class="n">{i+1:02d}</div>'
                "<div>"
                f'<h3 class="t">{h}</h3>'
                f'<p class="b">{b}</p>'
                "</div>"
                "</div>"
            )
        return (
            f'<section class="slide body numconcl{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="list">{rows_html}</div>'
            "</div>"
            "</section>"
        )

                                                                               
    def grid_2x2_layout(
        self,
        title: str,
        cells: List[dict],
        subtitle: str = "",
        caption: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        cells_html = ""
        for cell in cells[:4]:
            h3 = _e(cell.get("title", ""))
            p = _e(cell.get("body", ""))
            cells_html += (
                f'<div class="cell">'
                f'<div class="cell-content" data-fit-block style="display:flex;flex-direction:column;gap:12px;height:100%;">'
                '<div class="dash"></div>'
                f'<h3 data-fit data-fit-lines="3" data-fit-min="20" data-fit-max="48">{h3}</h3>'
                f"<p>{p}</p>"
                "</div>"
                "</div>"
            )
        return (
            f'<section class="slide body grid2x2{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="cells">{cells_html}</div>'
            "</div>"
            "</section>"
        )

                                                                                
    def research_question_layout(
        self,
        title: str,
        main_question: str,
        sub_questions: List[str],
    ) -> str:
        light_cls = " light" if self._light else ""
        subs_html = ""
        for i, sq in enumerate(sub_questions[:3]):
            subs_html += (
                f'<div class="sub">'
                f'<div class="lbl">Sub-Q {i+1:02d}</div>'
                f'<p class="q">{_e(sq)}</p>'
                "</div>"
            )
        return (
            f'<section class="slide body rquestion{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            '<div class="main-rq">'
            '<div class="lbl">Main RQ</div>'
            f'<div class="q">{_e(main_question)}</div>'
            "</div>"
            f'<div class="subs">{subs_html}</div>'
            "</div>"
            "</section>"
        )

                                                                                
    def agenda_layout(
        self,
        title: str,
        items: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        sub_html = f"<p>{_e(subtitle)}</p>" if subtitle else ""
        n = len(items)
        # Slide is 1920×1080, body-wrap padding 180px top + 140px bottom = 760px available.
        # Per-item budget: (760px - lhs_heading ~160px head reserve) / n
        # Use CSS custom properties so they cascade properly into li and .ttl.
        if n >= 9:
            ol_vars = ' style="--ag-li-pad:4px; --ag-ttl-size:28px;"'
        elif n == 8:
            ol_vars = ' style="--ag-li-pad:6px; --ag-ttl-size:32px;"'
        elif n == 7:
            ol_vars = ' style="--ag-li-pad:10px; --ag-ttl-size:38px;"'
        elif n == 6:
            ol_vars = ' style="--ag-li-pad:16px; --ag-ttl-size:46px;"'
        else:
            ol_vars = ''
        li_html = ""
        for item in items:
            ttl = _e(item.get("title", ""))
            dur = item.get("duration", "")
            dur_html = f'<span class="dur">{_e(dur)}</span>' if dur else ""
            li_html += f'<li><span class="ttl">{ttl}</span>{dur_html}</li>'
        return (
            f'<section class="slide body agenda{light_cls}">'
            '<div class="body-wrap">'
            '<div class="lhs">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="140">{_e(title)}</h2>'
            f"{sub_html}"
            "</div>"
            f"<ol{ol_vars}>{li_html}</ol>"
            "</div>"
            "</section>"
        )

                                                                                
    def stats_cards_layout(
        self,
        title: str,
        stats: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        nums_html = ""
        for stat in stats:
            big = _e(stat.get("value", ""))
            lbl = _e(stat.get("label", ""))
            nums_html += (
                f'<div class="n">'
                f'<div class="big">{big}</div>'
                f'<div class="lbl">{lbl}</div>'
                "</div>"
            )
        note_html = f'<div class="note">{_e(subtitle)}</div>' if subtitle else ""
        return (
            f'<section class="slide body stat{light_cls}">'
            '<div class="body-wrap">'
            f'<div class="kicker">{_e(title)}</div>'
            f'<div class="nums">{nums_html}</div>'
            f"{note_html}"
            "</div>"
            "</section>"
        )

                                                                                
    def quote_layout(
        self,
        quote: str,
        attribution: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        who_html = f'<div class="who"><b>{_e(attribution)}</b></div>' if attribution else ""
        return (
            f'<section class="slide body quote{light_cls}">'
            '<div class="body-wrap">'
            '<div class="mark">&ldquo;</div>'
            f'<blockquote data-fit data-fit-lines="3" data-fit-min="32" data-fit-max="120">{_e(quote)}</blockquote>'
            f"{who_html}"
            "</div>"
            "</section>"
        )

                                                                                
    def section_divider_layout(
        self,
        title: str,
        section_number: str = "",
        part_label: str = "",
    ) -> str:
        sec_num_html = (
            f'<div class="section-num">Part &middot; {_e(section_number)}</div>'
            if section_number else ""
        )
        foot_html = ""
        if part_label:
            foot_html = (
                '<div class="section-foot">'
                f'<div class="lead">{_e(part_label)}</div>'
                "</div>"
            )
        light_cls = " light" if self._light else ""
        return (
            f'<section class="slide section-divider{light_cls}">'
            '<div class="body-wrap">'
            f"{sec_num_html}"
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="280">{_e(title)}</h2>'
            f"{foot_html}"
            "</div>"
            "</section>"
        )

                                                                                
    def image_fullscreen_overlay_layout(
        self,
        title: str,
        body: str,
        img_path: str,
    ) -> str:
        img = (
            f'<img src="{_e(img_path)}" alt="{_e(title)}" '
            'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;">'
        )
        return (
            '<section class="slide imgfull">'
            f'<div class="img-slot-full">{img}</div>'
            '<div class="overlay"></div>'
            '<div class="body-wrap">'
            '<div class="accent-bar"></div>'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<p>{_e(body)}</p>'
            "</div>"
            "</section>"
        )

                                                                               
    def editorial_layout(
        self,
        title: str,
        lede: str,
        pull_quote: str = "",
        pull_attribution: str = "",
        eyebrow: str = "",
        meta: Optional[List[dict]] = None,
        footline_left: str = "",
        footline_right: str = "",
    ) -> str:
        eyebrow_html = (
            f'<div class="eyebrow">{_e(eyebrow)}</div>' if eyebrow
            else '<div class="eyebrow"></div>'
        )
        pull_html = ""
        if pull_quote:
            pa = f'<div class="who">{_e(pull_attribution)}</div>' if pull_attribution else ""
            pull_html = (
                '<div class="pull">'
                '<div class="mark">&ldquo;</div>'
                f'<blockquote data-fit data-fit-lines="3" data-fit-min="18" data-fit-max="48">'
                f'{_e(pull_quote)}</blockquote>'
                f"{pa}"
                "</div>"
            )
        meta_html = ""
        if meta:
            meta_items = "".join(
                f'<div><span class="k">{_e(m.get("key", ""))} &mdash;</span>'
                f'<span class="v">{_e(m.get("value", ""))}</span></div>'
                for m in meta
            )
            meta_html = f'<div class="meta">{meta_items}</div>'
        footline_html = ""
        if footline_left or footline_right:
            footline_html = (
                '<div class="footline">'
                f'<span>{_e(footline_left)}</span>'
                f'<span>{_e(footline_right)}</span>'
                "</div>"
            )
        return (
            '<section class="slide editorial-light">'
            '<div class="body-wrap">'
            f"{eyebrow_html}"
            f'<h2 data-fit data-fit-lines="2" data-fit-min="32" data-fit-max="120">{_e(title)}</h2>'
            f'<div class="sidebar">{pull_html}{meta_html}</div>'
            f'<p class="lede">{_e(lede)}</p>'
            f"{footline_html}"
            "</div>"
            "</section>"
        )

                                                                                
    def nested_bullets_layout(
        self,
        title: str,
        items: List,
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        items_list = items if isinstance(items, list) else []
        li_html = ""
        for it in items_list:
            if isinstance(it, dict):
                text = _e(it.get("text", ""))
                sub = it.get("sub", [])
            else:
                text = _e(str(it))
                sub = []
            sub_html = ""
            if sub:
                sub_html = "<ul>" + "".join(f"<li>{_e(str(s))}</li>" for s in sub) + "</ul>"
            li_html += f"<li>{text}{sub_html}</li>"
        top_texts = [
            (it.get("text", "") if isinstance(it, dict) else str(it)) for it in items_list
        ]
        style = self._bullet_style(top_texts)
        return (
            f'<section class="slide body bullets{light_cls}">'
            '<div class="body-wrap" data-fit-block data-fit-reserve="70">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<ul style="{style}">{li_html}</ul>'
            "</div>"
            "</section>"
        )

                                                                                
    def data_table_layout(
        self,
        title: str,
        headers: List[str],
        rows: List[List],
        caption: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        th = "".join(f"<th>{_cell_text(str(h))}</th>" for h in headers)
        tbody = ""
        for row in rows:
            td = "".join(f"<td>{_cell_text(str(c))}</td>" for c in row)
            tbody += f"<tr>{td}</tr>"
        table_html = f"<table><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>"
        cap_html = f'<div class="caption">{_e(caption)}</div>' if caption else ""
        return (
            f'<section class="slide body cmptable{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="112">{_e(title)}</h2>'
            f'<div class="tbl-wrap">{table_html}{cap_html}</div>'
            "</div>"
            "</section>"
        )

                                                                                
    def pricing_cards_layout(
        self,
        title: str,
        cards: List[dict],
        subtitle: str = "",
    ) -> str:
        light_cls = " light" if self._light else ""
        cards_html = ""
        for c in cards:
            name = _e(c.get("name", ""))
            price = _e(c.get("price", ""))
            period = _e(c.get("period", ""))
            features = c.get("features", [])
            note = _e(c.get("note", ""))
            hl = bool(c.get("highlighted", False))
            badge = _e(c.get("badge", "POPULAR"))
            card_cls = " featured" if hl else ""
            badge_html = f'<div class="badge">{badge}</div>' if hl else ""
            period_html = f"<small>/{period}</small>" if period else ""
            li_items = "".join(f"<li>{_e(str(f))}</li>" for f in features)
            note_html = f"<p>{note}</p>" if note else ""
            cards_html += (
                f'<div class="card{card_cls}">'
                f'<div class="card-content" data-fit-block style="display:flex;flex-direction:column;gap:10px;height:100%;">'
                f"{badge_html}"
                f'<div class="name">{name}</div>'
                f'<div class="price">{price}{period_html}</div>'
                f"<ul>{li_items}</ul>"
                f"{note_html}"
                "</div>"
                "</div>"
            )
        return (
            f'<section class="slide body pricing{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="100">{_e(title)}</h2>'
            f'<div class="cards">{cards_html}</div>'
            "</div>"
            "</section>"
        )

                                                                               
    def cover_split_layout(
        self,
        eyebrow: str = "",
        meta_rows: Optional[List[dict]] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        eyebrow_html = (
            f'<div class="eyebrow">{_e(eyebrow)}</div>'
            if eyebrow else '<div class="eyebrow"></div>'
        )
        rows_html = ""
        for row in (meta_rows or []):
            k = _e(row.get("k", row.get("key", "")))
            v = _e(row.get("v", row.get("value", "")))
            rows_html += f'<div class="row"><div class="k">{k}</div><div class="v">{v}</div></div>'
        if not rows_html:
            rows_html = f'<div class="row"><div class="k">Presented by</div><div class="v">{_e(self.author)}</div></div>'
        return (
            f'<section class="slide cover-split{light_cls}">'
            '<div class="body-wrap">'
            '<div class="lhs">'
            f'{eyebrow_html}'
            f'<h1 data-fit data-fit-lines="2" data-fit-min="80" data-fit-max="200">{_e(self.title)}</h1>'
            '</div>'
            f'<div class="rhs">{rows_html}</div>'
            '</div>'
            '</section>'
        )

                                                                               
    def toc_vertical_layout(
        self,
        toc_content: List[str],
        heading: str = "Table of Contents",
    ) -> str:
        light_cls = " light" if self._light else ""
        items_html = ""
        for item in toc_content:
            m = re.match(r"^(\d+)\.\s*(.*?)(?:\s*\|\s*(.*))?$", str(item))
            if m:
                num = m.group(1).zfill(2)
                text = _e(m.group(2).strip())
                page = m.group(3) or ""
            else:
                num = ""
                text = _e(str(item))
                page = ""
            page_html = f'<span class="p">{_e(page)}</span>' if page else ""
            items_html += (
                f'<li>'
                f'<span class="n">{num}</span>'
                f'<span class="t">{text}</span>'
                f'{page_html}'
                '</li>'
            )
        return (
            f'<section class="slide body toc-vertical{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="120">{_e(heading)}</h2>'
            f'<ol>{items_html}</ol>'
            '</div>'
            '</section>'
        )

                                                                                
    def toc_described_layout(
        self,
        toc_content: List,
        heading: str = "What we'll cover",
    ) -> str:
        light_cls = " light" if self._light else ""
        items_html = ""
        for i, item in enumerate(toc_content):
            if isinstance(item, dict):
                num = str(item.get("n", item.get("num", i + 1))).zfill(2)
                title = _e(item.get("title", item.get("t", "")))
                desc = _e(item.get("description", item.get("d", item.get("body", ""))))
                dur = _e(str(item.get("duration", item.get("dur", ""))))
            else:
                m = re.match(r"^(\d+)\.\s*(.*)", str(item))
                num = m.group(1).zfill(2) if m else str(i + 1).zfill(2)
                title = _e(m.group(2).strip() if m else str(item))
                desc = ""
                dur = ""
            dur_html = f'<div class="dur">{dur}</div>' if dur else ""
            desc_html = f'<div class="d">{desc}</div>' if desc else ""
            items_html += (
                f'<li>'
                f'<div class="n">{num}</div>'
                f'<div class="meta"><div class="t">{title}</div>{desc_html}</div>'
                f'{dur_html}'
                '</li>'
            )
        return (
            f'<section class="slide body toc-described{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="120">{_e(heading)}</h2>'
            f'<ol>{items_html}</ol>'
            '</div>'
            '</section>'
        )

                                                                                
    def toc_cards_layout(
        self,
        toc_content: List,
        heading: str = "Sections",
    ) -> str:
        light_cls = " light" if self._light else ""
        cards_html = ""
        for i, item in enumerate(toc_content):
            if isinstance(item, dict):
                n_label = _e(str(item.get("n", item.get("label", f"{i+1:02d}"))))
                title = _e(item.get("title", item.get("t", "")))
                desc = _e(item.get("description", item.get("d", "")))
            else:
                m = re.match(r"^(\d+)\.\s*(.*)", str(item))
                n_label = m.group(1).zfill(2) if m else str(i + 1).zfill(2)
                title = _e(m.group(2).strip() if m else str(item))
                desc = ""
            desc_html = f'<p data-fit data-fit-lines="5" data-fit-min="12" data-fit-max="22">{desc}</p>' if desc else ""
            desc_html_scaled = desc_html.replace('data-fit-max="22"', 'data-fit-max="32"')
            cards_html += (
                f'<div class="card">'
                f'<div class="card-content" data-fit-block style="display:flex;flex-direction:column;gap:10px;height:100%;">'
                f'<div class="n">{n_label}</div>'
                f'<h3 data-fit data-fit-lines="3" data-fit-min="18" data-fit-max="50" style="margin:0;">{title}</h3>'
                f'{desc_html_scaled}'
                '</div>'
                '</div>'
            )
        return (
            f'<section class="slide body toc-cards{light_cls}">'
            '<div class="body-wrap">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="24" data-fit-max="120">{_e(heading)}</h2>'
            f'<div class="grid" data-fit-block style="justify-content: center;">{cards_html}</div>'
            '</div>'
            '</section>'
        )

                                                                                
    def end_with_image_layout(
        self,
        end_text: str = "Thank you",
        img_path: str = "",
        caption: str = "",
        meta_items: Optional[List[dict]] = None,
    ) -> str:
        light_cls = " light" if self._light else ""
        items = meta_items or [
            {"k": "Presented by", "v": self.author},
            {"k": "Q&A Session", "v": "Open discussion"},
        ]
        foot_html = ""
        for item in items:
            k = _e(item.get("k", ""))
            v = _e(item.get("v", ""))
            foot_html += f'<div class="item"><div class="k">{k}</div><div class="v">{v}</div></div>'
        if img_path:
            slot_html = (
                f'<img src="{_e(img_path)}" alt="{_e(end_text)}" '
                'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;">'
            )
        else:
            slot_html = '<div class="label">Image / GIF</div><div class="hint">Drop a team photo or animated GIF here.</div>'
        cap_html = f'<figcaption><b>{_e(caption)}</b></figcaption>' if caption else ""
        return (
            f'<section class="slide end-with-image{light_cls}">'
            '<div class="body-wrap">'
            '<div class="lhs">'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="80" data-fit-max="200">{_e(end_text)}</h2>'
            f'<div class="end-foot">{foot_html}</div>'
            '</div>'
            '<figure>'
            f'<div class="img-slot">{slot_html}</div>'
            f'{cap_html}'
            '</figure>'
            '</div>'
            '</section>'
        )

                                                                                
    def end_image_hero_layout(
        self,
        end_text: str = "Thank you",
        img_path: str = "",
        meta_items: Optional[List[dict]] = None,
    ) -> str:
        items = meta_items or [
            {"k": "Presented by", "v": self.author},
            {"k": "Q&A Session", "v": "Open discussion"},
        ]
        foot_html = ""
        for item in items:
            k = _e(item.get("k", ""))
            v = _e(item.get("v", ""))
            foot_html += f'<div class="item"><div class="k">{k}</div><div class="v">{v}</div></div>'
        img_html = (
            f'<img src="{_e(img_path)}" alt="{_e(end_text)}" '
            'style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover;">'
        ) if img_path else ""
        return (
            '<section class="slide end-image-hero">'
            f'<div class="img-slot-full">{img_html}</div>'
            '<div class="overlay"></div>'
            '<div class="body-wrap">'
            '<div class="accent-bar"></div>'
            f'<h2 data-fit data-fit-lines="2" data-fit-min="80" data-fit-max="240">{_e(end_text)}</h2>'
            f'<div class="end-foot">{foot_html}</div>'
            '</div>'
            '</section>'
        )

                                                                                
    def build_html_document(
        self,
        sections: List[str],
        page_title: str = "Presentation",
    ) -> str:
        slides_html = "\n".join(sections)
        theme_vars = self._theme_vars()
        css = _EMBEDDED_CSS
        return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{_e(page_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="{_FONTS_LINK}" rel="stylesheet" />
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.css" />
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.11/dist/contrib/auto-render.min.js"
        onload="renderMathInElement(document.body,{{delimiters:[{{left:'$$',right:'$$',display:true}},{{left:'\\\\[',right:'\\\\]',display:true}},{{left:'$',right:'$',display:false}},{{left:'\\\\(',right:'\\\\)',display:false}}]}})"></script>
<style>
{css}
{_EDITOR_CSS}
</style>
</head>
<body>
<deck-stage style="{theme_vars}">
{slides_html}
</deck-stage>
<script src="deck-stage.js"></script>
<script>
{_EDITOR_SAVE_JS}
</script>
<script>
{_EDITOR_JS}
</script>
</body>
</html>"""

def _cell_text(c: str) -> str:
    c = re.sub(r'\$\$(.+?)\$\$', lambda m: f'${m.group(1).strip()}$', c, flags=re.DOTALL)
    tokens = re.split(r'(\*\*[^*\n]+\*\*)', c)
    parts: list[str] = []
    for token in tokens:
        bold_m = re.match(r'^\*\*([^*\n]+)\*\*$', token)
        if bold_m:
            parts.append(f'<b>{_e(bold_m.group(1))}</b>')
        else:
            sub_tokens = re.split(r'(<br\s*/?>)', token, flags=re.IGNORECASE)
            for sub in sub_tokens:
                if re.match(r'^<br\s*/?>$', sub, re.IGNORECASE):
                    parts.append('<br>')
                else:
                    parts.append(_e(sub))
    return ''.join(parts)

def _md_table_to_html(md: str) -> str:
    lines = [l.strip() for l in md.strip().splitlines() if l.strip()]
    if not lines:
        return ""
    rows: List[List[str]] = []
    for line in lines:
        if re.match(r"^\|?[-:| ]+\|?$", line):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line) if c.strip() or line.startswith("|")]
        if cells:
            rows.append(cells)
    if not rows:
        return ""
                                    
    max_cols = max(len(r) for r in rows)
    rows = [r + [""] * (max_cols - len(r)) for r in rows]
                                                                                         
    non_empty_cols = [i for i in range(max_cols) if any(rows[r][i] for r in range(len(rows)))]
    if non_empty_cols:
        rows = [[row[i] for i in non_empty_cols] for row in rows]
    head = rows[0]
    body = rows[1:]
    th = "".join(f"<th>{_cell_text(c)}</th>" for c in head)
    tbody = ""
    for row in body:
        td = "".join(f"<td>{_cell_text(c)}</td>" for c in row)
        tbody += f"<tr>{td}</tr>"
    return f"<table><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>"
