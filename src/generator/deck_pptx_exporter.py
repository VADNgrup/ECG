from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import random as _random
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from src.generator import pptx_pipeline
from src.generator.formula_render import to_mathtext
from src.generator.pptx_pattern_engines import (_find_duplicate_item_refs, _find_duplicate_words,
                                                _find_image_contract_violation, _find_implausible_font_size,
                                                _find_invalid_preset, _find_low_contrast, _find_missing_text_color,
                                                _find_missing_text_size, _find_offcanvas, _find_overlaps,
                                                _find_text_overflow, _image_shape)
from src.generator.pptx_patterns import (_extract_slide_items, _SAFE_ACCENT_SHAPES, PatternValidationError,
                                         render_narrative)
from src.generator.pptx_theme_library import SAFE_FONTS as _SYSTEM_SAFE_FONTS, THEME_LIBRARY, THEME_LIBRARY_BY_NAME
from src.rag.asset_validation import validate_formula
from src.utils.config import Config
from src.utils.llm import chat, generate_image
from src.utils.parse_llm_response import parse_json_response

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GEN_JS = Path(__file__).parent / 'deck_pptx_gen.js'

                                                                                

def _css_var(style: str, name: str) -> str:
    m = re.search(r'--' + re.escape(name) + r'\s*:\s*([^;]+)', style)
    return m.group(1).strip() if m else ''

