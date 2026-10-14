"""Phase 4: native editable PowerPoint export. No browser or HTML intermediate."""
import argparse
import hashlib
import json
from pathlib import Path

from src.generator.pptx_pipeline import export_native
from src.utils.config import Config


def output_name(lecture_id: str, limit: int = 60) -> str:
    """Folder/file stem for the deck: long ids are cut and get a hash suffix (PowerPoint rejects paths over 255 chars)."""
    if len(lecture_id) <= limit:
        return lecture_id
    return f"{lecture_id[:limit - 9].rstrip('-_')}-{hashlib.md5(lecture_id.encode('utf-8')).hexdigest()[:8]}"


def run_pipeline(lecture_json_path, lecture_title=None, speaker_information='', institution=''):
    source = Path(lecture_json_path)
    data = json.loads(source.read_text(encoding='utf-8'))
    lecture_id = data.get('lecture_id', source.stem)
    model = (Config.LLM_MODEL_NAME or 'unknown_model').replace('/', '_')
    name = output_name(lecture_id)
    output = Path(Config.OUTPUT_DIR) / name / model / f'{name}.pptx'
    count = export_native(source, output, title=lecture_title, speaker=speaker_information, institution=institution)
    print(f'[slide_gen] PPTX complete: {count} slides -> {output}')
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Generate native PPTX slides')
    parser.add_argument('--lecture', required=True)
    parser.add_argument('--title')
    parser.add_argument('--speaker', default='')
    args = parser.parse_args()
    run_pipeline(args.lecture, args.title, args.speaker)
