from typing import List, Dict, Optional, Any, Set
import json
import re
from pathlib import Path
from src.utils.config import Config

class ImageDistribution:

    def __init__(self):
        self.threshold = Config.IMAGE_MATCH_THRESHOLD
        self.max_images_per_slide = Config.IMAGE_MATCH_MAX_IMAGES_PER_SLIDE
        self.stopwords = self._load_stopwords()

    def distribute_images(self, lecture_id: str, lecture_dict: Dict[str, Any], aggregated_media: Dict[str, Any], used_images: Set[str], document_id: str = None) -> List[Dict[str, Any]]:
        self.aggregated_media = aggregated_media
        document_id = document_id or lecture_dict.get('metadata', {}).get('source_document_id', lecture_id)
        slides = lecture_dict.get('slides', [])
        content_slides = self._extract_content_slides(slides)
        if not content_slides:
            print('  No content-type slides found. Skipping image distribution.')
            return []
        print(f"\n{'=' * 60}")
        print(f'  Image Distribution — {len(content_slides)} content slides')
        print(f"{'=' * 60}")
        existing_images = aggregated_media.get('images', [])
        section_asset_tokens_by_slide, section_match_pages_by_slide, page_asset_map = self._load_section_info_by_slide(lecture_id, document_id)
        image_pool = self._step0_summarise_images(existing_images, page_asset_map)
        print(f'\n  Step 0 complete: {len(image_pool)} context images prepared')
        source_pages_by_slide = self._load_slide_source_pages(lecture_id)
        slide_pool = self._step1_prepare_slides(content_slides, source_pages_by_slide, aggregated_media.get('page_count'), section_asset_tokens_by_slide, section_match_pages_by_slide)
        print(f'  Step 1 complete: {len(slide_pool)} content slides prepared')
        # Collect source pages for slides whose content was extracted as formula or table —
        # images from those pages should not be placed on any slide as raw images.
        formula_table_pages: Set[int] = set()
        for slide_entry in slides:
            slide_meta = slide_entry.get('slide', {})
            if slide_meta.get('latex_block_formula') or (slide_meta.get('table') or {}).get('table_markdown'):
                sn = slide_meta.get('slide_number', -1)
                try:
                    sn = int(sn)
                except (TypeError, ValueError):
                    continue
                for pg in source_pages_by_slide.get(sn, []):
                    formula_table_pages.add(pg)
        if formula_table_pages:
            print(f'  [filter] Excluding images from formula/table source pages: {sorted(formula_table_pages)}')
        distributions = self._step2_match_images_to_slides(image_pool, slide_pool, used_images, formula_table_pages)
        print(f'  Step 2 complete: {len(distributions)} images matched to slides')
        
        assigned_slide_numbers = {d['slide_number'] for d in distributions}
        slides_without_images = [s for s in slide_pool if s['slide_number'] not in assigned_slide_numbers]
        
        if slides_without_images:
            print(f'\n  Note: {len(slides_without_images)} slides do not have suitable images from the document. Skipping web search as requested.')
        output_path = Config.LECTURES_DIR / lecture_id / f'{lecture_id}_image_distribution.json'
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(distributions, f, indent=2, ensure_ascii=False)
        print(f'\n  Saved image distributions to: {output_path}')
        return distributions

    def _step0_summarise_images(self, existing_images: List[Dict[str, Any]], page_asset_map: Optional[Dict[int, str]] = None) -> List[Dict[str, Any]]:
        image_pool: List[Dict[str, Any]] = []
        for img in existing_images:
            image_id = img.get('image_id', '')
            file_path = img.get('file_path', '')
            caption = img.get('caption', '')
            reference_context = img.get('reference_context') or None
            metadata = img.get('metadata', {})
            resolved_file_path = self._resolve_image_path(file_path)
            if not resolved_file_path:
                print(f'    [skip] Image file not found: {file_path}')
                continue
            if reference_context:
                image_description = self._clean_text(f'{caption} {reference_context}')
            else:
                image_description = self._clean_text(caption)
            # Enrich from compact page section assets when caption is empty
            if not image_description and page_asset_map:
                page_num = self._infer_page_number(file_path)
                if page_num and page_num in page_asset_map:
                    image_description = page_asset_map[page_num]
                    print(f'    [enrich] {image_id} p.{page_num}: {image_description[:60]}')
            if not image_description:
                image_description = "illustrative figure document diagram image"
            image_pool.append({'image_id': image_id, 'file_path': resolved_file_path, 'caption': caption, 'reference_context': reference_context, 'metadata': metadata, 'image_description': image_description, 'page_number': self._infer_page_number(file_path), 'tokens': self._tokens(image_description)})
            print(f'    [desc] {image_id}: {image_description[:80]}...')
        return image_pool

    @staticmethod
    def _resolve_image_path(file_path: str) -> str:
        if not file_path:
            return ''
        candidates = [Path(file_path), Config.DATA_DIR / file_path, Config.BASE_DIR / file_path]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
        return ''

    def _extract_content_slides(self, slides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        skip_types = {'greeting', 'goodbye'}
        content_slides = []
        for slide_entry in slides:
            slide_meta = slide_entry.get('slide', {})
            slide_type = slide_meta.get('slide_type', '')
            if slide_type in skip_types:
                continue
            if slide_meta.get('table') is not None or slide_meta.get('latex_block_formula') is not None:
                continue
            content_slides.append(slide_entry)
        return content_slides

    def _step1_prepare_slides(self, content_slides: List[Dict[str, Any]], source_pages_by_slide: Dict[int, List[int]], page_count: Optional[int], section_asset_tokens_by_slide: Optional[Dict[int, Set[str]]] = None, section_match_pages_by_slide: Optional[Dict[int, Set[int]]] = None) -> List[Dict[str, Any]]:
        slide_pool: List[Dict[str, Any]] = []
        total_slides = len(content_slides)
        section_asset_tokens_by_slide = section_asset_tokens_by_slide or {}
        section_match_pages_by_slide = section_match_pages_by_slide or {}
        for idx, slide_entry in enumerate(content_slides):
            slide_meta = slide_entry.get('slide', {})
            slide_number = slide_meta.get('slide_number', -1)
            content = slide_entry.get('content', [])
            if isinstance(content, list):
                bullet_points = [bp for bp in content if isinstance(bp, str) and bp.strip()]
            elif isinstance(content, str):
                bullet_points = [content]
            else:
                bullet_points = []
            if not bullet_points:
                continue
            slide_title = slide_meta.get('slide_title', '')
            slide_text = self._clean_text(' '.join([slide_title] + bullet_points))
            page_range = self._source_page_range(source_pages_by_slide.get(slide_number)) or self._fallback_page_range(idx, total_slides, page_count)
            slide_pool.append({'slide_number': slide_number, 'slide_title': slide_title, 'bullet_points': bullet_points, 'tokens': self._tokens(slide_text), 'page_range': page_range, 'section_asset_tokens': section_asset_tokens_by_slide.get(slide_number, set()), 'section_match_pages': section_match_pages_by_slide.get(slide_number, set())})
        return slide_pool

    def _step2_match_images_to_slides(self, image_pool: List[Dict[str, Any]], slide_pool: List[Dict[str, Any]], used_images: Set[str], formula_table_pages: Optional[Set[int]] = None) -> List[Dict[str, Any]]:
        distributions: List[Dict[str, Any]] = []
        formula_table_pages = formula_table_pages or set()

        # 1. Filter out banned or invalid images
        banned_image_paths = set()
        if hasattr(self, 'aggregated_media'):
            tables = self.aggregated_media.get('tables', [])
            for t in tables:
                caption = t.get('table_caption', '')
                md = t.get('markdown', '')
                for path in re.findall(r'!\[.*?\]\((.*?)\)', caption + '\n' + md):
                    banned_image_paths.add(Path(path).name)

        valid_images = []
        for img in image_pool:
            img_name = Path(img['file_path']).name
            caption_lower = (img.get('caption', '') or '').lower()
            ref_lower = (img.get('reference_context', '') or '').lower()
            desc_lower = (img.get('image_description', '') or '').lower()
            combined_lower = caption_lower + ' ' + ref_lower + ' ' + desc_lower

            # Skip images from pages where formula/table content was extracted
            img_page = img.get('page_number')
            if img_page and img_page in formula_table_pages:
                print(f'    [skip] {img_name} — page {img_page} is a formula/table source page')
                continue

            # Caption/description signals that this image IS the formula or table content
            is_formula = (
                ('general form' in combined_lower and 'constraint' in combined_lower) or
                ('objective function' in combined_lower and 'constraint' in combined_lower) or
                ('mathematical formulation' in combined_lower) or
                bool(re.search(r'\bequation\s*\(?\d+\)?|\bformula\b.*\bconstraint', combined_lower)) or
                bool(re.search(r'\\begin\{aligned\}|\\frac\{|\\sum_\{|\\le\b|\\ge\b', combined_lower))
            )
            is_table = bool(re.search(
                r'\btable\s*\d+|\btable\s*[ivxlcdm]+\b|\btable\s*[A-Z]\b|'
                r'\btab\.\s*\d|\bdata\s+table\b|\bmatrix\b.*\bconstraint',
                combined_lower
            ))
            if img_name in used_images or img_name in banned_image_paths or is_formula or is_table:
                if is_formula or is_table:
                    print(f'    [skip] {img_name} — formula/table content detected in caption/description')
                continue
            valid_images.append(img)
            
        if not valid_images or not slide_pool:
            return distributions
            
        # 2. Construct LLM prompt
        slide_info = []
        for slide in slide_pool:
            slide_num = slide['slide_number']
            title = slide['slide_title']
            content = " ".join(slide.get('bullet_points', []))
            # Restrict length to save tokens
            if len(content) > 300:
                content = content[:300] + "..."
            page_info = f", Pages: {slide.get('page_range')}" if slide.get('page_range') else ""
            slide_info.append(f"[Slide {slide_num}] Title: {title}\nContent: {content}{page_info}")
            
        image_info = []
        image_map = {}
        for img in valid_images:
            img_id = img['image_id']
            img_name = Path(img['file_path']).name
            desc = self._best_caption(img)
            if not desc:
                desc = img.get('image_description', '')
            if len(desc) > 200:
                desc = desc[:200] + "..."
            page = img.get('page_number', 'unknown')
            image_info.append(f"[Image {img_id}] Page: {page}, File: {img_name}\nDescription: {desc}")
            image_map[img_id] = img
            
        slide_text_block = "\n\n".join(slide_info)
        image_text_block = "\n\n".join(image_info)
        
        prompt = f"""You are an intelligent presentation designer. Your task is to select the most relevant image(s) for each slide based on the slide's content and the image descriptions.

Slides:
{"="*40}
{slide_text_block}
{"="*40}

Images Available:
{"="*40}
{image_text_block}
{"="*40}

Instructions:
1. Assign an image to a slide ONLY IF it is highly relevant to the slide's content.
2. An image can be assigned to AT MOST one slide.
3. A slide can have AT MOST {self.max_images_per_slide} image(s).
4. If no images are suitable for a slide, do not assign any.
5. Pay attention to the Page numbers. Images are more likely to match slides that cover the same page ranges, but semantic relevance is the most important factor.
6. CRITICAL RULE: For slides discussing high-level topics like 'Overview', 'System Design', or 'Architecture', STRONGLY PREFER images that represent the overall system architecture or overview over images that show specific sub-components (like a single transformer layer).

Output your assignments in JSON format EXACTLY like this:
{{
    "assignments": [
        {{"slide_number": 1, "image_id": "img_001", "reason": "short explanation"}},
        {{"slide_number": 3, "image_id": "img_005", "reason": "short explanation"}}
    ]
}}
"""
        from src.utils.llm import chat
        messages = [
            {"role": "system", "content": "You are a helpful presentation design assistant. Only output valid JSON."},
            {"role": "user", "content": prompt}
        ]
        
        try:
            print(f"    [match] Calling LLM to distribute {len(valid_images)} images to {len(slide_pool)} slides...")
            response = chat(model=Config.LLM_MODEL_NAME, messages=messages, temperature=0.1)
            
            # Find JSON block
            json_str = response
            if "```json" in response:
                json_str = response.split("```json")[1].split("```")[0]
            elif "```" in response:
                json_str = response.split("```")[1].split("```")[0]
                
            data = json.loads(json_str.strip())
            assignments = data.get("assignments", [])
            
            # 3. Process assignments
            slide_image_count = {s['slide_number']: 0 for s in slide_pool}
            assigned_images = set()
            
            for assignment in assignments:
                slide_num = assignment.get("slide_number")
                img_id = assignment.get("image_id")
                
                if slide_num not in slide_image_count:
                    continue
                if slide_image_count[slide_num] >= self.max_images_per_slide:
                    continue
                if img_id not in image_map or img_id in assigned_images:
                    continue
                    
                img = image_map[img_id]
                distributions.append({
                    'slide_number': slide_num,
                    'image_path': img['file_path'],
                    'score': 1.0,
                    'source': 'existing',
                    'caption': self._best_caption(img)
                })
                used_images.add(Path(img['file_path']).name)
                assigned_images.add(img_id)
                slide_image_count[slide_num] += 1
                
                print(f"    [match] LLM assigned {img_id} → slide {slide_num} (reason: {assignment.get('reason', '')})")
                
            for img in valid_images:
                if img['image_id'] not in assigned_images:
                    print(f"    [skip]  {img['image_id']} — not selected by LLM")
                    
        except Exception as e:
            print(f"    [error] LLM image matching failed: {e}")
            print(f"    [match] Fallback: no images assigned due to error.")
            
        return distributions

    @staticmethod
    def _infer_page_number(file_path: str) -> Optional[int]:
        match = re.search(r'page_(\d+)', file_path or '')
        return int(match.group(1)) if match else None

    @staticmethod
    def _source_page_range(source_pages: Optional[List[int]]) -> Optional[tuple[int, int]]:
        pages = sorted({int(page) for page in (source_pages or []) if isinstance(page, int) or str(page).isdigit()})
        if not pages:
            return None
        return (pages[0], pages[-1])

    @staticmethod
    def _fallback_page_range(slide_index: int, total_slides: int, page_count: Optional[int]) -> Optional[tuple[int, int]]:
        try:
            page_count = int(page_count or 0)
        except (TypeError, ValueError):
            return None
        if not page_count or page_count <= 0 or total_slides <= 0:
            return None
        center = round(1 + slide_index * max(0, page_count - 1) / max(1, total_slides - 1))
        start = max(1, center - 1)
        end = min(page_count, center + 1)
        return (start, end)

    @staticmethod
    def _load_slide_source_pages(lecture_id: str) -> Dict[int, List[int]]:
        packet_path = Config.LECTURES_DIR / lecture_id / f'{lecture_id}_slide_packets.json'
        if not packet_path.exists():
            return {}
        try:
            packets = json.loads(packet_path.read_text(encoding='utf-8'))
        except Exception:
            return {}
        result: Dict[int, List[int]] = {}
        for packet in packets if isinstance(packets, list) else []:
            slide_number = packet.get('slide_number')
            pages = packet.get('source_pages') or []
            if slide_number is None:
                continue
            clean_pages = [int(page) for page in pages if isinstance(page, int) or str(page).isdigit()]
            if clean_pages:
                result[int(slide_number)] = clean_pages
        return result

    def _load_section_info_by_slide(self, lecture_id: str, document_id: str = None):
        doc_id = document_id or lecture_id
        context_path = Config.CONTEXT_DIR / f"{doc_id}.json"
        packet_path = Config.LECTURES_DIR / lecture_id / f"{lecture_id}_slide_packets.json"
        empty: tuple = ({}, {}, {})
        if not packet_path.exists():
            return empty
        try:
            packets = json.loads(packet_path.read_text(encoding="utf-8"))
            context_data = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {}
        except Exception:
            return empty

        page_asset_map: Dict[int, str] = {}
        for card in context_data.get("page_insights", []):
            page = card.get("page") if isinstance(card, dict) else getattr(card, "page", None)
            if page is not None:
                page_asset_map[page] = ""

        section_asset_tokens: Dict[int, Set[str]] = {}
        section_match_pages: Dict[int, Set[int]] = {}

        for packet in (packets if isinstance(packets, list) else []):
            slide_number = packet.get("slide_number")
            if slide_number is None:
                continue
            slide_num = int(slide_number)

            packet_home_pages = set(packet.get("home_pages") or [])
            if packet_home_pages:
                section_match_pages.setdefault(slide_num, set()).update(packet_home_pages)
                
            assets = packet.get("section_assets", [])
            if assets:
                section_asset_tokens.setdefault(slide_num, set()).update(
                    self._tokens(" ".join(assets))
                )

        return section_asset_tokens, section_match_pages, page_asset_map

    @staticmethod
    def _page_score(page_number: Optional[int], page_range: Optional[tuple[int, int]]) -> float:
        if page_number is None or page_range is None:
            return 1.0
        start, end = page_range
        if start <= page_number <= end:
            return 1.0
        distance = min(abs(page_number - start), abs(page_number - end))
        if distance == 1:
            return 0.85
        elif distance == 2:
            return 0.65
        elif distance <= 4:
            return 0.40
        elif distance <= 8:
            return 0.25
        return 0.15

    def _shorten_caption(self, caption: str, max_words: int=15) -> str:
        if not caption or len(caption.split()) <= max_words:
            return caption
        return ' '.join(caption.split()[:max_words]) + '...'

    def _best_caption(self, img: Dict[str, Any]) -> str:
        candidates = [
            img.get('caption', ''),
            img.get('reference_context', ''),
            img.get('image_description', ''),
        ]
        for candidate in candidates:
            clean = self._clean_caption(candidate)
            if clean:
                return self._shorten_caption(clean)
        return ''

    @staticmethod
    def _clean_caption(text: str) -> str:
        clean = re.sub(r'\s+', ' ', text or '').strip()
        clean = re.sub(r'!\[[^\]]*\]\([^)]+\)', ' ', clean, flags=re.IGNORECASE)
        clean = re.sub(r'#{1,6}\s*', ' ', clean)
        clean = re.sub(r'\*+\s*(?:figure|fig\.?|table|hình|bảng)\s*:\s*', ' ', clean, flags=re.IGNORECASE)
        clean = clean.replace('*', ' ')
        clean = re.sub(r'<!--\s*PAGE\s+\d+\s*-->', ' ', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\b[a-zA-Z0-9_\-/]+\.(?:png|jpe?g|webp)\b', ' ', clean, flags=re.IGNORECASE)
        clean = re.sub(r'/assets/\S+|\bassets/\S+', ' ', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\s+', ' ', clean).strip(' -:;,.')
        if not clean:
            return ''
        if '/' in clean and len(clean.split()) <= 4:
            return ''
        if any(token in clean for token in ['![', '](', '##', '# ']):
            return ''
        if len(clean.split()) > 12:
            clean = ' '.join(clean.split()[:12])
        return clean

    def _tokens(self, text: str) -> Set[str]:
        words = re.findall(r'[A-Za-zÀ-ỹ0-9]{2,}', (text or '').lower())
        return {word for word in words if word not in self.stopwords}

    @staticmethod
    def _load_stopwords() -> Set[str]:
        path = Config.IMAGE_MATCH_STOPWORDS_PATH
        if not path.exists():
            return set()
        return {
            line.strip().lower()
            for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()
        }

    @staticmethod
    def _token_similarity(left: Set[str], right: Set[str]) -> float:
        if not left or not right:
            return 0.0
        overlap = len(left & right)
        return overlap / max(4, min(len(left), len(right)))

    @staticmethod
    def _clean_text(text: str) -> str:
        return re.sub(r'\s+', ' ', text or '').strip()