def _first_hex(value: str) -> str:
    m = re.search(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', value)
    if not m:
        return ''
    c = m.group(1)
    if len(c) == 3:
        c = c[0] * 2 + c[1] * 2 + c[2] * 2
    return c.upper()

def _first_font(value: str) -> str:
    m = re.search(r"'([^']+)'", value)
    if m:
        return m.group(1)
    return value.split(',')[0].strip().strip('"\'')

def _parse_gradient_stops(css_value: str) -> list[tuple[str, float]]:
    stops = []
    for m in re.finditer(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\s+(\d+(?:\.\d+)?)%', css_value):
        c = m.group(1)
        if len(c) == 3:
            c = c[0] * 2 + c[1] * 2 + c[2] * 2
        stops.append((c.upper(), float(m.group(2)) / 100.0))
    return stops

def _render_gradient_bg_png(stops: list[tuple[str, float]], out_path: Path,
                             w: int = 640, h: int = 360) -> bool:
    try:

        def h2f(hx: str) -> list[float]:
            hx = hx.lstrip('#')
            return [int(hx[i:i + 2], 16) for i in (0, 2, 4)]

        x = np.linspace(0, 1, w)
        y = np.linspace(0, 1, h)
        xx, yy = np.meshgrid(x, y)
        t = np.clip((xx + yy) / 2.0, stops[0][1], stops[-1][1])

        positions = [s[1] for s in stops]
        out = np.zeros((h, w, 3), dtype=np.uint8)
        for ch in range(3):
            vals = [h2f(s[0])[ch] for s in stops]
            out[:, :, ch] = np.clip(
                np.interp(t.ravel(), positions, vals).reshape(h, w), 0, 255
            ).astype(np.uint8)

        Image.fromarray(out, 'RGB').save(str(out_path), 'PNG', optimize=True)
        return True
    except Exception as e:
        print(f'[gradient_bg] {e}')
        return False

def _rgba_to_hex(value: str) -> str:
    m = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)', value)
    if m:
        return '{:02X}{:02X}{:02X}'.format(*[int(x) for x in m.groups()])
    return ''

def _parse_theme(deck_style: str) -> dict:
    accent     = _first_hex(_css_var(deck_style, 'accent'))
    cover_bg   = _css_var(deck_style, 'cover-bg')
    _stops     = _parse_gradient_stops(cover_bg)
    bg_dark    = _stops[-1][0] if _stops else _first_hex(cover_bg)
    panel_text = _first_hex(_css_var(deck_style, 'panel-text'))
    dim_css    = _css_var(deck_style, 'dim')
    dim_dark   = _first_hex(dim_css) or _rgba_to_hex(dim_css) or 'AAAAAA'
    text_dark  = _first_hex(_css_var(deck_style, 'text')) or 'FFFFFF'
    bg_light   = _first_hex(_css_var(deck_style, 'light-bg')) or 'F5F3EE'
    return {
        'accent':        accent     or 'FFCC33',
        'bg_dark':       bg_dark    or '500014',
        'grad_stops':    _parse_gradient_stops(cover_bg),
        'bg_light':      bg_light,
        'text_dark':     text_dark,
        'dim_dark':      dim_dark,
        'text_light':    panel_text or '0B0D12',
        'font_display':  _first_font(_css_var(deck_style, 'font-display')) or 'Playfair Display',
        'font_body':     _first_font(_css_var(deck_style, 'font-body'))    or 'Source Sans 3',
        'font_mono':     _first_font(_css_var(deck_style, 'font-mono'))    or 'IBM Plex Mono',
        'c1':            _first_hex(_css_var(deck_style, 'c1')) or '5b9bd5',
        'c2':            _first_hex(_css_var(deck_style, 'c2')) or 'e07b6a',
        'c3':            _first_hex(_css_var(deck_style, 'c3')) or '7b68c8',
        'c4':            _first_hex(_css_var(deck_style, 'c4')) or 'f0a050',
    }

                                                                                

def export_pptx(lecture_json_path, output_path, speaker=''):
    return pptx_pipeline.export_native(lecture_json_path, output_path, speaker=speaker)


def _toc_items(toc_content: list) -> list[dict]:
    items = []
    for i, item in enumerate(toc_content):
        if isinstance(item, dict):
            items.append({'n': str(item.get('n', i + 1)).zfill(2), 't': item.get('title', '')})
        else:
            m = re.match(r'^(\d+)\.\s*(.*)', str(item))
            n = m.group(1).zfill(2) if m else str(i + 1).zfill(2)
            t = m.group(2).strip() if m else str(item)
            items.append({'n': n, 't': t})
    return items


def _toc_cards(toc_content: list) -> list[dict]:
    cards = []
    for i, item in enumerate(toc_content):
        if isinstance(item, dict):
            cards.append({
                'n': str(item.get('n', i + 1)).zfill(2),
                'title': item.get('title', ''),
                'desc': item.get('description', ''),
            })
        else:
            m = re.match(r'^(\d+)\.\s*(.*)', str(item))
            cards.append({
                'n': m.group(1).zfill(2) if m else str(i + 1).zfill(2),
                'title': m.group(2).strip() if m else str(item),
                'desc': '',
            })
    return cards


def _toc_items_described(toc_content: list) -> list[dict]:
    items = []
    for i, item in enumerate(toc_content):
        if isinstance(item, dict):
            items.append({
                'n':   str(item.get('n', item.get('num', i + 1))).zfill(2),
                't':   item.get('title', item.get('t', '')),
                'd':   item.get('description', item.get('d', item.get('body', ''))),
                'dur': str(item.get('duration', item.get('dur', ''))),
            })
        else:
            m = re.match(r'^(\d+)\.\s*(.*)', str(item))
            items.append({
                'n':   m.group(1).zfill(2) if m else str(i + 1).zfill(2),
                't':   m.group(2).strip() if m else str(item),
                'd':   '',
                'dur': '',
            })
    return items


def _resolve_imgs(paths: list, output_dir: Path) -> list[str]:
    result = []
    for p in paths:
        if not p:
            continue
        abs_p = (output_dir / p).resolve()
        if abs_p.exists():
            result.append(str(abs_p))
    return result


def _formula_fields(latex: str, light: bool, output_dir: Path) -> dict:
    """The slide's formulas (separated by a blank line) as readable Unicode math (formula_text), one formula's lines
    after another with a blank line between two; the deck draws them stacked as text in the theme's colour."""
    blocks = []
    for part in [p for p in re.split(r"\n\s*\n", latex or "") if p.strip()]:
        validation = validate_formula(part)
        if not validation.get('valid'):
            raise ValueError(f"invalid_formula_asset: {validation.get('validation_reason')}")
        text = validation['normalized_content']
        blocks.append('\n'.join(to_mathtext(text)) or text)
    return {'formula_latex': latex or '', 'formula_text': '\n\n'.join(blocks), 'formula_image_path': ''}


# Builders that draw the slide's text as one list: they receive the two-level groups and render sub-bullets.
_LIST_TYPES = {'bullets', 'imgleft', 'imgright', 'imgabove', 'imgbelow', 'twoimgleft', 'twoimgright',
               'twoimgabove', 'twoimgbelow', 'formula', 'tblabove', 'codeside', 'codeabove'}


def _layout_log_to_slide(entry: dict, output_dir: Path, page_num: int) -> dict:
    slide = _native_slide(entry, output_dir, page_num)
    groups = (entry.get('args') or {}).get('groups')
    if groups and slide.get('type') in _LIST_TYPES:
        slide['groups'] = groups
    return slide


def _native_slide(entry: dict, output_dir: Path, page_num: int) -> dict:
    fn = entry['layout_function_name']
    args = entry.get('args', {})
    light = bool(args.get('light', False))

    if fn == 'config_and_greeting_slide':
        return {'type': 'cover', 'title': args.get('short_title', ''), 'page_num': page_num}

    elif fn == 'end_layout':
        return {'type': 'end', 'title': args.get('end_text') or args.get('title') or 'Thank you',
                'institution': args.get('institution', ''), 'author': args.get('author', ''),
                'acknowledgment': args.get('acknowledgment', ''), 'page_num': page_num}

    elif fn == 'end_centered_layout':
        return {'type': 'end_credits', 'title': args.get('end_text') or args.get('title') or 'Thank you',
                'institution': args.get('institution', ''), 'author': args.get('author', ''),
                'acknowledgment': args.get('acknowledgment', ''), 'page_num': page_num}

    elif fn in ('cover_editorial_layout', 'cover_minimal_layout', 'cover_hero_layout'):
        t = {'cover_editorial_layout': 'cover_editorial', 'cover_minimal_layout': 'cover_minimal',
             'cover_hero_layout': 'cover_hero'}[fn]
        return {'type': t, 'title': args.get('title') or args.get('short_title', ''),
                'short_title': args.get('short_title', ''), 'institution': args.get('institution', ''),
                'author': args.get('author', ''), 'date': args.get('date', ''), 'page_num': page_num}

    elif fn == 'end_contact_layout':
        return {'type': 'end_contact', 'title': args.get('end_text') or 'Thank you',
                'institution': args.get('institution', ''), 'author': args.get('author', ''),
                'acknowledgment': args.get('acknowledgment', ''), 'page_num': page_num}

    elif fn == 'end_gradient_layout':
        return {'type': 'end_gradient', 'title': args.get('end_text') or 'Thank you',
                'institution': args.get('institution', ''), 'author': args.get('author', ''),
                'page_num': page_num}

    elif fn == 'toc_sidebar_layout':
        return {'type': 'toc_sidebar', 'title': 'Contents',
                'toc_items': _toc_items(args.get('toc_content', [])), 'is_light': light, 'page_num': page_num}

    elif fn == 'toc_timeline_layout':
        return {'type': 'toc_timeline', 'title': args.get('heading', 'Contents'),
                'toc_items': _toc_items(args.get('toc_content', [])), 'is_light': light, 'page_num': page_num}

    elif fn == 'big_statement_layout':
        content = args.get('content', []) or []
        return {'type': 'bigstatement', 'title': args.get('title', ''),
                'statement': content[0] if content else args.get('title', ''),
                'bullets': content[1:], 'is_light': light, 'page_num': page_num}

    elif fn == 'definition_callout_layout':
        content = args.get('content', []) or []
        return {'type': 'definition', 'title': args.get('title', ''),
                'term': args.get('title', ''), 'bullets': content, 'is_light': light, 'page_num': page_num}

    elif fn == 'toc_layout':
        items = _toc_items(args.get('toc_content', []))
        # 'title' is only consumed by the pattern composer path; buildToc hardcodes "Outline" itself.
        return {'type': 'toc', 'title': 'Contents', 'toc_items': items, 'is_light': light, 'page_num': page_num}

    elif fn == 'toc_described_layout':
        heading = args.get('heading', "What we'll cover")
        items = _toc_items_described(args.get('toc_content', []))
        return {
            'type': 'toc_described', 'title': heading, 'toc_items': items,
            'heading': heading,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'toc_vertical_layout':
        items = _toc_items(args.get('toc_content', []))
        return {'type': 'toc_vertical', 'title': 'Contents', 'toc_items': items, 'is_light': light, 'page_num': page_num}

    elif fn == 'toc_cards_layout':
        heading = args.get('heading', 'Outline')
        cards = _toc_cards(args.get('toc_content', []))
        return {
            'type': 'toc_cards', 'title': heading, 'cards': cards,
            'heading': heading,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'only_content':
        return {
            'type': 'bullets', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_cols_content_layout':
        content = args.get('content', [])
        mid = (len(content) + 1) // 2
        return {
            'type': 'twocols', 'title': args.get('title', ''),
            'cols': [content[:mid], content[mid:]], 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'key_points_layout':
        pts = [
            {'ix': p.get('icon', ''), 'title': p.get('title', ''), 'body': p.get('body', ''),
             'items': p.get('bullets', [])}
            for p in args.get('points', [])
        ]
        return {
            'type': 'keypoints', 'title': args.get('title', ''),
            'points': pts, 'is_light': light, 'page_num': page_num,
        }

    elif fn in ('comparison_layout', 'data_table_layout'):
        return {
            'type': 'cmptable', 'title': args.get('title', ''),
            'table': {'table_markdown': args.get('table_markdown', '')},
            'parsed_table': args.get('parsed_table'),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'table_above_layout':
        return {
            'type': 'tblabove', 'title': args.get('title', ''),
            'table': {'table_markdown': args.get('table_markdown', '')},
            'parsed_table': args.get('parsed_table'),
            'bullets': args.get('content', []),
            'is_light': light, 'page_num': page_num,
        }

    elif fn in ('code_beside_layout', 'code_above_layout'):
        return {
            'type': 'codeside' if fn == 'code_beside_layout' else 'codeabove', 'title': args.get('title', ''),
            'code': args.get('code', ''), 'language': args.get('code_language', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn in ('formula_top_layout', 'formula_below_layout'):
        return {
            'type': 'formula', 'title': args.get('title', ''),
            **_formula_fields(args.get('latex_formula_block', ''), light, output_dir),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'formula_hero_layout':
        return {
            'type': 'formula_hero', 'title': args.get('title', ''),
            **_formula_fields(args.get('latex_formula_block', ''), light, output_dir),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'formula_annotated_layout':
        return {
            'type': 'formula_annot', 'title': args.get('title', ''),
            **_formula_fields(args.get('latex_formula_block', ''), light, output_dir),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'table_highlight_layout':
        return {
            'type': 'table_highlight', 'title': args.get('title', ''),
            'table': {'table_markdown': args.get('table_markdown', '')},
            'parsed_table': args.get('parsed_table'),
            'bullets': args.get('content', []), 'caption': args.get('caption', ''),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'image_comparison_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        return {
            'type': 'imgcompare', 'title': args.get('title', ''),
            'images': imgs, 'captions': [args.get('caption1', ''), args.get('caption2', '')],
            'is_light': light, 'page_num': page_num,
        }

    elif fn in ('chart_stat_layout', 'kpi_cards_layout', 'big_stat_layout'):
        _t = {'chart_stat_layout': 'chart_stat', 'kpi_cards_layout': 'kpi_cards',
              'big_stat_layout': 'big_stat'}[fn]
        return {
            'type': _t, 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'callout': args.get('callout', ''),
            'is_light': light, 'page_num': page_num,
        }

    elif fn in ('image_stat_hero_layout', 'image_quote_hero_layout'):
        _t = 'img_stat_hero' if fn == 'image_stat_hero_layout' else 'img_quote_hero'
        bg = _resolve_imgs([args.get('bg_image_path')], output_dir)
        return {
            'type': _t, 'title': args.get('title', ''),
            'bg_image_path': bg[0] if bg else '', 'bullets': args.get('content', []),
            'quote': args.get('quote', ''), 'credit': args.get('credit', ''),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'image_left_layout':
        imgs = _resolve_imgs([args.get('img_path')], output_dir)
        return {
            'type': 'imgleft', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'caption': args.get('caption', ''), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'image_right_layout':
        imgs = _resolve_imgs([args.get('img_path')], output_dir)
        return {
            'type': 'imgright', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'caption': args.get('caption', ''), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'image_above_layout':
        imgs = _resolve_imgs([args.get('img_path')], output_dir)
        return {
            'type': 'imgabove', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'caption': args.get('caption', ''), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'image_below_layout':
        imgs = _resolve_imgs([args.get('img_path')], output_dir)
        return {
            'type': 'imgbelow', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'caption': args.get('caption', ''), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_right_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        _caps = [args.get('caption1', ''), args.get('caption2', '')]
        return {
            'captions': _caps,
            'type': 'twoimgright', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_above_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        _caps = [args.get('caption1', ''), args.get('caption2', '')]
        return {
            'captions': _caps,
            'type': 'twoimgabove', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_below_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        _caps = [args.get('caption1', ''), args.get('caption2', '')]
        return {
            'captions': _caps,
            'type': 'twoimgbelow', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_left_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        _caps = [args.get('caption1', ''), args.get('caption2', '')]
        return {
            'captions': _caps,
            'type': 'twoimgleft', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'conclusion_cards_layout':
        conc = args.get('conclusions', [])
        cards = [
            {'num': str(i + 1).zfill(2), 'heading': c.get('heading', ''), 'body': c.get('body', ''),
             'items': c.get('items', [])}
            for i, c in enumerate(conc)
        ]
        return {
            'type': 'conclcards', 'title': args.get('title', ''),
            'cards': cards, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'numbered_conclusions_layout':
        conc = args.get('conclusions', [])
        rows = [
            {'n': str(i + 1).zfill(2), 'heading': c.get('heading', ''), 'body': c.get('body', ''),
             'items': c.get('items', [])}
            for i, c in enumerate(conc)
        ]
        return {
            'type': 'numconcl', 'title': args.get('title', ''),
            'rows': rows, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'three_cols_content_layout':
        raw = args.get('cols', [])
        cols = [
            {'tag': c.get('icon', ''), 'title': c.get('title', ''), 'body': c.get('body', ''), 'bullets': c.get('bullets', [])}
            for c in raw
        ]
        return {
            'type': 'threecol', 'title': args.get('title', ''),
            'cols': cols, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'grid_2x2_layout':
        raw = args.get('cells', [])
        cells = [{'title': c.get('title', ''), 'body': c.get('body', ''), 'items': c.get('items', [])} for c in raw]
        return {
            'type': 'grid2x2', 'title': args.get('title', ''),
            'cells': cells, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'steps_horizontal_layout':
        raw = args.get('steps', [])
        steps = [
            {'num': str(i + 1), 'title': s.get('title', ''), 'body': s.get('body', ''), 'items': s.get('items', [])}
            for i, s in enumerate(raw)
        ]
        return {
            'type': 'steps', 'title': args.get('title', ''),
            'steps': steps, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'research_question_layout':
        main_q = args.get('main_question', '')
        sub_qs = [
            {'lbl': f'Sub-Q {str(i + 1).zfill(2)}', 'q': str(q)}
            for i, q in enumerate(args.get('sub_questions', []))
        ]
        return {
            'type': 'rquestion', 'title': args.get('title', ''),
            'main_q': main_q, 'sub_qs': sub_qs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'quote_layout':
        return {
            'type': 'quote',
            'quote': args.get('quote', ''), 'attribution': args.get('attribution', ''),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'section_divider_layout':
        # fixed variant per heading level; falls back to page-based rotation if level is unknown
        _level = args.get('level')
        _LEVEL_VARIANT = {1: 0, 2: 1, 3: 2}
        variant = _LEVEL_VARIANT.get(_level, 3) if _level else page_num % 4
        return {
            'type': 'section-divider', 'title': args.get('title', ''),
            'section_num': args.get('section_number', ''), 'lead': args.get('lead', ''),
            'variant': variant, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'editorial_layout':
        return {
            'type': 'editorial',
            'title':             args.get('title', ''),
            'lede':              args.get('lede', ''),
            'pull_quote':        args.get('pull_quote', ''),
            'pull_attribution':  args.get('pull_attribution', ''),
            'eyebrow':           args.get('eyebrow', ''),
            'meta':              args.get('meta', []),
            'footline_left':     args.get('footline_left', ''),
            'footline_right':    args.get('footline_right', ''),
            'is_light': True, 'page_num': page_num,
        }

    elif fn == 'agenda_layout':
        return {
            'type': 'agenda', 'title': args.get('title', ''),
            'items': args.get('items', []),
            'variant': page_num % 3, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'timeline_vertical_layout':
        return {
            'type': 'timeline', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'rows': args.get('timeline_rows', []),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'spotlight_layout':
        return {
            'type': 'spotlight', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'icon_matrix_layout':
        return {
            'type': 'iconmatrix', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'comparison_columns_layout':
        return {
            'type': 'comparison2', 'title': args.get('title', ''),
            'sides': args.get('sides', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'comparison_matrix_layout':
        return {
            'type': 'cmpmatrix', 'title': args.get('title', ''),
            'matrix': args.get('matrix', {}), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'concept_breakdown_layout':
        return {
            'type': 'concepts', 'title': args.get('title', ''),
            'concepts': args.get('concepts', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn in ('highlight_list_layout', 'takeaway_panel_layout'):
        _t = 'highlight_list' if fn == 'highlight_list_layout' else 'takeaway_panel'
        return {
            'type': _t, 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn in ('pyramid_layout', 'funnel_layout', 'cycle_layout', 'hub_spoke_layout',
                'checklist_layout'):
        _t = {'pyramid_layout': 'pyramid', 'funnel_layout': 'funnel', 'cycle_layout': 'cycle',
              'hub_spoke_layout': 'hubspoke', 'checklist_layout': 'checklist'}[fn]
        return {
            'type': _t, 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'stats_cards_layout':
        return {
            'type': 'stat', 'title': args.get('title', ''),
            'stats': args.get('stats', []),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'nested_bullets_layout':
        raw = args.get('items', [])
        bullets = [item.get('text', '') if isinstance(item, dict) else str(item) for item in raw]
        return {
            'type': 'bullets', 'title': args.get('title', ''),
            'bullets': bullets, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'pricing_cards_layout':
        return {
            'type': 'pricing', 'title': args.get('title', ''),
            'cards': args.get('cards', []),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'split_contrast_layout':
        sides = [
            {'tag': '', 'title': args.get('left_title', ''), 'bullets': args.get('left_items', [])},
            {'tag': '', 'title': args.get('right_title', ''), 'bullets': args.get('right_items', [])},
        ]
        return {
            'type': 'splitcontrast',
            'sides': sides, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_contents_in_a_slide_layout':
        blocks = [
            {'subtitle': args.get('sub_title_1', ''), 'bullets': args.get('sub_content_1', [])},
            {'subtitle': args.get('sub_title_2', ''), 'bullets': args.get('sub_content_2', [])},
        ]
        return {
            'type': 'twocontents', 'title': args.get('title', ''),
            'blocks': blocks, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'cover_split_layout':
        rows = [
            {'k': r.get('k', r.get('key', '')), 'v': r.get('v', r.get('value', ''))}
            for r in (args.get('meta_rows') or [])
        ]
        return {
            'type': 'cover_split',
            'eyebrow':   args.get('eyebrow', ''),
            'meta_rows': rows,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'end_with_image_layout':
        imgs = _resolve_imgs([args.get('img_path', '')], output_dir)
        items = args.get('meta_items') or [
            {'k': 'Presented by', 'v': ''},
            {'k': 'Q&A Session',  'v': 'Open discussion'},
        ]
        return {
            'type': 'end_with_image',
            'title':      args.get('end_text', 'Thank You'),
            'images':     imgs,
            'caption':    args.get('caption', ''),
            'meta_items': items,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'end_image_hero_layout':
        imgs = _resolve_imgs([args.get('img_path', '')], output_dir)
        items = args.get('meta_items') or [
            {'k': 'Presented by', 'v': ''},
            {'k': 'Q&A Session',  'v': 'Open discussion'},
        ]
        return {
            'type': 'end_image_hero',
            'title':      args.get('end_text', 'Thank You'),
            'images':     imgs,
            'meta_items': items,
            'page_num': page_num,
        }

    # Fallback: render as bullets
    return {
        'type': 'bullets', 'title': args.get('title', ''),
        'bullets': args.get('content', args.get('items', [])),
        'is_light': light, 'page_num': page_num,
    }


_AI_IMAGE_STYLE_PROMPT = (
    'abstract technical/academic diagram or blueprint-style illustration -- schematic shapes, thin '
    'precise lines, muted palette matching the given theme colors -- NEVER photorealistic, NEVER a '
    'stock-photo or marketing-style image. Absolutely NO text, letters, words, numbers, captions, or '
    'typography of any kind rendered inside the image -- pure visual/graphic elements only (the real '
    'title/caption is added separately as PPTX text on top).'
)

def _get_or_generate_ai_image(prompt: str, document_id: str, theme: Optional[dict] = None,
                              size: str = '1024x1024') -> Optional[str]:
    """Cache-by-hash wrapper around llm.generate_image(): key = hash(prompt+size+document_id), saved to
    data/assets/{document_id}/ai_generated/{hash}.png with a JSON index mapping hash -> relative path,
    so re-exporting the same deck never re-generates (and re-pays for) an already-seen image."""
    theme_desc = {k: v for k, v in (theme or {}).items() if v}
    full_prompt = f'{prompt.strip()}\n\nStyle: {_AI_IMAGE_STYLE_PROMPT}\nTheme colors: {json.dumps(theme_desc, ensure_ascii=False)}'
    key = hashlib.sha256(f'{full_prompt}|{size}|{document_id}'.encode('utf-8')).hexdigest()[:24]

    out_dir = Config.ASSETS_DIR / document_id / 'ai_generated'
    index_path = out_dir / 'index.json'
    try:
        index = json.loads(index_path.read_text(encoding='utf-8'))
    except Exception:
        index = {}

    if key in index:
        cached_path = out_dir / index[key]
        if cached_path.exists():
            return str(cached_path)

    try:
        image_bytes = generate_image(full_prompt, size=size)
    except Exception as e:
        print(f'[pptx_layout_composer] AI image generation failed (skipping this image): {e}')
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f'{key}.png'
    (out_dir / filename).write_bytes(image_bytes)
    index[key] = filename
    try:
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    return str(out_dir / filename)


def _resolve_generated_images(shapes: list, document_id: Optional[str], theme: Optional[dict]) -> list:
    """Replace every image shape with source: "generate" with a real, cached AI-generated file path
    (calling the real Images API on a cache miss). A shape whose generation fails is dropped rather than
    handed to the renderer with a dangling image_prompt/path."""
    resolved = []
    for s in shapes:
        if s.get('type') == 'image' and s.get('source') == 'generate':
            path = _get_or_generate_ai_image(s.get('image_prompt') or '', document_id or 'unknown', theme)
            if not path:
                continue
            s = {**s, 'path': path}
            s.pop('image_prompt', None)
        resolved.append(s)
    return resolved


_DEFAULT_DESIGN_SPEC = {
    'concept': 'editorial minimalist',
    'motif': 'clean serif headlines, generous whitespace, a single confident accent color.',
    'palette': {
        'bg_dark': '1B1F27', 'bg_light': 'F7F5F0', 'accent': 'C99A3E',
        'c1': '5B9BD5', 'c2': 'E07B6A', 'c3': '7B68C8', 'c4': 'F0A050',
        'text_dark': 'FFFFFF', 'text_light': '14161B',
        'dim_dark': 'A9ADB6', 'dim_light': '6B6F78',
    },
    'fonts': {'display': 'Playfair Display', 'body': 'Source Sans 3', 'mono': 'IBM Plex Mono'},
    'default_mode': 'dark',
}

# Backwards-compatible explicit design-spec validation. Native PPTX normally selects a curated
# theme_name, while callers/tests that provide a full safe palette/font trio still work.
_SAFE_FONTS = {
    'display': list(dict.fromkeys(_SYSTEM_SAFE_FONTS['display'] +
                                 ['Playfair Display', 'Lora', 'DM Serif Display', 'Cormorant Garamond',
                                  'Fraunces', 'Bitter'])),
    'body': list(dict.fromkeys(_SYSTEM_SAFE_FONTS['body'] +
                              ['Source Sans 3', 'Inter', 'Lato', 'Work Sans', 'Nunito Sans', 'Karla'])),
    'mono': list(dict.fromkeys(_SYSTEM_SAFE_FONTS['mono'] +
                              ['IBM Plex Mono', 'Space Mono', 'JetBrains Mono', 'Roboto Mono'])),
}

_DESIGN_SPEC_VERSION = 8  # bumped: +10 themes, random theme selection
_ALLOWED_RHYTHMS = ('anchor', 'dense', 'breathing')


def _design_spec_content_brief(lecture_title: str, log_entries: list, max_slides: int = 40) -> str:
    """Zero-extra-IO subject-matter brief for the design-spec LLM call: reuses the already-loaded
    layout_log's slide titles/types -- no re-reading the source document, no extra cost."""
    lines = [f'Lecture title: {lecture_title}']
    for entry in log_entries[:max_slides]:
        args = entry.get('args', {}) or {}
        fn = entry.get('layout_function_name', '')
        label = (args.get('title') or args.get('short_title') or args.get('heading') or
                args.get('end_text') or args.get('left_title') or '')
        lines.append(f'- [{fn}] {label}'.strip())
    return '\n'.join(lines)


def _validate_and_fill_design_spec(spec: dict, n_slides: int = 0) -> dict:
    """Per-field validation with per-field fallback -- never raises, mirrors _parse_theme's philosophy:
    a malformed color/font/mode falls back individually rather than discarding the whole LLM response.
    `rhythm` is validated/padded/truncated to exactly `n_slides` entries (default 'dense' for anything
    missing/invalid) -- n_slides=0 keeps this a no-op, so existing callers/tests are unaffected.

    The palette/fonts/default_mode come from THEME_LIBRARY (see pptx_theme_library.py) via a
    `theme_name` the LLM picks -- NOT free-form hex the LLM invents -- so this loads the matching
    curated theme verbatim rather than validating individual color fields. `theme_name` unmatched or
    missing falls back to THEME_LIBRARY[0] (still a designed theme, never the old raw-hex default)."""
    spec = spec if isinstance(spec, dict) else {}
    theme_name = spec.get('theme_name')
    picked = THEME_LIBRARY_BY_NAME.get(theme_name) or THEME_LIBRARY[0]
    palette = dict(picked['palette'])
    fonts = dict(picked['fonts'])
    default_mode = picked['default_mode']
    title_style = picked.get('title_style', 'underline')
    d = _DEFAULT_DESIGN_SPEC

    explicit_palette = spec.get('palette') if isinstance(spec.get('palette'), dict) else {}
    for key, fallback in d['palette'].items():
        value = explicit_palette.get(key)
        if isinstance(value, str):
            value = value.lstrip('#').upper()
            if re.fullmatch(r'[0-9A-F]{6}', value):
                palette[key] = value
    explicit_fonts = spec.get('fonts') if isinstance(spec.get('fonts'), dict) else {}
    for role, allowed in _SAFE_FONTS.items():
        if explicit_fonts.get(role) in allowed:
            fonts[role] = explicit_fonts[role]
    if spec.get('default_mode') in ('dark', 'light'):
        default_mode = spec['default_mode']

    rhythm_in = spec.get('rhythm') if isinstance(spec.get('rhythm'), list) else []
    rhythm = [(r if r in _ALLOWED_RHYTHMS else 'dense') for r in rhythm_in[:n_slides]]
    rhythm.extend(['dense'] * (n_slides - len(rhythm)))

    return {
        'theme_name': picked['name'],
        'concept': str(spec.get('concept') or d['concept'])[:120],
        'motif': str(spec.get('motif') or d['motif'])[:300],
        'palette': palette,
        'fonts': fonts,
        'default_mode': default_mode,
        'title_style': title_style,
        'rhythm': rhythm,
    }


def _lock_bookend_rhythm(rhythm: list[str]) -> list[str]:
    """Force the first slide (cover) and last slide (closing) to 'anchor' regardless of what the LLM
    proposed -- matches ppt-master's real "Cover rhythm lock" convention (never trust the LLM alone for
    the two highest-impact bookend pages). Safe no-op for rhythm=[] or a single-slide deck."""
    if rhythm:
        rhythm[0] = 'anchor'
        rhythm[-1] = 'anchor'
    return rhythm


def _design_spec_to_theme(spec: dict) -> dict:
    """Pure mapping, no IO -- produces exactly the keys deck_pptx_gen.js already reads from `theme`,
    plus passthrough extras (motif/concept/default_mode/rhythm) that JS ignores harmlessly but that
    Python-side pattern selection uses. grad_stops is always empty: gradient backgrounds are out of scope
    for this path."""
    p, f = spec['palette'], spec['fonts']
    return {
        'name': spec.get('theme_name', ''),
        'bg_dark': p['bg_dark'], 'bg_light': p['bg_light'], 'accent': p['accent'],
        'text_dark': p['text_dark'], 'text_light': p['text_light'],
        'dim_dark': p['dim_dark'], 'dim_light': p['dim_light'],
        'font_display': f['display'], 'font_body': f['body'], 'font_mono': f['mono'],
        'c1': p['c1'], 'c2': p['c2'], 'c3': p['c3'], 'c4': p['c4'],
        'grad_stops': [],
        'motif': spec.get('motif', ''), 'concept': spec.get('concept', ''),
        'default_mode': spec.get('default_mode', 'dark'),
        'title_style': spec.get('title_style', 'underline'),
        'rhythm': spec.get('rhythm', []),
    }


def generate_deck_theme(document_id: str, lecture_title: str, content_brief: str, n_slides: int,
                        force: bool = False) -> dict:
    """PUBLIC entry point -- called ONCE per export (not per slide). ZERO LLM cost: the theme is picked at
    RANDOM from THEME_LIBRARY (seeded by document_id so it's stable per deck, varies across decks), never
    genre-matched by an LLM. `concept`/`motif`/`rhythm` used to come from an LLM call here, but JS never
    reads those fields (dead output -- verified: no `T.rhythm`/`T.motif`/`T.concept` in deck_pptx_gen.js),
    so that call was pure waste; removed. Still cached at data/assets/{document_id}/pptx_design_spec.json
    keyed by a hash of (title+brief+n_slides+version) in case that changes again later."""
    cache_path = Config.ASSETS_DIR / document_id / 'pptx_design_spec.json'
    cache_key = hashlib.sha256(
        f'{lecture_title}|{content_brief}|{n_slides}|{os.getenv("PPTX_THEME", "")}|v{_DESIGN_SPEC_VERSION}'.encode('utf-8')).hexdigest()[:24]

    if not force:
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            if cached.get('_cache_key') == cache_key and cached.get('spec'):
                return _design_spec_to_theme(cached['spec'])
        except Exception:
            pass

    chosen = os.getenv('PPTX_THEME', '')
    rand_theme = chosen if chosen in {t['name'] for t in THEME_LIBRARY} else \
        _random.Random(str(document_id)).choice(THEME_LIBRARY)['name']
    spec = _validate_and_fill_design_spec({'theme_name': rand_theme}, n_slides)
    print(f'[pptx_design_spec] theme (random, no LLM call): {rand_theme}')

    spec['rhythm'] = _lock_bookend_rhythm(spec['rhythm'])

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({'_cache_key': cache_key, 'spec': spec}, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass

    return _design_spec_to_theme(spec)


# ===========================================================================
# SVG authoring: constrained-SVG parser -> composed-shape dicts
#
# The LLM in SlideLayoutComposerAgent hand-authors one <svg> per slide (viewBox in
# inches matching `canvas`); everything below turns that markup into the same
# shape-dict list the JS renderer already understands, extended with a new
# 'freeform' type for CUSTOM_GEOMETRY (see Phase A finding: pptxgenjs's
# `points` are LOCAL to the shape's own declared x/y/w/h box, verified against
# real generated OOXML, not the slide's absolute coordinates).
# ===========================================================================

def _svg_tag(el: ET.Element) -> str:
    t = el.tag
    return t.split('}', 1)[1] if '}' in t else t


def _mat_identity() -> tuple:
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat_multiply(m1: tuple, m2: tuple) -> tuple:
    """Compose two SVG-convention affine matrices (a,b,c,d,e,f) meaning x'=a*x+c*y+e, y'=b*x+d*y+f --
    result applies m2 to a point first, then m1 (m1 is the outer/ancestor transform, m2 the inner/own
    one, matching SVG's left-to-right transform-list and nested-<g> composition order)."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2, b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1,
    )


def _mat_apply(m: tuple, x: float, y: float) -> tuple:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _decompose_matrix(m: tuple) -> tuple:
    """Split an affine matrix into (scale_x, scale_y, rotate_deg, has_skew). Any composition of pure
    translate/rotate/scale (our whole transform vocabulary) is skew-free; has_skew=True flags a raw
    matrix() with non-orthogonal columns, where scale_x/rotate_deg are meaningless and the caller should
    fall back to point-sampling the shape's boundary as freeform instead of trusting a rotate field."""
    a, b, c, d, _e, _f = m
    scale_x = math.hypot(a, b)
    scale_y = math.hypot(c, d)
    has_skew = abs(a * c + b * d) > 1e-6 * max(scale_x * scale_y, 1e-9)
    return scale_x, scale_y, math.degrees(math.atan2(b, a)), has_skew


_TRANSFORM_FN_RE = re.compile(r'(translate|rotate|scale|matrix)\s*\(([^)]*)\)')
_NUM_RE = re.compile(r'-?\d*\.?\d+(?:[eE][+-]?\d+)?')


def _parse_transform(s: Optional[str]) -> tuple:
    """Parse an SVG `transform` attribute (translate/rotate/scale/matrix, applied left-to-right per the
    SVG spec) into one composed affine matrix. Unrecognized functions (skewX/skewY) are ignored rather
    than raising -- the validator's vocabulary check catches those as an authoring-contract violation."""
    m = _mat_identity()
    if not s:
        return m
    for fn, args_s in _TRANSFORM_FN_RE.findall(s):
        args = [float(v) for v in _NUM_RE.findall(args_s)]
        if fn == 'translate':
            step = (1.0, 0.0, 0.0, 1.0, args[0] if args else 0.0, args[1] if len(args) > 1 else 0.0)
        elif fn == 'scale':
            sx = args[0] if args else 1.0
            sy = args[1] if len(args) > 1 else sx
            step = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif fn == 'rotate':
            deg = args[0] if args else 0.0
            rad = math.radians(deg)
            cos_a, sin_a = math.cos(rad), math.sin(rad)
            rot = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                step = _mat_multiply(_mat_multiply((1, 0, 0, 1, cx, cy), rot), (1, 0, 0, 1, -cx, -cy))
            else:
                step = rot
        elif fn == 'matrix' and len(args) == 6:
            step = tuple(args)
        else:
            continue
        m = _mat_multiply(m, step)
    return m


def _svg_arc_to_center(x1: float, y1: float, rx: float, ry: float, phi_deg: float,
                       large_arc: bool, sweep: bool, x2: float, y2: float) -> Optional[tuple]:
    """SVG endpoint-parameterized arc -> center-parameterized arc (SVG 1.1 spec Appendix F.6.5), the
    form DrawingML's arcTo (and pptxgenjs's curve:{type:'arc'}) needs. Returns None for a degenerate arc
    (caller should draw a straight line instead). NOTE: assumes phi_deg == 0 -- DrawingML's arcTo has no
    x-axis-rotation parameter for the ellipse, so a rotated-ellipse arc has no exact PPTX equivalent; the
    authoring contract instructs the LLM to keep arc rotation at 0, which covers the vast majority of
    real decorative arcs (circular/axis-aligned-elliptical), rather than half-implementing a lossy
    approximation for the rare rotated case."""
    if x1 == x2 and y1 == y2:
        return None
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-9 or ry < 1e-9:
        return None
    phi = math.radians(phi_deg % 360)
    cos_phi, sin_phi = math.cos(phi), math.sin(phi)
    dx2, dy2 = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cos_phi * dx2 + sin_phi * dy2
    y1p = -sin_phi * dx2 + cos_phi * dy2
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    sign = -1.0 if large_arc == sweep else 1.0
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    denom = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    co = sign * math.sqrt(max(0.0, num / denom)) if denom > 1e-12 else 0.0
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) / 2.0
    cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) / 2.0

    def _ang(ux, uy, vx, vy):
        length = math.sqrt((ux ** 2 + uy ** 2) * (vx ** 2 + vy ** 2))
        if length < 1e-12:
            return 0.0
        a = math.acos(max(-1.0, min(1.0, (ux * vx + uy * vy) / length)))
        return -a if (ux * vy - uy * vx) < 0 else a

    start = _ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = _ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    return cx, cy, rx, ry, math.degrees(start), math.degrees(delta)


_PATH_CMD_CHARS = set('MmLlHhVvCcSsQqTtAaZz')
_PATH_CMD_ARGC = {'M': 2, 'L': 2, 'H': 1, 'V': 1, 'C': 6, 'S': 4, 'Q': 4, 'T': 2, 'A': 7, 'Z': 0}


def _tokenize_path(d: str) -> list:
    tokens: list = []
    i, n = 0, len(d)
    while i < n:
        ch = d[i]
        if ch.isspace() or ch == ',':
            i += 1
        elif ch in _PATH_CMD_CHARS:
            tokens.append(ch)
            i += 1
        else:
            m = _NUM_RE.match(d, i)
            if not m:
                i += 1
                continue
            tokens.append(float(m.group(0)))
            i = m.end()
    return tokens


def _parse_path_d(d: str) -> list[dict]:
    """Parse an SVG path `d` string into absolute-coordinate, untransformed segment dicts. Normalizes
    the full M/L/H/V/C/S/Q/T/A/Z grammar (both absolute and relative forms, implicit command repetition,
    S/T control-point reflection) down to the M/L/C/Q/A/Z subset pptxgenjs's CUSTOM_GEOMETRY understands.
    Requires standard whitespace/comma-separated arguments (the authoring contract's canonical spelling,
    same closed-vocabulary philosophy ppt-master uses) -- does not attempt to disambiguate maximally
    compact flag-adjacent number runs in arc commands (a narrow, rarely LLM-authored edge case)."""
    tokens = _tokenize_path(d)
    segs: list[dict] = []
    i = 0
    cx = cy = start_x = start_y = 0.0
    last_cubic_ctrl: Optional[tuple] = None
    last_quad_ctrl: Optional[tuple] = None
    cmd: Optional[str] = None
    while i < len(tokens):
        tok = tokens[i]
        if isinstance(tok, str):
            cmd = tok
            i += 1
            if not tokens[i:i + 1] and cmd.upper() != 'Z':
                break
        if cmd is None:
            break
        upper = cmd.upper()
        rel = cmd.islower()
        if upper == 'Z':
            segs.append({'cmd': 'Z'})
            cx, cy = start_x, start_y
            last_cubic_ctrl = last_quad_ctrl = None
            continue
        nargs = _PATH_CMD_ARGC[upper]
        if i + nargs > len(tokens) or any(isinstance(a, str) for a in tokens[i:i + nargs]):
            break
        args = tokens[i:i + nargs]
        i += nargs

        if upper == 'M':
            x, y = args
            if rel:
                x, y = cx + x, cy + y
            segs.append({'cmd': 'M', 'x': x, 'y': y})
            cx, cy, start_x, start_y = x, y, x, y
            last_cubic_ctrl = last_quad_ctrl = None
            cmd = 'l' if rel else 'L'
        elif upper == 'L':
            x, y = args
            if rel:
                x, y = cx + x, cy + y
            segs.append({'cmd': 'L', 'x': x, 'y': y})
            cx, cy = x, y
            last_cubic_ctrl = last_quad_ctrl = None
        elif upper == 'H':
            x = cx + args[0] if rel else args[0]
            segs.append({'cmd': 'L', 'x': x, 'y': cy})
            cx = x
            last_cubic_ctrl = last_quad_ctrl = None
        elif upper == 'V':
            y = cy + args[0] if rel else args[0]
            segs.append({'cmd': 'L', 'x': cx, 'y': y})
            cy = y
            last_cubic_ctrl = last_quad_ctrl = None
        elif upper == 'C':
            x1, y1, x2, y2, x, y = args
            if rel:
                x1, y1, x2, y2, x, y = cx + x1, cy + y1, cx + x2, cy + y2, cx + x, cy + y
            segs.append({'cmd': 'C', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'x': x, 'y': y})
            cx, cy = x, y
            last_cubic_ctrl, last_quad_ctrl = (x2, y2), None
        elif upper == 'S':
            x2, y2, x, y = args
            if rel:
                x2, y2, x, y = cx + x2, cy + y2, cx + x, cy + y
            x1, y1 = (2 * cx - last_cubic_ctrl[0], 2 * cy - last_cubic_ctrl[1]) if last_cubic_ctrl else (cx, cy)
            segs.append({'cmd': 'C', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2, 'x': x, 'y': y})
            cx, cy = x, y
            last_cubic_ctrl, last_quad_ctrl = (x2, y2), None
        elif upper == 'Q':
            x1, y1, x, y = args
            if rel:
                x1, y1, x, y = cx + x1, cy + y1, cx + x, cy + y
            segs.append({'cmd': 'Q', 'x1': x1, 'y1': y1, 'x': x, 'y': y})
            cx, cy = x, y
            last_quad_ctrl, last_cubic_ctrl = (x1, y1), None
        elif upper == 'T':
            x, y = args
            if rel:
                x, y = cx + x, cy + y
            x1, y1 = (2 * cx - last_quad_ctrl[0], 2 * cy - last_quad_ctrl[1]) if last_quad_ctrl else (cx, cy)
            segs.append({'cmd': 'Q', 'x1': x1, 'y1': y1, 'x': x, 'y': y})
            cx, cy = x, y
            last_quad_ctrl, last_cubic_ctrl = (x1, y1), None
        elif upper == 'A':
            rx, ry, phi, large_arc, sweep, x, y = args
            if rel:
                x, y = cx + x, cy + y
            center = _svg_arc_to_center(cx, cy, rx, ry, phi, bool(large_arc), bool(sweep), x, y)
            if center is None:
                segs.append({'cmd': 'L', 'x': x, 'y': y})
            else:
                acx, acy, arx, ary, start_deg, sweep_deg = center
                segs.append({'cmd': 'A', 'cx': acx, 'cy': acy, 'rx': arx, 'ry': ary,
                            'start_deg': start_deg, 'sweep_deg': sweep_deg, 'x': x, 'y': y})
            cx, cy = x, y
            last_cubic_ctrl = last_quad_ctrl = None
    return segs


def _segs_to_freeform(segs: list[dict], m: tuple) -> tuple[list[dict], list[tuple]]:
    """Apply transform matrix `m` to parsed path segments, returning (pptxgenjs-style points-with-curve
    dicts using LOCAL-to-shape-bbox coordinates once re-based by the caller, plus the flat list of every
    transformed control/end point for bbox computation -- includes bezier control points, a safe
    OVER-approximation of the true curve's extent, never an under-approximation)."""
    out: list[dict] = []
    all_pts: list[tuple] = []
    for idx, seg in enumerate(segs):
        c = seg['cmd']
        if c == 'Z':
            out.append({'close': True})
            continue
        x, y = _mat_apply(m, seg['x'], seg['y'])
        all_pts.append((x, y))
        if c in ('M', 'L'):
            out.append({'x': x, 'y': y, 'moveTo': (idx == 0 and c == 'M')})
        elif c == 'C':
            x1, y1 = _mat_apply(m, seg['x1'], seg['y1'])
            x2, y2 = _mat_apply(m, seg['x2'], seg['y2'])
            all_pts.extend([(x1, y1), (x2, y2)])
            out.append({'x': x, 'y': y, 'curve': {'type': 'cubic', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2}})
        elif c == 'Q':
            x1, y1 = _mat_apply(m, seg['x1'], seg['y1'])
            all_pts.append((x1, y1))
            out.append({'x': x, 'y': y, 'curve': {'type': 'quadratic', 'x1': x1, 'y1': y1}})
        elif c == 'A':
            # Arc center/radii aren't single points under a general transform; approximate by
            # transforming the endpoint only and keeping local-space radii (matches the phi==0 /
            # no-skew assumption already documented in _svg_arc_to_center).
            scale_x, scale_y, _rot, _skew = _decompose_matrix(m)
            out.append({'x': x, 'y': y, 'curve': {
                'type': 'arc', 'hR': seg['ry'] * scale_y, 'wR': seg['rx'] * scale_x,
                'stAng': seg['start_deg'], 'swAng': seg['sweep_deg'],
            }})
    return out, all_pts


def _points_bbox(pts: list[tuple]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _freeform_shape(out_points: list[dict], all_pts: list[tuple], fill: Optional[str],
                    line_color: Optional[str] = None) -> Optional[dict]:
    """Re-base pptxgenjs curve points (currently in true canvas coordinates) to be LOCAL to their own
    bounding box, per the Phase A finding -- CUSTOM_GEOMETRY's `points` are relative to the shape's own
    declared x/y/w/h, not the slide."""
    if not all_pts:
        return None
    bx, by, bw, bh = _points_bbox(all_pts)
    bw, bh = max(bw, 0.02), max(bh, 0.02)

    def _rebase(px, py):
        return px - bx, py - by

    rebased: list[dict] = []
    for p in out_points:
        if p.get('close'):
            rebased.append({'close': True})
            continue
        x, y = _rebase(p['x'], p['y'])
        entry: dict = {'x': x, 'y': y}
        if p.get('moveTo'):
            entry['moveTo'] = True
        if 'curve' in p:
            c = p['curve']
            if c['type'] == 'cubic':
                x1, y1 = _rebase(c['x1'], c['y1'])
                x2, y2 = _rebase(c['x2'], c['y2'])
                entry['curve'] = {'type': 'cubic', 'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2}
            elif c['type'] == 'quadratic':
                x1, y1 = _rebase(c['x1'], c['y1'])
                entry['curve'] = {'type': 'quadratic', 'x1': x1, 'y1': y1}
            elif c['type'] == 'arc':
                entry['curve'] = dict(c)  # hR/wR/stAng/swAng are already local (radii, not points)
        rebased.append(entry)
    shape: dict = {'type': 'freeform', 'x': bx, 'y': by, 'w': bw, 'h': bh, 'points': rebased}
    if fill:
        shape['fill'] = fill
    if line_color:
        shape['line_color'] = line_color
    return shape


def _svg_num(el: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(el.get(name, default))
    except (TypeError, ValueError):
        return default


def _svg_fill(el: ET.Element, theme: dict) -> Optional[str]:
    fill = el.get('fill')
    if not fill or fill == 'none':
        return None
    return fill.lstrip('#')


def _rect_corners(x: float, y: float, w: float, h: float) -> list[tuple]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def _shape_from_rect_like(corners_local: list[tuple], m: tuple, fill: Optional[str],
                          shape_name: str, rect_radius: Optional[float] = None) -> dict:
    """rect/circle/ellipse under a skew-free transform stay a native preset (rotate field); under a
    genuine skew (raw matrix() with non-orthogonal columns) fall back to a freeform polygon of the
    transformed corners -- native presets look cleaner and stay independently editable in PowerPoint,
    so this is only a fallback for the rare skewed case, not the default path."""
    scale_x, scale_y, rotate_deg, has_skew = _decompose_matrix(m)
    if has_skew:
        pts = [_mat_apply(m, x, y) for x, y in corners_local]
        out_points = [{'x': p[0], 'y': p[1], 'moveTo': i == 0} for i, p in enumerate(pts)]
        out_points.append({'close': True})
        return _freeform_shape(out_points, pts, fill) or {}
    xs = [p[0] for p in corners_local]
    ys = [p[1] for p in corners_local]
    local_w, local_h = max(xs) - min(xs), max(ys) - min(ys)
    cx_local, cy_local = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    ccx, ccy = _mat_apply(m, cx_local, cy_local)
    w, h = local_w * scale_x, local_h * scale_y
    shape = {'type': 'accent_shape', 'shape': shape_name, 'x': ccx - w / 2, 'y': ccy - h / 2,
             'w': w, 'h': h}
    if fill:
        shape['fill'] = fill
    if abs(rotate_deg) > 0.05:
        shape['rotate'] = rotate_deg % 360
    if rect_radius is not None and shape_name == 'roundRect':
        shape['rectRadius'] = rect_radius
    return shape


def _svg_text_role_type(el: ET.Element) -> str:
    return 'title' if el.get('data-role') == 'title' else 'bullet'


def _element_to_shapes(el: ET.Element, m: tuple, items: list[str], doc_images: list[str],
                       theme: dict, document_id: Optional[str]) -> list[dict]:
    tag = _svg_tag(el)
    fill = _svg_fill(el, theme)

    if tag == 'rect':
        x, y = _svg_num(el, 'x'), _svg_num(el, 'y')
        w, h = _svg_num(el, 'width'), _svg_num(el, 'height')
        rx = el.get('rx')
        preset = el.get('data-preset')
        shape_name = preset if preset else ('roundRect' if rx else 'rect')
        s = _shape_from_rect_like(_rect_corners(x, y, w, h), m, fill, shape_name,
                                  rect_radius=(min(1.0, float(rx) / max(w, h, 1e-6)) if rx else None))
        return [s] if s else []

    if tag in ('circle', 'ellipse'):
        cx, cy = _svg_num(el, 'cx'), _svg_num(el, 'cy')
        rx = _svg_num(el, 'r') if tag == 'circle' else _svg_num(el, 'rx')
        ry = _svg_num(el, 'r') if tag == 'circle' else _svg_num(el, 'ry')
        corners = _rect_corners(cx - rx, cy - ry, rx * 2, ry * 2)
        s = _shape_from_rect_like(corners, m, fill, 'ellipse')
        return [s] if s else []

    if tag == 'line':
        x1, y1 = _svg_num(el, 'x1'), _svg_num(el, 'y1')
        x2, y2 = _svg_num(el, 'x2'), _svg_num(el, 'y2')
        p1, p2 = _mat_apply(m, x1, y1), _mat_apply(m, x2, y2)
        out_points = [{'x': p1[0], 'y': p1[1], 'moveTo': True}, {'x': p2[0], 'y': p2[1]}]
        stroke = (el.get('stroke') or '').lstrip('#') or None
        s = _freeform_shape(out_points, [p1, p2], None, line_color=stroke)
        return [s] if s else []

    if tag in ('polygon', 'polyline'):
        raw = (el.get('points') or '').replace(',', ' ').split()
        nums = [float(v) for v in raw]
        local_pts = list(zip(nums[0::2], nums[1::2]))
        if len(local_pts) < 2:
            return []
        pts = [_mat_apply(m, x, y) for x, y in local_pts]
        out_points = [{'x': p[0], 'y': p[1], 'moveTo': i == 0} for i, p in enumerate(pts)]
        if tag == 'polygon':
            out_points.append({'close': True})
        s = _freeform_shape(out_points, pts, fill)
        return [s] if s else []

    if tag == 'path':
        segs = _parse_path_d(el.get('d') or '')
        if not segs:
            return []
        out_points, all_pts = _segs_to_freeform(segs, m)
        if not all_pts:
            return []
        s = _freeform_shape(out_points, all_pts, fill)
        return [s] if s else []

    if tag == 'text':
        idx_attr = el.get('data-item')
        item_index = None
        if idx_attr is not None and idx_attr.isdigit() and int(idx_attr) < len(items):
            text = items[int(idx_attr)]
            item_index = int(idx_attr)
        else:
            text = (el.text or '').strip()
        x, y = _svg_num(el, 'x'), _svg_num(el, 'y')
        w, h = _svg_num(el, 'width', 2.0), _svg_num(el, 'height', 0.4)
        _scale_x, _scale_y, rotate_deg, _skew = _decompose_matrix(m)
        px, py = _mat_apply(m, x, y)
        shape = {'type': _svg_text_role_type(el), 'text': text, 'x': px, 'y': py, 'w': w, 'h': h,
                 'font_size_hint': _svg_num(el, 'font-size', 16),
                 'size_declared': el.get('width') is not None and el.get('height') is not None}
        if item_index is not None:
            shape['item_index'] = item_index
        if fill:
            shape['color'] = fill
        align = el.get('text-anchor')
        if align in ('middle', 'end'):
            shape['align'] = 'center' if align == 'middle' else 'right'
        if abs(rotate_deg) > 0.05:
            shape['rotate'] = rotate_deg % 360
        if el.get('data-shadow') == 'true':
            shape['shadow'] = True
        if el.get('data-glow') == 'true':
            shape['glow'] = True
        return [shape]

    if tag == 'image':
        x, y = _svg_num(el, 'x'), _svg_num(el, 'y')
        w, h = _svg_num(el, 'width', 3.0), _svg_num(el, 'height', 2.0)
        px, py = _mat_apply(m, x, y)
        img_idx = el.get('data-image')
        if img_idx is not None and img_idx.isdigit() and int(img_idx) < len(doc_images):
            return [_image_shape('document', doc_images[int(img_idx)], px, py, w, h)]
        prompt = (el.get('data-prompt') or el.text or '').strip()
        if prompt:
            return [_image_shape('generate', prompt, px, py, w, h)]
        return []

    return []


_SVG_ALLOWED_TAGS = {'svg', 'g', 'defs', 'rect', 'circle', 'ellipse', 'line', 'polygon', 'polyline',
                     'path', 'text', 'tspan', 'image'}


def parse_svg_shapes(svg_text: str, items: list[str], doc_images: list[str], theme: dict,
                     document_id: Optional[str] = None) -> list[dict]:
    """Top-level SVG-authoring-contract parser: walk the LLM's hand-authored <svg> document and emit the
    same shape-dict list deck_pptx_gen.js already renders (title/bullet/image/accent_shape/scrim), plus
    the new 'freeform' type for CUSTOM_GEOMETRY paths/polygons. Raises ValueError (caught by the
    validator/repair loop in SlideLayoutComposerAgent, not swallowed here) for any element/attribute outside
    the authoring contract -- fail loud on an unrecognized vocabulary item rather than silently dropping
    content, matching this project's version-guard convention elsewhere (src/rag/lightrag_patch.py)."""
    root = ET.fromstring(svg_text.strip())
    if _svg_tag(root) != 'svg':
        raise ValueError(f'root element must be <svg>, got <{_svg_tag(root)}>')

    shapes: list[dict] = []

    def _walk(el: ET.Element, m: tuple):
        for child in el:
            tag = _svg_tag(child)
            if tag not in _SVG_ALLOWED_TAGS:
                raise ValueError(f'<{tag}> is not in the SVG authoring contract vocabulary')
            if tag == 'defs':
                continue  # gradient/pattern defs are unsupported (no gradient fill) -- never visible content
            child_m = _mat_multiply(m, _parse_transform(child.get('transform')))
            if tag == 'g':
                _walk(child, child_m)
                continue
            if tag == 'tspan':
                continue  # handled as part of the parent <text>'s own text content, not a shape of its own
            shapes.extend(_element_to_shapes(child, child_m, items, doc_images, theme, document_id))

    _walk(root, _mat_identity())
    return shapes


def _describe_composition(shapes: list[dict]) -> str:
    counts = Counter(s.get('type') for s in shapes)
    n_text = counts.get('title', 0) + counts.get('bullet', 0)
    n_shape = counts.get('accent_shape', 0) + counts.get('freeform', 0)
    n_img = counts.get('image', 0)
    parts = []
    if n_img:
        parts.append(f'{n_img} image(s)')
    if n_shape:
        parts.append(f'{n_shape} decorative shape(s)')
    if n_text:
        parts.append(f'{n_text} text block(s)')
    return ', '.join(parts) or 'minimal composition'


def _violation_context(shapes: list[dict], idx: int) -> str:
    if not (0 <= idx < len(shapes)):
        return f'shape #{idx}'
    s = shapes[idx]
    label = s.get('text') or s.get('shape') or s.get('type', 'shape')
    x, y, w, h = s.get('x'), s.get('y'), s.get('w'), s.get('h')
    coord = f' at x={x:.2f},y={y:.2f},w={w:.2f},h={h:.2f}' if None not in (x, y, w, h) else ''
    return f'shape #{idx} ({s.get("type")} {str(label)[:40]!r}{coord})'


def _svg_violations_report(shapes: list[dict], canvas: dict, margin: float, bg_color: str,
                           top_margin: Optional[float] = None) -> list[str]:
    """Run every validator over a freshly-parsed shapes list and turn each violation into one
    human-readable line the LLM can act on directly (which shape, what's wrong, by how much) --
    the repair loop sends this back verbatim instead of a bare exception message. `top_margin`
    optionally reserves extra space at the top (e.g. for a title band) -- defaults to `margin`."""
    lines: list[str] = []
    for i, edge, amt in _find_offcanvas(shapes, canvas, margin, top_margin=top_margin):
        lines.append(f'{_violation_context(shapes, i)} extends {amt}in past the {edge} canvas edge.')
    for i, needed, declared in _find_text_overflow(shapes):
        lines.append(f'{_violation_context(shapes, i)} needs height >= {needed}in for its own text at its '
                     f'own font-size, but only {declared}in was given -- enlarge it or shrink the font.')
    for i, j in _find_overlaps(shapes):
        lines.append(f'{_violation_context(shapes, i)} overlaps {_violation_context(shapes, j)}.')
    for i, j, item_idx in _find_duplicate_item_refs(shapes):
        lines.append(f'{_violation_context(shapes, i)} and {_violation_context(shapes, j)} both render '
                     f'data-item {item_idx} -- reference each content item in exactly ONE <text> element.')
    for i, phrase in _find_duplicate_words(shapes):
        lines.append(f'{_violation_context(shapes, i)} has a duplicated word/phrase {phrase!r}.')
    for i, bad_name in _find_invalid_preset(shapes, frozenset(_SAFE_ACCENT_SHAPES)):
        lines.append(f'{_violation_context(shapes, i)} uses preset name {bad_name!r}, which is not in the '
                     'allowed preset list.')
    for i in _find_missing_text_color(shapes):
        lines.append(f'{_violation_context(shapes, i)} has no fill color -- every <text> element MUST '
                     'specify fill="#RRGGBB" (an unfilled text element is invisible).')
    for i in _find_missing_text_size(shapes):
        lines.append(f'{_violation_context(shapes, i)} has no explicit width/height -- every <text> '
                     'element MUST specify both (an unset one silently becomes a tiny 2.0x0.4in box, '
                     'too small for real content); estimate a real size using the sizing rule above.')
    for i, fs in _find_implausible_font_size(shapes):
        lines.append(f'{_violation_context(shapes, i)} has font-size={fs} which is implausibly small -- '
                     'font-size is in POINTS, not inches like x/y/width/height; use a real point value '
                     '(typically 11-48).')
    for i, ratio in _find_low_contrast(shapes, bg_color):
        lines.append(f'{_violation_context(shapes, i)} has a contrast ratio of only {ratio} against what\'s '
                     'behind it -- pick a more contrasting text color.')
    for i, reason in _find_image_contract_violation(shapes):
        lines.append(f'{_violation_context(shapes, i)}: {reason}.')
    return lines




