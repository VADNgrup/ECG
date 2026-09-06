from __future__ import annotations

import datetime as _dt
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GEN_JS = Path(__file__).parent / 'deck_pptx_gen.js'

                                                                                

def _render_formula_png(
    latex_str: str,
    out_path: Path,
    fg_color: str = '#FFFFFF',
    accent_color: str = '#FFCC33',
) -> bool:
    try:
        import io
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        eqs = [e.strip() for e in re.split(r'\n\s*\n', latex_str.strip()) if e.strip()]
        n   = len(eqs)
        fig, ax = plt.subplots(figsize=(10, max(1.4, n * 1.4)))
        ax.set_facecolor('none')
        fig.patch.set_alpha(0.0)
        ax.axis('off')

        step = 1.0 / (n + 1)
        for i, eq in enumerate(eqs):
            y = 1.0 - (i + 1) * step
                                                                        
            try:
                ax.text(0.5, y, f'${eq}$', fontsize=22, ha='center', va='center',
                        transform=ax.transAxes, color=fg_color,
                        fontfamily='DejaVu Serif')
            except Exception:
                plain = re.sub(r'\\[a-zA-Z]+', '', eq).replace('{', '').replace('}', '')
                ax.text(0.5, y, plain, fontsize=18, ha='center', va='center',
                        transform=ax.transAxes, color=fg_color, style='italic')

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=180, bbox_inches='tight', transparent=True)
        plt.close(fig)
        out_path.write_bytes(buf.getvalue())
        return True
    except Exception:
        return False

                                                                                

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
        from PIL import Image
        import numpy as np

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

                                                                                

def _txt(el) -> str:
    return re.sub(r'\s+', ' ', el.get_text(separator=' ', strip=True)).strip()

def _clean_formula_latex(raw: str) -> str:
    raw = re.sub(r'\\\[(.+?)\\\]',
                 lambda m: m.group(1).strip() + '\n\n', raw, flags=re.DOTALL)
    raw = re.sub(r'\\\((.+?)\\\)',
                 lambda m: m.group(1).strip(), raw, flags=re.DOTALL)
    return raw.strip()

def _extract_images(section, html_dir: Path, exclude_class: Optional[str] = None) -> list[str]:
    imgs_wrap = section.find(class_='imgs')
    if imgs_wrap:
        paths = []
        for img in imgs_wrap.find_all('img'):
            src = img.get('src', '')
            if src:
                p = (html_dir / src).resolve()
                if p.exists():
                    paths.append(str(p))
        return paths

    for fig in section.find_all('figure'):
        if exclude_class and fig.find_parent(class_=exclude_class):
            continue
        img = fig.find('img')
        if img:
            src = img.get('src', '')
            if src:
                p = (html_dir / src).resolve()
                if p.exists():
                    return [str(p)]
    return []

