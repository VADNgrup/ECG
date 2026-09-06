from __future__ import annotations
import json
import random
import re
import shutil
import logging
from collections import defaultdict
from pathlib import Path
from PIL import Image
from typing import Dict, List, Optional
from src.generator.deck_html_layout_manager import DeckHTMLLayoutManager
from src.generator.fabric_editor_builder import build_slide_spec_json, build_fabric_html
from src.generator.theme_selection import select_theme
from src.utils.config import Config
_PROJECT_ROOT = Path(__file__).resolve().parents[2]

def _extract_institution(markdown: str) -> str:
    fn_match = re.search(r'(\[\^\d+\]:.*?)(?=\n##|\Z)', markdown, re.DOTALL)
    search_text = fn_match.group(1) if fn_match else markdown[:3000]
    for pattern in [
        r'University\s+of\s+[\w\s]{3,50}',
        r'[\w\s]{3,40}\s+University',
        r'Institute\s+of\s+[\w\s]{3,50}',
        r'[\w\s]{3,40}\s+Institute\s+of\s+Technology',
        r'College\s+of\s+[\w\s]{3,40}',
        r'(?:Research\s+)?(?:Center|Centre)\s+(?:for|of)\s+[\w\s]{3,50}',
    ]:
        m = re.search(pattern, search_text, re.I)
        if m:
            result = m.group(0).strip().rstrip('., ')
            if len(result) >= 8:
                return result
    return ''
