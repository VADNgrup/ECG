from typing import List, Dict, Any, Set
import json
from pathlib import Path
from src.utils.fuzzy_distance import fuzzy_distance
from src.utils.config import Config

class TableChartDistribution:

    def __init__(self, model: str='gpt-5'):
        pass

    def distribute_tables(self, lecture_id: str, lecture_dict: Dict[str, Any], aggregated_media: Dict[str, Any], used_tables: Set[str]) -> List[Dict[str, Any]]:
        distributions = []
        context_tables = aggregated_media.get('tables', [])
        slides = lecture_dict.get('slides', [])
        if not context_tables or not slides:
            print('No tables or slides found')
            return distributions
        print(f'\nDistributing tables via fuzzy matching...')
        print(f'  Context tables: {len(context_tables)}, Slides: {len(slides)}')
        for slide_entry in slides:
            slide = slide_entry.get('slide', {})
            slide_type = slide.get('slide_type', '')
            slide_number = slide.get('slide_number', 0)
            if slide_type != 'have_table':
                continue
            table_info = slide.get('table')
            if not table_info:
                continue
            slide_table_md = table_info.get('table_markdown', '')
            slide_table_caption = table_info.get('table_caption', '')
            if not slide_table_md:
                continue
            print(f"\n  Slide {slide_number} ({slide.get('slide_title', '')}):")
            best_score = -1
            best_ctx_table = None
            best_ctx_id = None
            for (ctx_idx, ctx_table) in enumerate(context_tables):
                ctx_id = ctx_table.get('table_id', f'table_{ctx_idx}')
                if ctx_id in used_tables:
                    continue
                ctx_md = ctx_table.get('markdown', '')
                if not ctx_md:
                    continue
                score = fuzzy_distance(slide_table_md, ctx_md)
                print(f'    vs {ctx_id}: {score:.1f}')
                if score > best_score:
                    best_score = score
                    best_ctx_table = ctx_table
                    best_ctx_id = ctx_id
            if best_ctx_table and best_score > 0:
                image_path = best_ctx_table.get('image_table_path')
                distributions.append({
                    'slide_number': slide_number, 
                    'table_data': best_ctx_table.get('markdown', ''), 
                    'table_caption': best_ctx_table.get('table_caption', slide_table_caption), 
                    'image_table_path': image_path, 
                    'relevance_score': best_score
                })
                used_tables.add(best_ctx_id)
                print(f'Matched {best_ctx_id} (score: {best_score:.1f}), image: {image_path}')
            else:
                print(f'No matching context table found')
        output_path = Config.LECTURES_DIR / lecture_id / f'{lecture_id}_table_distribution.json'
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(distributions, f, indent=2, ensure_ascii=False)
        print(f'\nSaved table distributions to: {output_path}')
        return distributions