def _parse_section(section, html_dir: Path) -> Optional[dict]:
    classes  = section.get('class', [])
    is_cover = 'cover' in classes
    is_end   = 'end'   in classes
    is_light = 'light' in classes

    pn_el    = section.find(class_='pn')
    page_num = pn_el.get_text(strip=True) if pn_el else ''

                                                                             
    if is_cover:
        h1    = section.find('h1')
        title = _txt(h1) if h1 else ''
        by_el = section.find(class_='by')
        spk   = ''
        if by_el:
            b = by_el.find('b')
            spk = _txt(b) if b else ''
        return {'type': 'cover', 'title': title, 'speaker': spk}

                                                                             
    if is_end:
        h2    = section.find('h2')
        title = _txt(h2) if h2 else 'Thank You'
        return {'type': 'end', 'title': title}

                                                                             
    LAYOUTS = {
        'toc', 'toc-vertical', 'toc-cards', 'toc-described',
        'bullets', 'formula',
        'twoimgbelow', 'twoimgright', 'twoimgleft',
        'imgleft', 'imgright', 'imgabove', 'imgbelow', 'twoimgabove',
        'twocols', 'twocontents', 'cmptable', 'tblabove', 'stat',
        'steps', 'keypoints', 'threecol',
        'splitcontrast', 'conclcards', 'numconcl',
        'grid2x2', 'rquestion', 'agenda', 'quote',
        'section-divider', 'imgfull', 'pricing',
    }
    TOC_VARIANTS = {'toc', 'toc-vertical', 'toc-cards', 'toc-described'}
    layout = 'bullets'
    for c in classes:
        if c in LAYOUTS:
            layout = c
            break
                                                            
    if layout in TOC_VARIANTS:
        pass # Keep the specific layout variant (e.g., 'toc-cards')

                                                                             
    if layout in ('twoimgright', 'imgright', 'twocontents'):
        lhs = section.find(class_='lhs')
        h2  = (lhs.find('h2') if lhs else None) or section.find('h2')
    elif layout == 'imgleft':
        rhs = section.find(class_='rhs')
        h2  = (rhs.find('h2') if rhs else None) or section.find('h2')
    elif layout == 'keypoints':
        head = section.find(class_='head')
        h2   = (head.find('h2') if head else None) or section.find('h2')
    elif layout == 'agenda':
        lhs = section.find(class_='lhs')
        h2  = (lhs.find('h2') if lhs else None) or section.find('h2')
    elif layout in ('quote', 'splitcontrast'):
        h2 = None
    elif layout == 'stat':
        h2 = None                            
    else:
        h2 = section.find('h2')
    title = _txt(h2) if h2 else ''

    entry: dict = {
        'type':     layout,
        'title':    title,
        'is_light': is_light,
        'page_num': page_num,
    }

                                                                             
    if layout in ('bullets', 'twoimgbelow', 'formula', 'imgabove', 'imgbelow', 'twoimgabove'):
        ul = section.find('ul')
        if ul:
            entry['bullets'] = [_txt(li) for li in ul.find_all('li', recursive=False)]
    elif layout in ('imgright', 'twoimgright'):
        lhs = section.find(class_='lhs')
        ul  = (lhs.find('ul') if lhs else None) or section.find('ul')
        if ul:
            entry['bullets'] = [_txt(li) for li in ul.find_all('li', recursive=False)]
    elif layout == 'imgleft':
        rhs = section.find(class_='rhs')
        ul  = (rhs.find('ul') if rhs else None) or section.find('ul')
        if ul:
            entry['bullets'] = [_txt(li) for li in ul.find_all('li', recursive=False)]

                                                                             
    if layout == 'twocols':
        grid = section.find(class_='grid')
        cols: list[list[str]] = []
        if grid:
            for ul in grid.find_all('ul'):
                cols.append([_txt(li) for li in ul.find_all('li', recursive=False)])
        entry['cols'] = cols

                                                                             
    if layout in TOC_VARIANTS:
        items = []
                                         
        for row in section.find_all(class_='toc-row'):
            n_el = row.find(class_='n')
            t_el = row.find(class_='t')
            items.append({
                'n': n_el.get_text(strip=True) if n_el else '',
                't': t_el.get_text(strip=True) if t_el else '',
            })
                                                                            
        if not items:
            for li in section.find_all('li'):
                n_el = li.find(class_='n')
                t_el = li.find(class_='t')
                if not t_el:
                    meta = li.find(class_='meta')
                    t_el = meta.find(class_='t') if meta else None
                items.append({
                    'n': n_el.get_text(strip=True) if n_el else '',
                    't': t_el.get_text(strip=True) if t_el else _txt(li),
                })
                                            
        if not items:
            for card in section.find_all(class_='card'):
                n_el = card.find(class_='n')
                h3   = card.find('h3')
                p    = card.find('p')
                items.append({
                    'n': n_el.get_text(strip=True) if n_el else '',
                    'title': _txt(h3) if h3 else '',
                    'desc': _txt(p) if p else '',
                    't': _txt(h3) if h3 else '',
                })
        
        if layout == 'toc-cards':
            entry['cards'] = items
            entry['type'] = 'toc_cards'
        elif layout == 'toc-described':
            entry['toc_items'] = items
            entry['type'] = 'toc_described'
        elif layout == 'toc-vertical':
            entry['toc_items'] = items
            entry['type'] = 'toc_vertical'
        else:
            entry['toc_items'] = items

    if layout == 'formula':
        eq_el = section.find(class_='eq')
        if eq_el:
            entry['formula_latex'] = _clean_formula_latex(
                eq_el.get_text(separator='\n', strip=True)
            )

    if layout in ('imgabove', 'imgbelow'):
        imgs = _extract_images(section, html_dir)
        if imgs:
            entry['images'] = imgs
    elif layout in ('twoimgbelow', 'twoimgright', 'twoimgabove'):
        imgs = _extract_images(section, html_dir)
        if imgs:
            entry['images'] = imgs
    elif layout in ('imgright', 'twoimgleft'):
        imgs = _extract_images(section, html_dir, exclude_class='lhs')
        if imgs:
            entry['images'] = imgs
    elif layout == 'imgleft':
        imgs = _extract_images(section, html_dir, exclude_class='rhs')
        if imgs:
            entry['images'] = imgs
    elif layout == 'imgfull':
        img_el = section.find('img')
        if img_el:
            src = img_el.get('src', '')
            if src:
                p = (html_dir / src).resolve()
                if p.exists():
                    entry['images'] = [str(p)]
        bw = section.find(class_='body-wrap')
        p_el = (bw or section).find('p')
        entry['body_text'] = _txt(p_el) if p_el else ''

                                                                             
    if layout in ('cmptable', 'tblabove'):
        table_el = section.find('table')
        if table_el:
            thead = table_el.find('thead')
            tbody = table_el.find('tbody')
            headers = [_txt(th) for th in thead.find_all('th')] if thead else []
            rows: list[list[str]] = []
            if tbody:
                for tr in tbody.find_all('tr'):
                    row = [_txt(td) for td in tr.find_all('td')]
                    if any(c for c in row):
                        rows.append(row)
            entry['parsed_table'] = {'headers': headers, 'rows': rows}

    if layout == 'stat':
        kicker = section.find(class_='kicker')
        entry['title'] = _txt(kicker) if kicker else ''
        stats = []
        for n_el in section.find_all(class_='n'):
            big = n_el.find(class_='big')
            lbl = n_el.find(class_='lbl')
            stats.append({
                'value': _txt(big) if big else '',
                'label': _txt(lbl) if lbl else '',
            })
        entry['stats'] = stats
        note = section.find(class_='note')
        entry['note'] = _txt(note) if note else ''

    if layout == 'twocontents':
        blocks = []
        for block in section.find_all(class_='block'):
            h3 = block.find('h3')
            ul = block.find('ul')
            blocks.append({
                'subtitle': _txt(h3) if h3 else '',
                'bullets':  [_txt(li) for li in ul.find_all('li', recursive=False)] if ul else [],
            })
        entry['blocks'] = blocks

    if layout == 'steps':
        steps = []
        for step in section.find_all(class_='step'):
            num_el = step.find(class_='num')
            h3     = step.find('h3')
            p      = step.find('p')
            steps.append({
                'num':   _txt(num_el) if num_el else '',
                'title': _txt(h3)    if h3     else '',
                'body':  _txt(p)     if p      else '',
            })
        entry['steps'] = steps

    if layout == 'keypoints':
        points = []
        for pt in section.find_all(class_='pt'):
            ix   = pt.find(class_='ix')
            ttl  = pt.find(class_='ttl')
            body = pt.find(class_='body')
            points.append({
                'ix':    _txt(ix)   if ix   else '',
                'title': _txt(ttl)  if ttl  else '',
                'body':  _txt(body) if body else '',
            })
        entry['points'] = points

    if layout == 'threecol':
        cols_data = []
        for col in section.find_all(class_='col'):
            tag = col.find(class_='tag')
            h3  = col.find('h3')
            p   = col.find('p')
            ul  = col.find('ul')
            cols_data.append({
                'tag':     _txt(tag) if tag else '',
                'title':   _txt(h3)  if h3  else '',
                'body':    _txt(p)   if p   else '',
                'bullets': [_txt(li) for li in ul.find_all('li', recursive=False)] if ul else [],
            })
        entry['cols'] = cols_data

    if layout == 'splitcontrast':
        sides = []
        for side in section.find_all(class_='side'):
            tag = side.find(class_='tag')
            h3  = side.find('h3')
            ul  = side.find('ul')
            sides.append({
                'tag':     _txt(tag) if tag else '',
                'title':   _txt(h3)  if h3  else '',
                'bullets': [_txt(li) for li in ul.find_all('li', recursive=False)] if ul else [],
            })
        entry['sides'] = sides

    if layout == 'conclcards':
        cards = []
        for card in section.find_all(class_='card'):
            num     = card.find(class_='num')
            h3      = card.find('h3')
            p       = card.find('p')
            cards.append({
                'num':     _txt(num) if num else '',
                'heading': _txt(h3)  if h3  else '',
                'body':    _txt(p)   if p   else '',
            })
        entry['cards'] = cards

    if layout == 'numconcl':
        rows_data = []
        for row in section.find_all(class_='row'):
            n_el = row.find(class_='n')
            t_el = row.find(class_='t')
            b_el = row.find(class_='b')
            rows_data.append({
                'n':       _txt(n_el) if n_el else '',
                'heading': _txt(t_el) if t_el else '',
                'body':    _txt(b_el) if b_el else '',
            })
        entry['rows'] = rows_data

    if layout == 'grid2x2':
        cells = []
        for cell in section.find_all(class_='cell'):
            h3 = cell.find('h3')
            p  = cell.find('p')
            cells.append({
                'title': _txt(h3) if h3 else '',
                'body':  _txt(p)  if p  else '',
            })
        entry['cells'] = cells

    if layout == 'rquestion':
        main_rq = section.find(class_='main-rq')
        q_el    = main_rq.find(class_='q') if main_rq else None
        entry['main_q'] = _txt(q_el) if q_el else ''
        sub_qs = []
        for sub in section.find_all(class_='sub'):
            lbl = sub.find(class_='lbl')
            q   = sub.find(class_='q')
            sub_qs.append({
                'lbl': _txt(lbl) if lbl else '',
                'q':   _txt(q)   if q   else '',
            })
        entry['sub_qs'] = sub_qs

    if layout == 'agenda':
        items = []
        for li in section.find_all('li'):
            ttl = li.find(class_='ttl')
            dur = li.find(class_='dur')
            items.append({
                'title':    _txt(ttl) if ttl else _txt(li),
                'duration': _txt(dur) if dur else '',
            })
        entry['items'] = items

    if layout == 'quote':
        bq  = section.find('blockquote')
        who = section.find(class_='who')
        entry['quote']       = _txt(bq)  if bq  else ''
        entry['attribution'] = _txt(who) if who else ''

    if layout == 'section-divider':
        sec_num  = section.find(class_='section-num')
        sec_foot = section.find(class_='section-foot')
        lead_el  = sec_foot.find(class_='lead') if sec_foot else None
        entry['section_num'] = _txt(sec_num)  if sec_num  else ''
        entry['lead']        = _txt(lead_el)  if lead_el  else ''

    if layout == 'pricing':
        cards = []
        for card in section.find_all(class_='card'):
            name  = card.find(class_='name')
            price = card.find(class_='price')
            ul    = card.find('ul')
            cards.append({
                'name':     _txt(name)  if name  else '',
                'price':    _txt(price) if price else '',
                'features': [_txt(li) for li in ul.find_all('li', recursive=False)] if ul else [],
            })
        entry['cards'] = cards

    return entry