class SlidePickMerge:

    def __init__(self, lecture_json_path: str, lecture_title: str, speaker_information: str, deck_dir: Path | None = None, date: str = "", institution: str = ""):
        self.lecture_json_path = Path(lecture_json_path).resolve()
        self.lecture_title = lecture_title
        self.speaker_information = speaker_information
        self.institution = institution
        import datetime as _dt
        self.date = date or _dt.date.today().strftime("%d/%m/%Y")
        stem = self.lecture_json_path.stem
        parent = self.lecture_json_path.parent
        self.image_dist_path = parent / f'{stem}_image_distribution.json'
        self.table_dist_path = parent / f'{stem}_table_distribution.json'
        self.outline_path = parent / f'{stem}_outline.md'
        with open(self.lecture_json_path, encoding='utf-8') as f:
            self._lecture = json.load(f)
        self.lecture_id = self._lecture['lecture_id']
        self.source_doc_id = self._lecture['metadata']['source_document_id']
        self.slides = self._lecture['slides']
        self._image_dist: Dict[int, List[dict]] = self._load_image_dist()
        self._table_dist: Dict[int, dict] = self._load_table_dist()
        outline_md = self.outline_path.read_text(encoding='utf-8') if self.outline_path.exists() else ''
        (self.theme, self.font) = select_theme(outline_md)
        logging.info(f'====> Selected theme: {self.theme}')
        self._mgr = DeckHTMLLayoutManager(theme=self.theme, title=self.lecture_title, author=self.speaker_information)
        self._deck_dir: Path = deck_dir if deck_dir is not None else _PROJECT_ROOT / 'src' / 'generator' / 'deck'
        self._layout_log: List[dict] = []
        self._slide_counter: int = 0
        self._content_idx: int = 0
        self._page_counter: int = 0
        self._short_title: str = ""
        self._deck_sections: List[str] = []

    def _formula_table_source_pages(self) -> set:
        """Return set of PDF page numbers that are source pages for slides with formula/table content.
        Images from these pages were consumed by OCR extraction and should not be placed as raw images.
        Uses two independent sources so stale packet slide-numbers don't cause silent failures."""
        # Source A: slide_packets.json — exact source_pages per slide number
        packets_path = Config.LECTURES_DIR / self.lecture_id / f'{self.lecture_id}_slide_packets.json'
        pages_by_slide: Dict[int, List[int]] = {}
        if packets_path.exists():
            try:
                packets = json.loads(packets_path.read_text(encoding='utf-8'))
                for pkt in (packets if isinstance(packets, list) else []):
                    sn = pkt.get('slide_number')
                    if sn is None:
                        continue
                    pages = [int(p) for p in (pkt.get('source_pages') or []) if str(p).isdigit()]
                    if pages:
                        pages_by_slide[int(sn)] = pages
            except Exception:
                pass

        formula_pages: set = set()
        for slide_entry in self.slides:
            slide_meta = slide_entry.get('slide', {})
            has_formula = bool(slide_meta.get('latex_block_formula'))
            has_table = bool((slide_meta.get('table') or {}).get('table_markdown'))
            if has_formula or has_table:
                sn = slide_meta.get('slide_number', -1)
                try:
                    sn = int(sn)
                except (TypeError, ValueError):
                    continue
                for pg in pages_by_slide.get(sn, []):
                    formula_pages.add(pg)

        # Source B: title-based packets match — guards against stale slide numbering.
        # If the lecture was regenerated with different slide numbers, Source A silently
        # returns 0 pages. Re-match by title prefix so renumbered slides still block their pages.
        if packets_path.exists() and len(formula_pages) == 0:
            try:
                formula_titles = set()
                for slide_entry in self.slides:
                    sm = slide_entry.get('slide', {})
                    if sm.get('latex_block_formula') or (sm.get('table') or {}).get('table_markdown'):
                        t = (sm.get('slide_title', '') or '').lower()
                        if t:
                            formula_titles.add(t[:40])
                packets = json.loads(packets_path.read_text(encoding='utf-8'))
                for pkt in (packets if isinstance(packets, list) else []):
                    pt = (pkt.get('slide_title', '') or '').lower()[:40]
                    if any(pt and (pt in ft or ft in pt) for ft in formula_titles):
                        for pg in [int(p) for p in (pkt.get('source_pages') or []) if str(p).isdigit()]:
                            formula_pages.add(pg)
            except Exception:
                pass

        if formula_pages:
            logging.debug(f'[image_dist] Formula/table source pages: {sorted(formula_pages)}')
        return formula_pages

    @staticmethod
    def _is_formula_or_table_image(item: dict) -> bool:
        """Return True if the image caption/description signals it IS the formula/table content."""
        cap = (item.get('caption', '') or '').lower()
        is_table = bool(re.search(
            r'\btable\s*\d+|\btable\s*[ivxlcdm]+\b|\btable\s*[a-z]\b|\btab\.\s*\d|\bdata\s+table\b',
            cap
        ))
        is_formula = (
            bool(re.search(r'\bequation\s*\(?\d+\)?', cap)) or
            bool(re.search(r'\\begin\{|\\frac\{|\\le\b|\\ge\b', cap))
        )
        return is_table or is_formula

    def _load_image_dist(self) -> Dict[int, List[dict]]:
        if not self.image_dist_path.exists():
            return {}
        with open(self.image_dist_path, encoding='utf-8') as f:
            items = json.load(f)
        formula_pages = self._formula_table_source_pages()
        result: Dict[int, List[dict]] = defaultdict(list)
        for item in items:
            img_path = item.get('image_path', '')
            # Layer 1: page-based filter (requires slide_packets.json to be current)
            m = re.search(r'page_(\d+)', img_path)
            if m and formula_pages and int(m.group(1)) in formula_pages:
                logging.debug(f'[image_dist] Skip {Path(img_path).name} — page {m.group(1)} is formula/table source page')
                continue
            # Layer 2: caption-based filter (works even when packets file is stale/missing)
            if self._is_formula_or_table_image(item):
                logging.debug(f'[image_dist] Skip {Path(img_path).name} — table/formula caption detected')
                continue
            result[int(item['slide_number'])].append(item)
        return dict(result)

    def _load_table_dist(self) -> Dict[int, dict]:
        if not self.table_dist_path.exists():
            return {}
        with open(self.table_dist_path, encoding='utf-8') as f:
            items = json.load(f)
        return {int(item['slide_number']): item for item in items}

    def _to_assets_path(self, original_path: str) -> str:
        filename = original_path.replace('\\', '/').split('/')[-1]
        return f'assets/{self.lecture_id}/' + filename

    def _lecture_assets_dir(self) -> Path:
        return self._deck_dir / 'assets' / self.lecture_id

    def _clear_public_assets(self):
        lecture_dir = self._lecture_assets_dir()
        if lecture_dir.exists():
            shutil.rmtree(lecture_dir)
        lecture_dir.mkdir(parents=True, exist_ok=True)

    def _copy_assets(self):
        dest = self._lecture_assets_dir()
        base = _PROJECT_ROOT / 'data' / 'assets' / self.source_doc_id
        for sub in ('images', 'downloaded_images'):
            folder = base / sub
            if folder.exists():
                for f in folder.iterdir():
                    if f.is_file():
                        shutil.copy2(f, dest / f.name)
        downloaded = _PROJECT_ROOT / 'data' / 'lectures' / self.lecture_id / 'downloaded_images'
        if downloaded.exists():
            for f in downloaded.iterdir():
                if f.is_file():
                    shutil.copy2(f, dest / f.name)

    def _img_aspect_ratio(self, img_path: str) -> float:
        try:
            resolved = _PROJECT_ROOT / Path(img_path.replace('\\', '/'))
            with Image.open(resolved) as img:
                (w, h) = img.size
                return w / h if h else 0.0
        except Exception:
            return 1.0

    def _log_layout(self, slide_num, func_name: str, args: dict) -> None:
        self._layout_log.append({'slide_num': slide_num, 'layout_function_name': func_name, 'args': args})

    def _parse_outline(self) -> List[str]:
        counter = 0
        results: List[str] = []
        if not self.outline_path.exists():
            for slide_entry in self.slides:
                slide_info = slide_entry.get('slide', {}) if isinstance(slide_entry, dict) else {}
                text = str(slide_info.get('slide_title') or '').strip()
                text = re.sub(r'^\s*\d+(?:\.\d+)*[.)]?\s*', '', text).strip()
                if not text:
                    continue
                counter += 1
                results.append(f'{counter}. {text}')
            return results
        with open(self.outline_path, encoding='utf-8') as f:
            for line in f:
                line = line.rstrip()
                if not line:
                    continue
                m = re.match('^(#{1,6})\\s+(.*)', line)
                if not m:
                    continue
                depth = len(m.group(1))
                if depth != 1:
                    continue
                text = m.group(2).strip()
                counter += 1
                results.append(f'{counter}. {text}')
        return results

    def _toc_two_col_split(self, contents: List[str]) -> tuple[List[str], List[str]]:

        def _is_major(item: str) -> bool:
            return bool(re.match('^\\d+\\.\\s+\\S', item)) and item.split('.')[0].strip().isdigit()
        total = sum((len(s) for s in contents))
        target = total / 2
        running = 0
        mid_idx = len(contents) // 2
        for (i, item) in enumerate(contents):
            running += len(item)
            if running >= target:
                mid_idx = i
                break
        split_idx = None
        for i in range(mid_idx, len(contents)):
            if _is_major(contents[i]):
                split_idx = i
                break
        if split_idx is None or split_idx == 0:
            split_idx = max(1, mid_idx)
        return (contents[:split_idx], contents[split_idx:])

    def _get_section_goals(self) -> dict:
        goals: dict = {}
        for slide_entry in self.slides:
            slide_info = slide_entry.get('slide', {})
            title = str(slide_info.get('slide_title', ''))
            m = re.match(r'^(\d+)\.', title)
            if not m:
                continue
            num = int(m.group(1))
            if num in goals:
                continue
            goal = str(slide_info.get('goal', '')).strip()
            if not goal:
                continue
            goals[num] = goal
        return goals

    def _build_toc_slide(self, contents: List[str]) -> str:
        mgr = self._mgr
        if len(contents) > 15:
            contents = [item for item in contents if re.match('^\\d+\\.\\s+\\S', item) and item.split('.')[0].strip().isdigit()]
        self._slide_counter += 1
        heading = 'Outline'
        if len(contents) > 6:
            candidates = ['toc']
        else:
            candidates = ['toc', 'toc_vertical', 'toc_described']
            if 3 <= len(contents) <= 5:
                candidates.append('toc_cards')
        variant = random.choice(candidates)
        if variant == 'toc_vertical':
            self._log_layout(self._slide_counter, 'toc_vertical_layout', {'toc_content': contents, 'heading': heading})
            return mgr.toc_vertical_layout(contents, heading=heading)
        if variant == 'toc_described':
            self._log_layout(self._slide_counter, 'toc_described_layout', {'toc_content': contents, 'heading': heading})
            return mgr.toc_described_layout(contents, heading=heading)
        if variant == 'toc_cards':
            goals = self._get_section_goals()
            card_items = []
            for item in contents:
                m = re.match(r'^(\d+)\.\s*(.*)', str(item))
                num = int(m.group(1)) if m else 0
                title_text = m.group(2).strip() if m else str(item)
                card_items.append({'n': str(num).zfill(2), 'title': title_text, 'description': goals.get(num, '')})
            self._log_layout(self._slide_counter, 'toc_cards_layout', {'toc_content': card_items, 'heading': heading})
            return mgr.toc_cards_layout(card_items, heading=heading)
        self._log_layout(self._slide_counter, 'toc_layout', {'toc_content': contents, 'heading': heading})
        return mgr.toc_layout(contents, heading=heading)

    def _pick_image_layout(self, slide_num: int, title: str, contents: List[str], img_url: str, img_path: str, img2_url: Optional[str]=None, img2_path: Optional[str]=None, caption: Optional[str]=None, caption2: Optional[str]=None) -> str:
        mgr = self._mgr
        caption = self._normalise_image_caption(caption, title)
        caption2 = self._normalise_image_caption(caption2, title)
        if img2_url and img2_path:
            ratio1 = self._img_aspect_ratio(img_path)
            ratio2 = self._img_aspect_ratio(img2_path)
            both_landscape = ratio1 >= 1.77 and ratio2 >= 1.77
            if both_landscape:
                _args = {'title': title, 'content': contents, 'img1_path': img_url, 'img2_path': img2_url, 'caption1': caption, 'caption2': caption2}
                if random.random() < 0.5:
                    self._log_layout(slide_num, 'two_image_above_layout', _args)
                    return mgr.two_image_above_layout(title, contents, img_url, img2_url, caption1=caption, caption2=caption2)
                else:
                    self._log_layout(slide_num, 'two_image_below_layout', _args)
                    return mgr.two_image_below_layout(title, contents, img_url, img2_url, caption1=caption, caption2=caption2)
            else:
                _args = {'title': title, 'content': contents, 'img1_path': img_url, 'img2_path': img2_url, 'image_width': '30%', 'caption1': caption, 'caption2': caption2}
                if random.random() < 0.5:
                    self._log_layout(slide_num, 'two_image_right_layout', _args)
                    return mgr.two_image_right_layout(title, contents, img_url, img2_url, caption1=caption, caption2=caption2)
                else:
                    self._log_layout(slide_num, 'two_image_left_layout', _args)
                    return mgr.two_image_left_layout(title, contents, img_url, img2_url, caption1=caption, caption2=caption2)
        ratio = self._img_aspect_ratio(img_path)
        _args = {'title': title, 'content': contents, 'img_path': img_url, 'image_width': '40%', 'caption': caption}
        if ratio > 1.77:
            if random.random() < 0.5:
                self._log_layout(slide_num, 'image_above_layout', _args)
                return mgr.image_above_layout(title, contents, img_url, caption=caption)
            else:
                self._log_layout(slide_num, 'image_below_layout', _args)
                return mgr.image_below_layout(title, contents, img_url, caption=caption)
        elif random.random() < 0.5:
            self._log_layout(slide_num, 'image_right_layout', _args)
            return mgr.image_right_layout(title, contents, img_url, caption=caption)
        else:
            self._log_layout(slide_num, 'image_left_layout', _args)
            return mgr.image_left_layout(title, contents, img_url, caption=caption)

    @staticmethod
    def _normalise_image_caption(caption: Optional[str], slide_title: str) -> Optional[str]:
        text = re.sub(r'\s+', ' ', str(caption or '')).strip()
        text = re.sub(r'!\[[^\]]*\]\([^)]+\)', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'#{1,6}\s*', ' ', text)
        text = re.sub(r'\*+\s*(?:figure|fig\.?|table|hình|bảng)\s*:\s*', ' ', text, flags=re.IGNORECASE)
        text = text.replace('*', ' ')
        text = re.sub(r'\b[a-zA-Z0-9_\-/]+\.(?:png|jpe?g|webp)\b', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'/assets/\S+|\bassets/\S+', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'<!--\s*PAGE\s+\d+\s*-->', ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'\s+', ' ', text).strip(' -:;,.')
        if not text:
            return None
        if '/' in text and len(text.split()) <= 4:
            return None
        if any(token in text for token in ['![', '](', '##', '# ']):
            return None
        if len(text.split()) > 12:
            text = ' '.join(text.split()[:12])
        if len(text.split()) <= 2 and text.lower() == slide_title.strip().lower():
            return None
        return text

    @staticmethod
    def _split_bullet_heading_body(text: str):
        text = text.strip()
        for sep in [':', ' — ', ' – ', ' - ']:
            if sep in text:
                idx = text.index(sep)
                heading = text[:idx].strip()
                body = text[idx + len(sep):].strip()
                if 1 <= len(heading.split()) <= 8 and body:
                    return heading, body
        return text, ""

    @staticmethod
    def _is_bullet_split_friendly(text: str) -> bool:
        text = text.strip()
        for sep in [':', ' — ', ' – ', ' - ']:
            if sep in text:
                idx = text.index(sep)
                heading = text[:idx].strip()
                body = text[idx + len(sep):].strip()
                if 1 <= len(heading.split()) <= 8 and body:
                    return True
        return False

    @staticmethod
    def _bullets_are_split_friendly(bullets: List[str], min_ratio: float = 0.6) -> bool:
        if not bullets:
            return False
        friendly = sum(1 for b in bullets if SlidePickMerge._is_bullet_split_friendly(b))
        return friendly / len(bullets) >= min_ratio

    @staticmethod
    def _bullets_to_key_points(bullets: List[str]) -> List[dict]:
        icons = ['📌', '🔑', '💡', '📋', '🎯', '⚡', '🔍', '📊']
        pts = []
        for i, b in enumerate(bullets):
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            if not body and heading:
                # Plain sentence: auto-split — first 4 words become the title
                words = heading.split()
                if len(words) > 5:
                    heading = ' '.join(words[:4])
                    body = ' '.join(words[4:])
            pts.append({'icon': icons[i % len(icons)], 'title': heading, 'body': body})
        return pts

    @staticmethod
    def _bullets_to_conclusions(bullets: List[str]) -> List[dict]:
        results = []
        for b in bullets:
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            results.append({'heading': heading, 'body': body or b})
        return results

    @staticmethod
    def _bullets_to_steps(bullets: List[str]) -> List[dict]:
        steps = []
        for b in bullets[:5]:
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            steps.append({'title': heading, 'body': body or b})
        return steps

    @staticmethod
    def _bullets_to_grid_cells(bullets: List[str]) -> List[dict]:
        icons = ['🔷', '🔶', '🔵', '🟡']
        cells = []
        for i, b in enumerate(bullets[:4]):
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            cells.append({'icon': icons[i], 'title': heading, 'body': body or b})
        return cells

    @staticmethod
    def _bullets_to_three_cols(bullets: List[str]) -> List[dict]:
        icons = ['📌', '🔑', '💡']
        cols = []
        for i, b in enumerate(bullets[:3]):
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            cols.append({'icon': icons[i], 'title': heading, 'body': body or b, 'bullets': []})
        return cols

    @staticmethod
    def _bullets_to_agenda_items(bullets: List[str]) -> List[dict]:
        items = []
        for b in bullets:
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            items.append({'title': heading, 'body': body or b, 'duration': ''})
        return items

    @staticmethod
    def _bullets_to_pricing_cards(bullets: List[str]) -> List[dict]:
        cards = []
        for i, b in enumerate(bullets[:4]):
            heading, body = SlidePickMerge._split_bullet_heading_body(b)
            cards.append({
                'name': heading,
                'price': '',
                'features': [body] if body else [b],
                'highlighted': i == 0,
            })
        return cards

    @staticmethod
    def _strip_table_bullets(contents: list) -> list:
        if not isinstance(contents, list):
            return contents
        cleaned = []
        for item in contents:
            if isinstance(item, dict) and 'table_markdown' in item:
                continue
            if isinstance(item, str):
                s = item.strip()
                if s.startswith('|') and s.endswith('|'):
                    continue
                if 'table_markdown' in s and ('|' in s):
                    continue
                if s.count('|') >= 6:
                    continue
                parts = SlidePickMerge._split_bullet_text(s)
                cleaned.extend(parts)
                continue
            cleaned.append(item)
        return cleaned

    @staticmethod
    def _split_bullet_text(text: str) -> List[str]:
        text = str(text).strip()
        if not text:
            return []
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if len(lines) > 1:
            parts = []
            for line in lines:
                line = re.sub(r'^[-*•]\s+', '', line).strip()
                if line:
                    parts.append(line)
            return parts
        return [text]

    @staticmethod
    def _sanitise_latex(latex: str) -> str:
        if not latex:
            return ""
        s = latex.replace("\\n", "\n")
        s = s.strip()
        if s.startswith("$$") and s.endswith("$$") and s.count("$$") == 2:
            return s[2:-2].strip()
        if s.startswith("$") and s.endswith("$") and s.count("$") == 2:
            return s[1:-1].strip()
        if "$$" in s:
            s = re.sub(r'\$\$(.*?)\$\$', lambda m: f'\\[{m.group(1).strip()}\\]', s, flags=re.DOTALL)
        return s

    @staticmethod
    def _normalise_contents(raw_contents):
        def _compact(items):
            compacted = []
            for item in items:
                text = re.sub(r"\s+", " ", str(item).strip())
                if not text:
                    continue
                compacted.append(text)
            return SlidePickMerge._merge_fragments(compacted)[:9]

        if isinstance(raw_contents, list):
            cleaned = SlidePickMerge._strip_table_bullets(raw_contents)
            items = [str(item).strip() for item in cleaned if isinstance(item, str) and str(item).strip()]
            return _compact(items)
        if isinstance(raw_contents, str):
            text = re.sub(r'\s+', ' ', raw_contents).strip()
            if not text:
                return []
            sentences = re.split(r'(?<=[.!?])\s+', text)
            bullets = [s.strip() for s in sentences if s.strip()]
            return _compact(bullets[:10] if bullets else [text])
        if isinstance(raw_contents, dict):
            return raw_contents
        return []

    @staticmethod
    def _merge_fragments(items: List[str]) -> List[str]:
        merged: List[str] = []
        for item in items:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            starts_fragment = bool(re.match(r"^(?:and|or|but|with|while|plus|including|such as|each|plus|and each|32gb|16gb|250gb|40gb)\b", text, flags=re.IGNORECASE))
            
            is_cjk = any('\u4e00' <= char <= '\u9fff' for char in text)
            if is_cjk:
                too_short = len(text) <= 12 and bool(re.search(r"\d|gb|mb|cores?|ram|disk", text, flags=re.IGNORECASE))
            else:
                too_short = len(text.split()) <= 4 and bool(re.search(r"\d|gb|mb|cores?|ram|disk", text, flags=re.IGNORECASE))
                
            if merged and (starts_fragment or too_short):
                merged[-1] = re.sub(r"\s+", " ", f"{merged[-1].rstrip(' .;:,')}, {text.lstrip(' ,;:.')}")
                continue
            merged.append(text)
        return merged

    # Layouts that require "Short Title: Detailed body" bullet format
    # NOTE: key_points_layout is excluded — it handles plain sentences via auto-split in _bullets_to_key_points
    _TITLE_BODY_LAYOUTS = frozenset({
        'conclusion_cards_layout', 'numbered_conclusions_layout',
        'three_cols_content_layout', 'grid_2x2_layout', 'steps_horizontal_layout',
        'agenda_layout', 'pricing_cards_layout',
    })

    def _apply_layout_hint(self, sn: int, title: str, contents: List[str], layout_hint: str) -> Optional[str]:
        mgr = self._mgr
        n = len(contents)
        # Title:body layouts require properly formatted bullets — fall back if not split-friendly
        if layout_hint in self._TITLE_BODY_LAYOUTS and not self._bullets_are_split_friendly(contents):
            return None
        if layout_hint == 'key_points_layout' and 3 <= n <= 6:
            pts = self._bullets_to_key_points(contents)
            self._log_layout(sn, 'key_points_layout', {'title': title, 'points': pts})
            return mgr.key_points_layout(title, pts)
        if layout_hint == 'conclusion_cards_layout' and 3 <= n <= 5:
            conc = self._bullets_to_conclusions(contents)
            self._log_layout(sn, 'conclusion_cards_layout', {'title': title, 'conclusions': conc})
            return mgr.conclusion_cards_layout(title, conc)
        if layout_hint == 'numbered_conclusions_layout' and 4 <= n <= 7:
            conc = self._bullets_to_conclusions(contents)
            self._log_layout(sn, 'numbered_conclusions_layout', {'title': title, 'conclusions': conc})
            return mgr.numbered_conclusions_layout(title, conc)
        if layout_hint == 'three_cols_content_layout' and 3 <= n <= 4:
            cols = self._bullets_to_three_cols(contents[:3])
            self._log_layout(sn, 'three_cols_content_layout', {'title': title, 'cols': cols})
            return mgr.three_cols_content_layout(title, cols)
        if layout_hint == 'grid_2x2_layout' and 4 <= n <= 5:
            cells = self._bullets_to_grid_cells(contents[:4])
            self._log_layout(sn, 'grid_2x2_layout', {'title': title, 'cells': cells})
            return mgr.grid_2x2_layout(title, cells)
        if layout_hint == 'steps_horizontal_layout' and 3 <= n <= 5:
            steps = self._bullets_to_steps(contents)
            self._log_layout(sn, 'steps_horizontal_layout', {'title': title, 'steps': steps})
            return mgr.steps_horizontal_layout(title, steps)
        if layout_hint == 'two_cols_content_layout' and n >= 4:
            self._log_layout(sn, 'two_cols_content_layout', {'title': title, 'content': contents})
            return mgr.two_cols_content_layout(title, contents)
        if layout_hint == 'only_content':
            return None  # Let the random picker decide; fall back to only_content if nothing fits
        if layout_hint == 'research_question_layout' and n >= 2:
            main_q = contents[0]
            sub_qs = contents[1:4]
            self._log_layout(sn, 'research_question_layout', {'title': title, 'main_question': main_q, 'sub_questions': sub_qs})
            return mgr.research_question_layout(title, main_q, sub_qs)
        if layout_hint == 'quote_layout' and n >= 1:
            q = contents[0]
            attr = contents[1] if n >= 2 else ''
            self._log_layout(sn, 'quote_layout', {'quote': q, 'attribution': attr})
            return mgr.quote_layout(q, attr)
        if layout_hint == 'section_divider_layout':
            _m = re.match(r'^(\d+)[\.\):]?\s*', title)
            sec_num = _m.group(1).zfill(2) if _m else ''
            clean_title = re.sub(r'^\d+[\.\):]?\s*', '', title).strip() or title
            self._log_layout(sn, 'section_divider_layout', {'title': clean_title, 'section_number': sec_num})
            return mgr.section_divider_layout(clean_title, section_number=sec_num)
        if layout_hint == 'editorial_layout' and n >= 1:
            lede = ' '.join(contents)
            self._log_layout(sn, 'editorial_layout', {'title': title, 'lede': lede})
            return mgr.editorial_layout(title, lede)
        if layout_hint == 'agenda_layout' and n >= 2:
            items = self._bullets_to_agenda_items(contents)
            self._log_layout(sn, 'agenda_layout', {'title': title, 'items': items})
            return mgr.agenda_layout(title, items)
        if layout_hint == 'stats_cards_layout' and 2 <= n <= 4:
            stats = [{'value': '', 'label': b, 'body': ''} for b in contents[:4]]
            self._log_layout(sn, 'stats_cards_layout', {'title': title, 'stats': stats})
            return mgr.stats_cards_layout(title, stats)
        if layout_hint == 'nested_bullets_layout' and n >= 1:
            items = [{'text': b, 'sub': []} for b in contents]
            self._log_layout(sn, 'nested_bullets_layout', {'title': title, 'items': items})
            return mgr.nested_bullets_layout(title, items)
        if layout_hint == 'pricing_cards_layout' and 2 <= n <= 4:
            cards = self._bullets_to_pricing_cards(contents)
            self._log_layout(sn, 'pricing_cards_layout', {'title': title, 'cards': cards})
            return mgr.pricing_cards_layout(title, cards)
        return None

    def _build_content_slide(self, slide_entry: dict) -> str:
        slide_info = slide_entry['slide']
        num = int(slide_info['slide_number'])
        title = slide_info['slide_title']
        stype = slide_info['slide_type']
        raw_contents = slide_entry.get('content', [])
        contents = self._normalise_contents(raw_contents)
        
        if isinstance(contents, list):
            cleaned_contents = []
            clean_title = re.sub(r'^\s*\d+(?:\.\d+)*\.\s*', '', title).strip().lower()
            for c in contents:
                clean_c = re.sub(r'^\s*\d+(?:\.\d+)*\.\s*', '', str(c)).strip().lower()
                if clean_c == clean_title:
                    continue
                cleaned_contents.append(c)
            contents = cleaned_contents
            
        mgr = self._mgr
        self._slide_counter += 1
        sn = self._slide_counter
        self._content_idx += 1
        has_images = num in self._image_dist
        has_table_dist = num in self._table_dist
        if stype == 'comparison':
            table_obj = slide_info.get('table') or {}
            table_md = table_obj.get('table_markdown') or ''
            if not table_md and isinstance(contents, str) and '|' in contents:
                table_md = contents
            if table_md and '|' in table_md:
                if contents and isinstance(contents, list) and len(contents) >= 1:
                    self._log_layout(sn, 'table_above_layout', {'title': title, 'table_markdown': table_md, 'content': contents})
                    return mgr.table_above_layout(title, table_md, contents)
                self._log_layout(sn, 'comparison_layout', {'title': title, 'table_markdown': table_md})
                return mgr.comparison_layout(title, table_md)
            self._log_layout(sn, 'only_content', {'title': title, 'content': contents})
            return mgr.only_content(title, contents if isinstance(contents, list) else [str(contents)])
        if stype == 'two_sub_contents':
            if isinstance(contents, list) and len(contents) <= 2:
                self._log_layout(sn, 'only_content', {'title': title, 'content': contents})
                return mgr.only_content(title, contents)
            if isinstance(contents, dict):
                keys = list(contents.keys())
                sub_title_1 = keys[0] if len(keys) > 0 else 'Part 1'
                sub_title_2 = keys[1] if len(keys) > 1 else 'Part 2'
                sub_content_1 = contents.get(sub_title_1, [])
                sub_content_2 = contents.get(sub_title_2, [])
            else:
                mid = (len(contents) + 1) // 2
                (sub_title_1, sub_title_2) = ('Part 1', 'Part 2')
                sub_content_1 = contents[:mid]
                sub_content_2 = contents[mid:]
            if random.random() < 0.30:
                _left  = (sub_content_1 if isinstance(sub_content_1, list) else [str(sub_content_1)])[:4]
                _right = (sub_content_2 if isinstance(sub_content_2, list) else [str(sub_content_2)])[:4]
                _args2 = {'left_title': sub_title_1, 'left_items': _left, 'right_title': sub_title_2, 'right_items': _right}
                self._log_layout(sn, 'split_contrast_layout', _args2)
                return mgr.split_contrast_layout(sub_title_1, _left, sub_title_2, _right)
            self._log_layout(sn, 'two_contents_in_a_slide_layout', {'title': title, 'sub_title_1': sub_title_1, 'sub_title_2': sub_title_2, 'sub_content_1': sub_content_1, 'sub_content_2': sub_content_2})
            return mgr.two_contents_in_a_slide_layout(title, sub_title_1, sub_title_2, sub_content_1, sub_content_2)
        if stype == 'have_formula':
            latex = self._sanitise_latex(slide_info.get('latex_block_formula') or '')
            _args = {'title': title, 'latex_formula_block': latex, 'content': contents}
            if random.random() < 0.5:
                self._log_layout(sn, 'formula_top_layout', _args)
                return mgr.formula_top_layout(title, latex, contents)
            else:
                self._log_layout(sn, 'formula_below_layout', _args)
                return mgr.formula_below_layout(title, latex, contents)
        if stype == 'have_table':
            table_obj = slide_info.get('table') or {}
            table_caption = table_obj.get('table_caption', None)
            if has_table_dist:
                image_table_path = self._table_dist[num].get('image_table_path')
                if image_table_path and image_table_path != 'None':
                    image_url = self._to_assets_path(image_table_path)
                    ratio = self._img_aspect_ratio(image_table_path)
                    image_width = '90%' if ratio >= 1.0 else '60%'
                    self._log_layout(sn, 'image_above_layout', {'title': title, 'content': [], 'img_path': image_url, 'image_width': image_width, 'caption': table_caption})
                    return mgr.image_above_layout(title, [], image_url, image_width=image_width, caption=table_caption)
            # Prioritise the markdown table over images — images on have_table slides are secondary
            table_md = table_obj.get('table_markdown') or ''
            if table_md and '|' in table_md:
                if contents and isinstance(contents, list) and len(contents) >= 1:
                    self._log_layout(sn, 'table_above_layout', {'title': title, 'table_markdown': table_md, 'content': contents})
                    return mgr.table_above_layout(title, table_md, contents)
                self._log_layout(sn, 'comparison_layout', {'title': title, 'table_markdown': table_md})
                return mgr.comparison_layout(title, table_md)
            if has_images:
                img_entries = self._image_dist[num]
                if len(img_entries) >= 2:
                    img1 = img_entries[0]
                    img2 = img_entries[1]
                    return self._pick_image_layout(sn, title, contents, self._to_assets_path(img1['image_path']), img1['image_path'], self._to_assets_path(img2['image_path']), img2['image_path'], caption=img1.get('caption'), caption2=img2.get('caption'))
                else:
                    img = img_entries[0]
                    return self._pick_image_layout(sn, title, contents, self._to_assets_path(img['image_path']), img['image_path'], caption=img.get('caption'))
            self._log_layout(sn, 'only_content', {'title': title, 'content': contents})
            return mgr.only_content(title, contents)
        layout_hint = slide_entry.get('layout_hint')
        if has_images:
            img_entries = self._image_dist[num]
            if len(img_entries) >= 2:
                img1 = img_entries[0]
                img2 = img_entries[1]
                return self._pick_image_layout(sn, title, contents, self._to_assets_path(img1['image_path']), img1['image_path'], self._to_assets_path(img2['image_path']), img2['image_path'], caption=img1.get('caption'), caption2=img2.get('caption'))
            else:
                img = img_entries[0]
                return self._pick_image_layout(sn, title, contents, self._to_assets_path(img['image_path']), img['image_path'], caption=img.get('caption'))
        if has_table_dist:
            tbl_entry = self._table_dist[num]
            image_table_path = tbl_entry.get('image_table_path')
            if image_table_path and image_table_path != 'None':
                image_url = self._to_assets_path(image_table_path)
                table_caption = tbl_entry.get('table_caption')
                ratio = self._img_aspect_ratio(image_table_path)
                image_width = '90%' if ratio >= 1.0 else '60%'
                self._log_layout(sn, 'image_above_layout', {'title': title, 'content': [], 'img_path': image_url, 'image_width': image_width, 'caption': table_caption})
                return mgr.image_above_layout(title, [], image_url, image_width=image_width, caption=table_caption)
        if isinstance(contents, list):
            n = len(contents)
            total_chars = sum(len(s) for s in contents)
            split_ok = self._bullets_are_split_friendly(contents)
            if layout_hint:
                hinted = self._apply_layout_hint(sn, title, contents, layout_hint)
                if hinted is not None:
                    return hinted
            if n == 3:
                pick = random.random()
                if pick < 0.30 and split_ok:
                    cols = self._bullets_to_three_cols(contents)
                    self._log_layout(sn, 'three_cols_content_layout', {'title': title, 'cols': cols})
                    return mgr.three_cols_content_layout(title, cols)
                if pick < 0.55 and split_ok:
                    conc = self._bullets_to_conclusions(contents)
                    self._log_layout(sn, 'conclusion_cards_layout', {'title': title, 'conclusions': conc})
                    return mgr.conclusion_cards_layout(title, conc)
                # key_points: 55–80% chance when split-friendly; 25% when plain sentences (auto-split titles)
                if (pick < 0.80 and split_ok) or (not split_ok and pick < 0.25):
                    pts = self._bullets_to_key_points(contents)
                    self._log_layout(sn, 'key_points_layout', {'title': title, 'points': pts})
                    return mgr.key_points_layout(title, pts)

            elif n == 4:
                pick = random.random()
                if pick < 0.28 and split_ok:
                    cells = self._bullets_to_grid_cells(contents)
                    self._log_layout(sn, 'grid_2x2_layout', {'title': title, 'cells': cells})
                    return mgr.grid_2x2_layout(title, cells)
                if pick < 0.52 and split_ok:
                    conc = self._bullets_to_conclusions(contents)
                    self._log_layout(sn, 'conclusion_cards_layout', {'title': title, 'conclusions': conc})
                    return mgr.conclusion_cards_layout(title, conc)
                # key_points: 52–75% when split-friendly; 25% when plain sentences
                if (pick < 0.75 and split_ok) or (not split_ok and pick < 0.25):
                    pts = self._bullets_to_key_points(contents)
                    self._log_layout(sn, 'key_points_layout', {'title': title, 'points': pts})
                    return mgr.key_points_layout(title, pts)
                if total_chars > 420 or any(len(s) > 88 for s in contents):
                    self._log_layout(sn, 'two_cols_content_layout', {'title': title, 'content': contents})
                    return mgr.two_cols_content_layout(title, contents)
            elif 5 <= n <= 6:
                pick = random.random()
                # key_points: 0–25% when split-friendly; 0–20% when plain sentences
                if (pick < 0.25 and split_ok) or (not split_ok and pick < 0.20):
                    pts = self._bullets_to_key_points(contents)
                    self._log_layout(sn, 'key_points_layout', {'title': title, 'points': pts})
                    return mgr.key_points_layout(title, pts)
                if pick < 0.47 and split_ok:
                    conc = self._bullets_to_conclusions(contents)
                    self._log_layout(sn, 'numbered_conclusions_layout', {'title': title, 'conclusions': conc})
                    return mgr.numbered_conclusions_layout(title, conc)
                if pick < 0.62 and split_ok:
                    steps = self._bullets_to_steps(contents)
                    self._log_layout(sn, 'steps_horizontal_layout', {'title': title, 'steps': steps})
                    return mgr.steps_horizontal_layout(title, steps)
                if total_chars > 420 or any(len(s) > 88 for s in contents):
                    self._log_layout(sn, 'two_cols_content_layout', {'title': title, 'content': contents})
                    return mgr.two_cols_content_layout(title, contents)
            elif n >= 4 and (total_chars > 420 or any(len(s) > 88 for s in contents)):
                self._log_layout(sn, 'two_cols_content_layout', {'title': title, 'content': contents})
                return mgr.two_cols_content_layout(title, contents)
        self._log_layout(sn, 'only_content', {'title': title, 'content': contents})
        return mgr.only_content(title, contents)

    def _resolve_institution(self) -> str:
        if self.institution:
            return self.institution
        try:
            ctx_path = Config.CONTEXT_DIR / f'{self.lecture_id}.json'
            if ctx_path.exists():
                ctx = json.loads(ctx_path.read_text(encoding='utf-8'))
                full_text = ctx.get('text_content', {}).get('markdown', '')
                return _extract_institution(full_text)
        except Exception:
            pass
        return ''

    def build(self) -> str:
        self._clear_public_assets()
        self._copy_assets()
        self._slide_counter = 1
        self._content_idx = 0
        self._page_counter = 0
        self._deck_sections = []
        self._resolved_institution = self._resolve_institution()
        short_title = self._summarise_title(self.lecture_title)
        self._short_title = short_title
        cover = self._mgr.config_and_greeting_slide(short_title=short_title, institution=self._resolved_institution)
        self._log_layout(self._slide_counter, 'config_and_greeting_slide', {
            'short_title': short_title,
            'institution': self._resolved_institution,
        })
        self._deck_sections.append(cover)
        toc_items = self._parse_outline()
        toc_slide = self._inject_chrome(self._build_toc_slide(toc_items))
        self._deck_sections.append(toc_slide)
        for slide_entry in self.slides:
            self._deck_sections.append(self._inject_chrome(self._build_content_slide(slide_entry)))
        self._slide_counter += 1
        institution = self._resolved_institution
        ack = 'The authors acknowledge all contributors and reviewers of this work.' if institution else ''
        end_slide = self._mgr.end_layout(end_text='Thank you', institution=institution, acknowledgment=ack)
        self._log_layout(self._slide_counter, 'end_layout', {'end_text': 'Thank you', 'institution': institution})
        self._deck_sections.append(end_slide)
        self._deck_dir.mkdir(parents=True, exist_ok=True)

        # ── Legacy DOM deck output — DISABLED. We now ship only the Fabric canvas
        #    editor (as {id}.html) + the .pptx. Kept for reference / possible reuse. ──
        # html_doc = self._mgr.build_html_document(sections=self._deck_sections, page_title=self.lecture_title)
        # html_doc = self._make_self_contained(html_doc)
        # with open(self._deck_dir / f'{self.lecture_id}.html', 'w', encoding='utf-8') as f:
        #     f.write(html_doc)

        # ── Sole HTML output: Fabric.js canvas editor → {id}.html ──
        _slide_specs = [{'layout': e['layout_function_name'], **e['args']} for e in self._layout_log]
        _spec_json = build_slide_spec_json(
            _slide_specs, title=self._mgr.title, author=self._mgr.author, theme=self._mgr.theme,
        )
        html_doc = self._make_self_contained(build_fabric_html(_spec_json, page_title=self.lecture_title))
        out_path = self._deck_dir / f'{self.lecture_id}.html'
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html_doc)
        logging.info(f'[deck_html] Canvas editor → {out_path}')
        log_path = self.lecture_json_path.parent / f'{self.lecture_id}_layout_distribution.json'
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(self._layout_log, f, ensure_ascii=False, indent=2)
        self._lecture.setdefault('metadata', {})['presentation_date'] = self.date
        if self.speaker_information:
            self._lecture['metadata']['speaker_information'] = self.speaker_information
        if institution:
            self._lecture['metadata']['institution'] = institution
        with open(self.lecture_json_path, 'w', encoding='utf-8') as f:
            json.dump(self._lecture, f, ensure_ascii=False, indent=2)
        return html_doc

    def _make_self_contained(self, html_doc: str) -> str:
        """Inline all local images as base64 data URIs and embed deck-stage.js."""
        import base64
        MIME = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.png': 'image/png',  '.gif': 'image/gif',
            '.webp': 'image/webp', '.svg': 'image/svg+xml',
        }
        # Inline the runtime engine FIRST, from the canonical source file, so the
        # generic src="..."→base64 pass below can never hijack `src="deck-stage.js"`
        # (which happens whenever a stale deck-stage.js copy lives in the output dir).
        js_path = _PROJECT_ROOT / 'src' / 'generator' / 'deck-stage.js'
        if js_path.exists():
            js_content = js_path.read_text(encoding='utf-8')
            html_doc = html_doc.replace(
                '<script src="deck-stage.js"></script>',
                f'<script>{js_content}</script>',
            )
        def _repl(m: re.Match) -> str:
            src = m.group(1)
            if src.startswith('data:') or src.startswith('http'):
                return m.group(0)
            p = self._deck_dir / src
            if not p.exists():
                return m.group(0)
            mime = MIME.get(p.suffix.lower(), 'image/jpeg')
            encoded = base64.b64encode(p.read_bytes()).decode('ascii')
            return f'src="data:{mime};base64,{encoded}"'
        html_doc = re.sub(r'src="([^"]+)"', _repl, html_doc)

        # Embed image path values inside DECK_SPEC JSON (Fabric editor) — fixes CORS on
        # file://. Covers single (img_path) and two-image layouts (img1_path/img2_path).
        def _repl_img_path(m: re.Match) -> str:
            key, src = m.group(1), m.group(2)
            if src.startswith('data:') or src.startswith('http'):
                return m.group(0)
            p = self._deck_dir / src
            if not p.exists():
                return m.group(0)
            mime = MIME.get(p.suffix.lower(), 'image/jpeg')
            encoded = base64.b64encode(p.read_bytes()).decode('ascii')
            return f'"{key}": "data:{mime};base64,{encoded}"'
        html_doc = re.sub(r'"(img_path|img1_path|img2_path)":\s*"([^"]+)"', _repl_img_path, html_doc)
        return html_doc

    def _summarise_title(self, title: str) -> str:
        text = re.sub(r'[_:;|]+', ' ', str(title or '')).strip()
        return re.sub(r'\s+', ' ', text)

    def _inject_chrome(self, html):
        self._page_counter += 1
        if isinstance(html, dict):
            # JSON / Fabric.js format: chrome is handled by the JS renderer
            return html
        chrome = DeckHTMLLayoutManager._chrome(
            self._short_title, self._page_counter, self.date, self.speaker_information or "",
            institution=self._resolved_institution,
        )
        blobs = '<div class="blobs"></div>'
        return html.replace('</section>', blobs + chrome + '</section>', 1)
