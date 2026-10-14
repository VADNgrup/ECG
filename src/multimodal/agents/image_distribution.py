"""Each slide's figure: the one the Packet Builder chose for it (a figure its passages cite, or one close in meaning on
their pages). The Writer already wrote the slide for that figure, so nothing is matched or chosen again here."""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.utils import lang
from src.utils.config import Config
from src.workflow.scope import find_kb_dir


class ImageDistribution:

    def __init__(self):
        self.last_no_image_slide_numbers: List[int] = []

    def distribute_images(self, lecture_id: str, lecture_dict: Dict[str, Any], aggregated_media: Dict[str, Any],
                          used_images: Set[str], document_id: str = None) -> List[Dict[str, Any]]:
        document_id = document_id or lecture_dict.get('metadata', {}).get('source_document_id', lecture_id)
        self._use_document_labels(document_id)
        content_slides = self._extract_content_slides(lecture_dict.get('slides', []))
        if not content_slides:
            print('  No content-type slides found. Skipping image distribution.')
            return []
        print(f"\n{'=' * 60}")
        print(f'  Image Distribution — {len(content_slides)} content slides')
        print(f"{'=' * 60}")
        chosen = self._packet_images(lecture_id)
        numbers = [int(s.get('slide', {}).get('slide_number', 0)) for s in content_slides]
        return self._save(lecture_id, chosen, [n for n in numbers if n not in {d['slide_number'] for d in chosen}])

    def _packet_images(self, lecture_id: str) -> List[Dict[str, Any]]:
        """The figures the deck's packets chose (up to MAX_SLIDE_IMAGES per slide, in the packet's order)."""
        path = Config.LECTURES_DIR / lecture_id / f'{lecture_id}_slide_packets.json'
        try:
            packets = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        rows = [p for p in packets if isinstance(p, dict) and not p.get('section_opener')] if isinstance(packets, list) else []
        out = []
        for p in rows:
            images = [a for a in p.get('section_assets') or [] if isinstance(a, dict) and a.get('type') == 'image']
            for image in images[:Config.MAX_SLIDE_IMAGES]:
                resolved = self._resolve_image_path(str(image.get('id') or ''))
                if resolved:
                    out.append({'slide_number': int(p['slide_number']), 'image_path': resolved, 'score': 1.0,
                                'source': 'packet', 'caption': image.get('real_caption') or image.get('caption') or ''})
        print(f'  [packet-images] {len(out)} figure(s) chosen by the Packet Builder, used as they are')
        return out

    def _save(self, lecture_id: str, distributions: List[Dict[str, Any]], without: List[int]) -> List[Dict[str, Any]]:
        self.last_no_image_slide_numbers = without
        output_path = Config.LECTURES_DIR / lecture_id / f'{lecture_id}_image_distribution.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(distributions, indent=2, ensure_ascii=False), encoding='utf-8')
        return distributions

    @staticmethod
    def _resolve_image_path(file_path: str) -> str:
        if not file_path:
            return ''
        candidates = [Path(file_path), Config.DATA_DIR / file_path, Config.BASE_DIR / file_path]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return ''

    @staticmethod
    def _extract_content_slides(slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Content slides that can show a figure: not a chapter opener, not a formula or table slide."""
        content_slides = []
        for slide_entry in slides:
            slide_meta = slide_entry.get('slide', {})
            if slide_meta.get('slide_type') in {'greeting', 'goodbye'} or slide_meta.get('is_section_opener'):
                continue
            if slide_meta.get('table') is not None or slide_meta.get('latex_block_formula') is not None:
                continue
            content_slides.append(slide_entry)
        return content_slides

    @staticmethod
    def _use_document_labels(document_id: Optional[str]) -> None:
        """Figure/table labels of the document's own captions (for numbered-reference matching)."""
        kb_dir = find_kb_dir(document_id) if document_id else None
        if kb_dir is not None:
            lang.use_labels(lang.labels_for_kb(kb_dir))