def _parse_deck_html(html_path: Path, speaker: str = '') -> Optional[dict]:
    if not html_path.exists():
        return None
    try:
        from bs4 import BeautifulSoup                
    except ImportError:
        return None

    import datetime as _dt

    html = html_path.read_text(encoding='utf-8')
    soup = BeautifulSoup(html, 'html.parser')
    html_dir = html_path.parent

    deck_el = soup.find('deck-stage')
    theme   = _parse_theme(deck_el.get('style', '') if deck_el else '')

    title_tag     = soup.find('title')
    lecture_title = title_tag.get_text(strip=True) if title_tag else ''

                                                    
    date_str = _dt.date.today().strftime('%B %Y')
    for cb in soup.find_all(class_='chrome-bot'):
        for span in cb.find_all('span', recursive=False):
            if not span.find(class_='pn') and span.get_text(strip=True):
                date_str = span.get_text(strip=True)
                break
        else:
            continue
        break

    root   = deck_el if deck_el else soup
    slides = []
    for section in root.find_all('section', class_='slide'):
        entry = _parse_section(section, html_dir)
        if entry:
            slides.append(entry)

    return {
        'lecture_title': lecture_title,
        'speaker':       speaker,
        'date':          date_str,
        'theme':         theme,
        'slides':        slides,
    }


