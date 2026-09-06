from typing import List, Dict, Optional, Any
import json
import re
from pathlib import Path

class VisualAggregation:

    def aggregate_media_from_lecture(self, lecture_dict: Dict[str, Any]) -> Dict[str, Any]:
        metadata = lecture_dict.get('metadata', {})
        source_document_id = metadata.get('source_document_id')
        if not source_document_id:
            raise ValueError('No source_document_id found in lecture metadata')
        context_data = self._load_context_file(source_document_id)
        tables = context_data.get('tables', [])
        assets = context_data.get('assets', {})
        images = assets.get('images', [])
        images = self._enrich_images_from_markdown(images, context_data.get('text_content', {}).get('markdown', ''))
        page_count = context_data.get('text_content', {}).get('page_count') or context_data.get('metadata', {}).get('page_count')
        result = {'tables': tables, 'images': images, 'source_document_id': source_document_id, 'total_tables': len(tables), 'total_images': len(images), 'page_count': page_count}
        return result

    def _enrich_images_from_markdown(self, images: List[Dict[str, Any]], markdown: str) -> List[Dict[str, Any]]:
        image_context = self._extract_image_context(markdown)
        enriched = []
        for image in images:
            item = dict(image)
            file_path = item.get('file_path', '')
            info = image_context.get(file_path)
            if info:
                if not item.get('caption'):
                    item['caption'] = info.get('caption', '')
                if not item.get('reference_context'):
                    item['reference_context'] = info.get('reference_context', '')
            enriched.append(item)
        return enriched

    @staticmethod
    def _extract_image_context(markdown: str) -> Dict[str, Dict[str, str]]:
        result: Dict[str, Dict[str, str]] = {}
        for match in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', markdown):
            caption = re.sub(r'\s+', ' ', match.group(1)).strip()
            path = match.group(2).strip()
            start = max(0, match.start() - 500)
            end = min(len(markdown), match.end() + 500)
            reference_context = re.sub(r'\s+', ' ', markdown[start:end]).strip()
            result[path] = {'caption': caption, 'reference_context': reference_context}
        return result

    def _load_context_file(self, source_document_id: str) -> Dict[str, Any]:
        context_path = Path('data/context') / f'{source_document_id}.json'
        if not context_path.exists():
            raise FileNotFoundError(f'Context file not found: {context_path}')
        with open(context_path, 'r', encoding='utf-8') as f:
            context_data = json.load(f)
        return context_data

    def aggregate_from_lecture_file(self, lecture_json_path: str) -> Dict[str, Any]:
        with open(lecture_json_path, 'r', encoding='utf-8') as f:
            lecture_dict = json.load(f)
        return self.aggregate_media_from_lecture(lecture_dict)

    def save_aggregated_media(self, lecture_dict: Dict[str, Any], output_path: Optional[str]=None) -> str:
        aggregated = self.aggregate_media_from_lecture(lecture_dict)
        if output_path is None:
            output_dir = Path('data/media')
            output_dir.mkdir(parents=True, exist_ok=True)
            source_id = aggregated['source_document_id']
            output_path = output_dir / f'{source_id}_media.json'
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(aggregated, f, indent=2, ensure_ascii=False)
        return str(output_path)
