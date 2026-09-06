import os
import sys
import json
import logging
import subprocess
from pathlib import Path
import fitz
import numpy as np
from PIL import Image
from sklearn.cluster import KMeans
from src.utils.config import Config
from src.utils.fuzzy_distance import fuzzy_distance
logger = logging.getLogger(__name__)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

class SlideImproving:
    SLIDE_IMPROVING_MAX_ITERATION = Config.SLIDE_IMPROVING_MAX_ITERATION

    def __init__(self, md_path: str, lecture_json_path: str, lecture_title: str='', speaker_information: str='', theme: str='frankfurt', font: str='STIX Two Text'):
        _given = Path(md_path)
        _generator_dir = Path(__file__).parent
        if _given.is_absolute() and _given.exists():
            self.md_path = _given
        elif _given.resolve().exists():
            self.md_path = _given.resolve()
        elif (_generator_dir / _given).exists():
            self.md_path = (_generator_dir / _given).resolve()
        else:
            raise FileNotFoundError(f"Slide file not found: '{md_path}'.\nTried:\n  {_given.resolve()}\n  {(_generator_dir / _given).resolve()}")
        self.slidev_dir = self.md_path.parent
        self.lecture_id = self.md_path.stem
        self.theme = theme
        self.font = font
        _lec_json = Path(lecture_json_path).resolve()
        self.layout_dist_path = _lec_json.parent / f'{self.lecture_id}_layout_distribution.json'
        self.lecture_title = lecture_title
        self.speaker_information = speaker_information
        self._step = float(Config.SLIDE_IMAGE_WIDTH_STEP)
        self._bg_color: np.ndarray | None = None

    def run(self) -> None:
        logger.info(f"[SlideImproving] Starting for lecture '{self.lecture_id}'")
        entries = self._read_layout_dist()
        image_slides = [e for e in entries if 'image_width' in e.get('args', {})]
        if not image_slides:
            logger.info('[SlideImproving] No image slides found — nothing to optimise.')
        else:
            slide_states: dict[int, dict] = {entry['slide_num']: {'slide_num': entry['slide_num'], 'w': float(entry['args']['image_width'].replace('%', '')), 'expected': self._get_expected_content(entry['args']), 'phase': 1, 'iter_phase1': 0, 'iter_phase2': 0, 'converged': False, 'f_cur': None, 'g_cur': None, 'prev_w': float(entry['args']['image_width'].replace('%', '')), 'prev_g': 1.0, 'stop_reason': 'none', 'final_w': None, 'cand_w': None} for entry in image_slides}
            pdf_path = self.slidev_dir / f'{self.lecture_id}-export.pdf'
            logger.info('[SlideImproving] Initial export for bg-colour detection …')
            self._replay_layouts(entries)
            self._export_pdf_full()
            ref_page_idx = image_slides[-1]['slide_num'] - 1
            with fitz.open(str(pdf_path)) as doc:
                self._init_bg_color_from_doc(doc, ref_page_idx)
                for (slide_num, state) in slide_states.items():
                    page_idx = slide_num - 1
                    if page_idx < len(doc):
                        (f, g) = self._evaluate_page(doc, page_idx, state['expected'])
                        state['f_cur'] = f
                        state['g_cur'] = g
                        logger.info(f"[SlideImproving] Slide {slide_num}: initial w={state['w']:g}%  f={f:.4f}  g={g:.4f}")
            max_rounds = self.SLIDE_IMPROVING_MAX_ITERATION * 2 + 2
            for round_num in range(1, max_rounds + 1):
                active = [s for s in slide_states.values() if not s['converged']]
                if not active:
                    logger.info(f'[SlideImproving] All slides converged after round {round_num - 1}.')
                    break
                logger.info(f'\n[SlideImproving] ── Round {round_num}/{max_rounds} ({len(active)} active slides) ──')
                for state in active:
                    slide_num = state['slide_num']
                    if state['phase'] == 1 and state['f_cur'] == 0.0:
                        logger.info(f'[SlideImproving] Slide {slide_num}: f=0 → transitioning to Phase 2 (grow)')
                        state['phase'] = 2
                        state['prev_w'] = state['w']
                        state['prev_g'] = state['g_cur']
                    if state['phase'] == 1:
                        cand_w = state['w'] - self._step
                        if cand_w <= 0:
                            logger.info(f"[SlideImproving] Slide {slide_num}: Phase 1 hit lower bound → converge (f={state['f_cur']:.4f})")
                            state['final_w'] = state['w']
                            state['converged'] = True
                            continue
                        state['cand_w'] = cand_w
                    elif state['phase'] == 2:
                        cand_w = state['w'] + self._step
                        if cand_w >= 100:
                            logger.info(f"[SlideImproving] Slide {slide_num}: Phase 2 hit upper bound → converge at w={state['w']:g}%")
                            state['final_w'] = state['w']
                            state['converged'] = True
                            continue
                        state['cand_w'] = cand_w
                pending = [s for s in active if s['cand_w'] is not None]
                if not pending:
                    break
                tmp_entries = list(entries)
                for state in pending:
                    slide_num = state['slide_num']
                    idx = next((i for (i, e) in enumerate(tmp_entries) if e['slide_num'] == slide_num))
                    tmp_entries[idx] = {**tmp_entries[idx], 'args': {**tmp_entries[idx]['args'], 'image_width': f"{state['cand_w']:g}%"}}
                self._replay_layouts(tmp_entries)
                self._export_pdf_full()
                with fitz.open(str(pdf_path)) as doc:
                    for state in pending:
                        slide_num = state['slide_num']
                        page_idx = slide_num - 1
                        cand_w = state['cand_w']
                        state['cand_w'] = None
                        if page_idx >= len(doc):
                            continue
                        (c_f, c_g) = self._evaluate_page(doc, page_idx, state['expected'])
                        if state['phase'] == 1:
                            state['w'] = cand_w
                            state['f_cur'] = c_f
                            state['g_cur'] = c_g
                            state['iter_phase1'] += 1
                            logger.info(f'[SlideImproving] Slide {slide_num}: Phase 1 r{round_num}  w={cand_w:g}%  f={c_f:.4f}  g={c_g:.4f}')
                            if c_f == 0.0:
                                logger.info(f'[SlideImproving] Slide {slide_num}: Phase 1 — f=0 reached at w={cand_w:g}%')
                            elif state['iter_phase1'] >= self.SLIDE_IMPROVING_MAX_ITERATION:
                                logger.info(f'[SlideImproving] Slide {slide_num}: Phase 1 — max iter, f={c_f:.4f} > 0, converging')
                                state['final_w'] = state['w']
                                state['converged'] = True
                        elif state['phase'] == 2:
                            state['iter_phase2'] += 1
                            logger.info(f'[SlideImproving] Slide {slide_num}: Phase 2 r{round_num}  w={cand_w:g}%  f={c_f:.4f}  g={c_g:.4f}')
                            if c_f > 0.0:
                                state['stop_reason'] = 'f_violated'
                                final_w = state['prev_w'] if state['prev_w'] < state['w'] else state['w']
                                logger.info(f'[SlideImproving] Slide {slide_num}: Phase 2 — f violated → step back to w={final_w:g}%')
                                state['final_w'] = final_w
                                state['converged'] = True
                            elif c_g >= state['g_cur']:
                                state['stop_reason'] = 'g_plateau'
                                logger.info(f"[SlideImproving] Slide {slide_num}: Phase 2 — g plateau ({c_g:.4f} >= {state['g_cur']:.4f}) → keep w={state['w']:g}%")
                                state['final_w'] = state['w']
                                state['converged'] = True
                            else:
                                state['prev_w'] = state['w']
                                state['prev_g'] = state['g_cur']
                                state['w'] = cand_w
                                state['f_cur'] = c_f
                                state['g_cur'] = c_g
                                if state['iter_phase2'] >= self.SLIDE_IMPROVING_MAX_ITERATION:
                                    logger.info(f'[SlideImproving] Slide {slide_num}: Phase 2 — max iter → converge at w={cand_w:g}%')
                                    state['final_w'] = state['w']
                                    state['converged'] = True
            for state in slide_states.values():
                slide_num = state['slide_num']
                final_w = state['final_w'] if state['final_w'] is not None else state['w']
                idx = next((i for (i, e) in enumerate(entries) if e['slide_num'] == slide_num))
                entries[idx] = {**entries[idx], 'args': {**entries[idx]['args'], 'image_width': f'{final_w:g}%'}}
                logger.info(f'[SlideImproving] Slide {slide_num}: final image_width={final_w:g}%')
            self._write_layout_dist(entries)
            self._replay_layouts(entries)
        self._export_pdf_full()
        logger.info('[SlideImproving] Finished.')

    def _init_bg_color_from_doc(self, doc: fitz.Document, page_idx: int) -> None:
        if page_idx >= len(doc):
            logger.warning(f'[SlideImproving] Requested page {page_idx} out of bounds (doc has {len(doc)} pages). Using last page instead.')
            page_idx = len(doc) - 1
        if page_idx < 0:
            logger.error('[SlideImproving] Document has no pages. Cannot detect background color.')
            return
        page = doc[page_idx]
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        (W, H) = img.size
        arr = np.array(img)
        pixels = arr.reshape(-1, 3).astype(float)
        sample_idx = np.random.choice(len(pixels), min(5000, len(pixels)), replace=False)
        sample = pixels[sample_idx]
        km = KMeans(n_clusters=3, n_init=3, random_state=42)
        km.fit(sample)
        (labels, counts) = np.unique(km.labels_, return_counts=True)
        bg_label = labels[np.argmax(counts)]
        self._bg_color = km.cluster_centers_[bg_label]
        logger.info(f'[SlideImproving] slide size={W}×{H} px  bg_color=({self._bg_color[0]:.0f},{self._bg_color[1]:.0f},{self._bg_color[2]:.0f})')

    def _evaluate_page(self, doc: fitz.Document, page_idx: int, expected: str) -> tuple[float, float]:
        page = doc[page_idx]
        text = page.get_text('text').strip()
        fuzzy_score = fuzzy_distance(expected, text)
        f = 1.0 - fuzzy_score / 100.0
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
        arr = np.array(img).astype(float)
        dist = np.linalg.norm(arr - self._bg_color, axis=2)
        g = float(np.mean(dist < 25))
        return (f, g)

    @staticmethod
    def _get_expected_content(args: dict) -> str:
        parts: list[str] = []
        for key in ('title', 'sub_title_1', 'sub_title_2'):
            if key in args and args[key]:
                parts.append(str(args[key]))
        for key in ('content', 'toc_content', 'sub_content_1', 'sub_content_2'):
            val = args.get(key)
            if isinstance(val, list):
                parts.extend((str(v) for v in val))
            elif val:
                parts.append(str(val))
        if 'latex_formula_block' in args:
            parts.append(str(args['latex_formula_block']))
        if 'caption' in args:
            parts.append(str(args['caption']))
        if 'caption1' in args and 'caption2' in args:
            parts.append(str(args['caption1']))
            parts.append(str(args['caption2']))
        return ' '.join(parts)

    def _read_layout_dist(self) -> list[dict]:
        with open(self.layout_dist_path, encoding='utf-8') as f:
            return json.load(f)

    def _write_layout_dist(self, entries: list[dict]) -> None:
        with open(self.layout_dist_path, 'w', encoding='utf-8') as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

    def _replay_layouts(self, entries: list[dict]) -> None:
        from src.generator.slide_layout_manager import SlideLayoutManager
        mgr = SlideLayoutManager(theme=self.theme, font_sans=self.font, font_serif=self.font, font_mono=self.font, title=self.lecture_title, author=self.speaker_information)
        doc = ''
        for entry in entries:
            func_name = entry['layout_function_name']
            args = entry['args']
            func = getattr(mgr, func_name)
            doc += func(**args)
        self.md_path.write_text(doc, encoding='utf-8')

    def _export_pdf_full(self) -> None:
        cmd = f'slidev export "{self.lecture_id}.md"'
        logger.info(f'[SlideImproving] Full export: {cmd}')
        _env = os.environ.copy()
        _env['NODE_OPTIONS'] = '--max-old-space-size=4096'
        result = subprocess.run(['npm', 'exec', '--', 'slidev', 'export', f'{self.lecture_id}.md'], cwd=str(self.slidev_dir), capture_output=True, text=True, encoding='utf-8', errors='replace', shell=False, env=_env)
        if result.returncode != 0:
            error_msg = f'STDOUT: {result.stdout}\nSTDERR: {result.stderr}'
            logger.error(f'[SlideImproving] slidev export (full) failed:\n{error_msg}')
            raise RuntimeError(f'slidev export failed with return code {result.returncode}.\n{error_msg}')
        logger.info('[SlideImproving] Full export complete.')

    def _export_pdf_single(self, slide_num: int) -> None:
        cmd = f'slidev export "{self.lecture_id}.md" --range {slide_num} --per-slide'
        logger.info(f'[SlideImproving] Exporting slide {slide_num}: {cmd}')
        _env = os.environ.copy()
        _env['NODE_OPTIONS'] = '--max-old-space-size=4096'
        result = subprocess.run(['npm', 'exec', '--', 'slidev', 'export', f'{self.lecture_id}.md', '--range', str(slide_num), '--per-slide'], cwd=str(self.slidev_dir), capture_output=True, text=True, encoding='utf-8', errors='replace', shell=False, env=_env)
        if result.returncode != 0:
            logger.error(f'[SlideImproving] slidev export (single) failed:\n{result.stderr}')
            raise RuntimeError(f'slidev export failed: {result.stderr}')

    def _extract_text_pymupdf(self) -> str:
        pdf_path = self.slidev_dir / f'{self.lecture_id}-export.pdf'
        try:
            doc = fitz.open(str(pdf_path))
            text = doc[0].get_text('text')
            doc.close()
            return text.strip()
        except Exception as e:
            logger.error(f'[SlideImproving] PyMuPDF text extraction failed: {e}')
            return ''

    def _compute_bg_ratio(self) -> float:
        pdf_path = self.slidev_dir / f'{self.lecture_id}-export.pdf'
        try:
            doc = fitz.open(str(pdf_path))
            page = doc[0]
            mat = fitz.Matrix(2, 2)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
            doc.close()
        except Exception as e:
            logger.error(f'[SlideImproving] PDF→image failed for bg_ratio: {e}')
            return 0.0
        arr = np.array(img).astype(float)
        dist = np.linalg.norm(arr - self._bg_color, axis=2)
        return float(np.mean(dist < 25))
if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(levelname)s | %(message)s')
    parser = argparse.ArgumentParser(description='Optimise image_width per slide using PyMuPDF + bg-ratio objective.')
    parser.add_argument('md_path', help='Path to the .md slide file')
    parser.add_argument('--lecture-json', required=True, help='Path to lecture JSON file')
    parser.add_argument('--title', default='', help='Lecture title')
    parser.add_argument('--speaker', default='', help='Speaker information')
    args = parser.parse_args()
    improver = SlideImproving(md_path=args.md_path, lecture_json_path=args.lecture_json, lecture_title=args.title, speaker_information=args.speaker)
    improver.run()