def _build_manifest_from_json(
    json_path:   Path,
    output_path: Path,
    speaker:     str = '',
) -> dict:
    import datetime as _dt

    with open(json_path, encoding='utf-8') as f:
        data = json.load(f)

    lecture_id    = data['lecture_id']
    lecture_title = data.get('lecture_title', lecture_id)
    metadata      = data.get('metadata', {})
    speaker       = speaker or metadata.get('speaker_information', '')
    slides_data   = data.get('slides', [])

    img_dist: dict[int, list] = defaultdict(list)
    img_dist_path = json_path.parent / f'{lecture_id}_image_distribution.json'
    if img_dist_path.exists():
        for item in json.load(open(img_dist_path, encoding='utf-8')):
            img_dist[int(item['slide_number'])].append(item)
    for k in img_dist:
        img_dist[k].sort(key=lambda x: x.get('score', 0), reverse=True)

    manifest_slides = []
    for entry in slides_data:
        info        = entry.get('slide', {})
        slide_num   = int(info.get('slide_number', 0))
        slide_title = info.get('slide_title', '')
        slide_type  = info.get('slide_type', 'content')
        bullets     = [str(b) for b in entry.get('content', [])]
        latex_fml   = info.get('latex_block_formula') or None
        table_data  = info.get('table') or None

        best_img: Optional[str] = None
        if slide_type not in ('have_formula', 'have_table') and slide_num in img_dist:
            cands = img_dist[slide_num]
            if cands:
                p = Path(str(cands[0]['image_path']).replace('\\', '/'))
                if p.exists():
                    best_img = str(p)

        sl: dict = {
            'type':     'bullets',
            'title':    slide_title,
            'bullets':  bullets,
            'is_light': False,
            'page_num': str(len(manifest_slides) + 1),
        }
        if table_data:
            sl['type']  = 'cmptable'
            sl['table'] = table_data
        if best_img:
            sl['images'] = [best_img]
            sl['type']   = 'imgright'
        if latex_fml:
            sl['formula_latex'] = latex_fml
            sl['type']          = 'formula'

        manifest_slides.append(sl)

    return {
        'lecture_title': lecture_title,
        'speaker':       speaker,
        'date':          _dt.date.today().strftime('%B %Y'),
        'theme':         {},
        'slides': (
            [{'type': 'cover', 'title': lecture_title, 'speaker': speaker}]
            + manifest_slides
            + [{'type': 'end', 'title': 'Thank You'}]
        ),
    }

                                                                                

def export_pptx(
    lecture_json_path: str | Path,
    output_path:       str | Path,
    speaker:           str = '',
) -> int:
    json_path   = Path(lecture_json_path).resolve()
    output_path = Path(output_path)

                                                                        
    html_path = output_path.with_suffix('.html')
    manifest  = _parse_deck_html(html_path, speaker)

    if manifest is None:
        print('[pptx_export] HTML unavailable or bs4 missing — using JSON manifest')
        manifest = _build_manifest_from_json(json_path, output_path, speaker)

                                                        
    grad_tmp: Optional[Path] = None
    grad_stops = manifest.get('theme', {}).get('grad_stops', [])
    if len(grad_stops) >= 2:
        grad_tmp = Path(tempfile.mktemp(suffix='.png'))
        if _render_gradient_bg_png(grad_stops, grad_tmp):
            manifest['dark_bg_path'] = str(grad_tmp)
        else:
            grad_tmp = None

    for slide in manifest.get('slides', []):
        latex = slide.pop('formula_latex', None)
        if latex:
            slide['formula_text'] = latex

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as mf:
        json.dump(manifest, mf, ensure_ascii=False)
        manifest_path = Path(mf.name)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ['node', str(_GEN_JS),
             '--manifest', str(manifest_path),
             '--output',   str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.stdout:
            print(result.stdout.strip())
        if result.returncode != 0:
            err = result.stderr.strip() or 'unknown error'
            raise RuntimeError(f'deck_pptx_gen.js exited {result.returncode}: {err}')
    finally:
        manifest_path.unlink(missing_ok=True)
        if grad_tmp:
            grad_tmp.unlink(missing_ok=True)

    return len(manifest.get('slides', []))


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


def _layout_log_to_slide(entry: dict, output_dir: Path, page_num: int) -> dict:
    fn = entry['layout_function_name']
    args = entry.get('args', {})
    light = bool(args.get('light', False))

    if fn == 'config_and_greeting_slide':
        return {'type': 'cover', 'title': args.get('short_title', ''), 'page_num': page_num}

    elif fn == 'end_layout':
        return {'type': 'end', 'title': args.get('end_text', 'Thank you'), 'page_num': page_num}

    elif fn == 'toc_layout':
        items = _toc_items(args.get('toc_content', []))
        return {'type': 'toc', 'toc_items': items, 'is_light': light, 'page_num': page_num}

    elif fn == 'toc_described_layout':
        items = _toc_items_described(args.get('toc_content', []))
        return {
            'type': 'toc_described', 'toc_items': items,
            'heading': args.get('heading', "What we'll cover"),
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'toc_vertical_layout':
        items = _toc_items(args.get('toc_content', []))
        return {'type': 'toc_vertical', 'toc_items': items, 'is_light': light, 'page_num': page_num}

    elif fn == 'toc_cards_layout':
        cards = _toc_cards(args.get('toc_content', []))
        return {
            'type': 'toc_cards', 'cards': cards,
            'heading': args.get('heading', 'Outline'),
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
            {'ix': p.get('icon', ''), 'title': p.get('title', ''), 'body': p.get('body', '')}
            for p in args.get('points', [])
        ]
        return {
            'type': 'keypoints', 'title': args.get('title', ''),
            'points': pts, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'comparison_layout':
        return {
            'type': 'cmptable', 'title': args.get('title', ''),
            'table': {'table_markdown': args.get('table_markdown', '')},
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'table_above_layout':
        return {
            'type': 'tblabove', 'title': args.get('title', ''),
            'table': {'table_markdown': args.get('table_markdown', '')},
            'bullets': args.get('content', []),
            'is_light': light, 'page_num': page_num,
        }

    elif fn in ('formula_top_layout', 'formula_below_layout'):
        return {
            'type': 'formula', 'title': args.get('title', ''),
            'formula_latex': args.get('latex_formula_block', ''),
            'bullets': args.get('content', []), 'is_light': light, 'page_num': page_num,
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
        return {
            'type': 'twoimgright', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_above_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        return {
            'type': 'twoimgabove', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_below_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        return {
            'type': 'twoimgbelow', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'two_image_left_layout':
        imgs = _resolve_imgs([args.get('img1_path'), args.get('img2_path')], output_dir)
        return {
            'type': 'twoimgleft', 'title': args.get('title', ''),
            'bullets': args.get('content', []), 'images': imgs,
            'is_light': light, 'page_num': page_num,
        }

    elif fn == 'conclusion_cards_layout':
        conc = args.get('conclusions', [])
        cards = [
            {'num': str(i + 1).zfill(2), 'heading': c.get('heading', ''), 'body': c.get('body', '')}
            for i, c in enumerate(conc)
        ]
        return {
            'type': 'conclcards', 'title': args.get('title', ''),
            'cards': cards, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'numbered_conclusions_layout':
        conc = args.get('conclusions', [])
        rows = [
            {'n': str(i + 1).zfill(2), 'heading': c.get('heading', ''), 'body': c.get('body', '')}
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
        cells = [{'title': c.get('title', ''), 'body': c.get('body', '')} for c in raw]
        return {
            'type': 'grid2x2', 'title': args.get('title', ''),
            'cells': cells, 'is_light': light, 'page_num': page_num,
        }

    elif fn == 'steps_horizontal_layout':
        raw = args.get('steps', [])
        steps = [
            {'num': str(i + 1), 'title': s.get('title', ''), 'body': s.get('body', '')}
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
        return {
            'type': 'section-divider', 'title': args.get('title', ''),
            'section_num': args.get('section_number', ''), 'lead': args.get('lead', ''),
            'is_light': light, 'page_num': page_num,
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
            'is_light': light, 'page_num': page_num,
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


def export_pptx_from_layout_log(
    lecture_json_path: 'str | Path',
    layout_log_path:   'str | Path',
    output_path:       'str | Path',
    html_path:         'str | Path | None' = None,
    speaker:           str = '',
) -> int:
    """Export PPTX using the layout_log JSON as the authoritative data source.

    Falls back to :func:`export_pptx` if the layout_log file does not exist.
    """
    json_path        = Path(lecture_json_path).resolve()
    layout_log_path  = Path(layout_log_path)
    output_path      = Path(output_path)

    if not layout_log_path.exists():
        print(f'[pptx_export] layout_log not found ({layout_log_path}) — falling back to export_pptx()')
        return export_pptx(json_path, output_path, speaker)

    with open(json_path, encoding='utf-8') as f:
        lecture_data = json.load(f)
    lecture_title = lecture_data.get('lecture_title', json_path.stem)
    if not speaker:
        speaker = lecture_data.get('metadata', {}).get('speaker_information', '')

    theme: dict = {}
    _html_path = Path(html_path) if html_path else output_path.with_suffix('.html')
    if _html_path.exists():
        try:
            from bs4 import BeautifulSoup
            html_text = _html_path.read_text(encoding='utf-8')
            soup      = BeautifulSoup(html_text, 'html.parser')
            deck_el   = soup.find('deck-stage')
            if deck_el:
                theme = _parse_theme(deck_el.get('style', ''))
        except Exception:
            pass

    with open(layout_log_path, encoding='utf-8') as f:
        log_entries = json.load(f)

    output_dir = output_path.parent

    manifest_slides: list[dict] = []
    for i, entry in enumerate(log_entries):
        slide = _layout_log_to_slide(entry, output_dir, i)
        manifest_slides.append(slide)

    manifest: dict = {
        'lecture_title': lecture_title,
        'speaker':       speaker,
        'date':          _dt.date.today().strftime('%B %Y'),
        'theme':         theme,
        'slides':        manifest_slides,
    }

    # Render formula PNGs and replace formula_latex with formula_image_path
    for slide in manifest_slides:
        latex = slide.pop('formula_latex', None)
        if latex:
            slide['formula_text'] = latex

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json',
                                     delete=False, encoding='utf-8') as mf:
        json.dump(manifest, mf, ensure_ascii=False)
        manifest_path = Path(mf.name)

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ['node', str(_GEN_JS),
             '--manifest', str(manifest_path),
             '--output',   str(output_path)],
            capture_output=True, text=True, timeout=120,
        )
        if result.stdout:
            print(result.stdout.strip())
        if result.returncode != 0:
            err = result.stderr.strip() or 'unknown error'
            raise RuntimeError(f'deck_pptx_gen.js exited {result.returncode}: {err}')
    finally:
        manifest_path.unlink(missing_ok=True)

    return len(manifest_slides)
