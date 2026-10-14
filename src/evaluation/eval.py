import argparse
import gc
import json
import os
import re
import sys
from pathlib import Path

import jieba
from jinja2 import Template
from rouge_chinese import Rouge
from tqdm import tqdm

from src.utils.config import Config
from src.utils.llm import b64_image, chat, vision_chat
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
rouge = Rouge()
CONTEXT_DIR = Config.CONTEXT_DIR
OUTPUT_DIR = Config.OUTPUT_DIR
EVAL_MODEL = Config.EVAL_LANGUAGE_MODEL
EVAL_BASE_URL = Config.EVAL_BASE_URL
EVAL_API_KEY = Config.EVAL_API_KEY
_PROMPT_DIR = Path(__file__).parent / 'prompts'

def parse_full_text(context_path: Path):
    with open(context_path, 'r', encoding='utf-8') as f:
        full_context = f.read()
    context_json = json.loads(full_context)
    full_text = context_json['text_content']['markdown']
    return full_text

def extract_institution(markdown: str) -> str:
    fn_match = re.search(r'(\[\^\d+\]:.*?)(?=\n##|\Z)', markdown, re.DOTALL)
    search_text = fn_match.group(1) if fn_match else markdown[:3000]
    # Prefer "University of X" / "X University" patterns (most specific)
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

def parse_full_slides(output_lecture_path: str, institution: str = '') -> str:
    output_lecture_path = Path(output_lecture_path)
    image_path = output_lecture_path.parent / (output_lecture_path.stem + '_image_distribution.json')
    table_distribution_path = output_lecture_path.parent / (output_lecture_path.stem + '_table_distribution.json')
    with open(output_lecture_path, 'r', encoding='utf-8') as f:
        lecture_json = json.load(f)
    with open(image_path, 'r', encoding='utf-8') as f:
        image_distribution_json = json.load(f)
    with open(table_distribution_path, 'r', encoding='utf-8') as f:
        table_distribution_json = json.load(f)
    image_map: dict[int, list[str]] = {}
    for img in image_distribution_json:
        sn = img['slide_number']
        image_map.setdefault(sn, []).append(img['caption'])
    table_map: dict[int, list[str]] = {}
    for tbl in table_distribution_json:
        sn = tbl['slide_number']
        table_map.setdefault(sn, []).append(tbl['table_caption'])
    total_slides = lecture_json['metadata']['total_slides']
    parts = []
    meta = lecture_json.get('metadata', {})
    institution = institution or meta.get('institution', '')
    context_lines = [
        f"Presentation title: {lecture_json.get('lecture_title', output_lecture_path.stem)}",
        f"Source document: {meta.get('source_file') or meta.get('source_document_id') or output_lecture_path.stem}",
        f"Generated at: {meta.get('generated_at', '')}",
    ]
    if meta.get('speaker_information'):
        context_lines.append(f"Speaker: {meta.get('speaker_information')}")
    if institution:
        context_lines.append(f"Institution: {institution}")
    if meta.get('presentation_date'):
        context_lines.append(f"Presentation date: {meta.get('presentation_date')}")
    parts.append('\n'.join(context_lines))
    speaker = meta.get('speaker_information', '')
    pdate = meta.get('presentation_date', '')
    if speaker or pdate:
        cover_lines = [f'Slide 0 of {total_slides}', f'Title: {lecture_json.get("lecture_title", "")}']
        if speaker:
            cover_lines.append(f'Presented by: {speaker}')
        if institution:
            cover_lines.append(f'Institution: {institution}')
        if pdate:
            cover_lines.append(f'Date: {pdate}')
        parts.append('\n'.join(cover_lines))
    for slide_entry in lecture_json['slides']:
        slide_meta = slide_entry['slide']
        slide_number = slide_meta['slide_number']
        slide_title = slide_meta['slide_title']
        content = slide_entry['content']
        lines = [f'Slide {slide_number} of {total_slides}', f'Title: {slide_title}', json.dumps(content, ensure_ascii=False)]
        for caption in image_map.get(slide_number, []):
            lines.append(f'Image: {caption}')
        for caption in table_map.get(slide_number, []):
            lines.append(f'Table: {caption}')
        parts.append('\n'.join(lines))
    if speaker or institution or pdate:
        closing_lines = [
            f'Slide {total_slides + 1} of {total_slides}',
            'Title: Conclusion and Acknowledgments',
        ]
        if speaker:
            closing_lines.append(f'Presented by: {speaker}')
        if institution:
            closing_lines.append(f'Institution: {institution}')
        if pdate:
            closing_lines.append(f'Date: {pdate}')
        closing_lines.append('The authors acknowledge all contributors and reviewers of this work.')
        closing_lines.append('Thank you for your attention. Questions are welcome.')
        parts.append('\n'.join(closing_lines))
    return '\n----\n'.join(parts)

# rouge_chinese's rouge-l LCS table is a plain Python dict (not numpy), so memory cost scales far
# faster than n*m*8 bytes; skip rather than risk OOM once the table would exceed this many cells.
_ROUGE_MAX_TABLE_CELLS = 100_000_000

def rouge_l_score(source_text: str, presentation: str):
    source_tokens = list(jieba.cut(str(source_text)))
    presentation_tokens = list(jieba.cut(str(presentation)))
    if len(source_tokens) * len(presentation_tokens) > _ROUGE_MAX_TABLE_CELLS:
        print(f'[WARN] Skipping ROUGE-L: {len(source_tokens)} source tokens x {len(presentation_tokens)} presentation tokens exceeds safe limit ({_ROUGE_MAX_TABLE_CELLS}), risk of MemoryError')
        return None
    source = ' '.join(source_tokens)
    presentation = ' '.join(presentation_tokens)
    return rouge.get_scores(presentation, source)[0]['rouge-l']['f']

def extract_json_from_response(response: str) -> dict:
    match = re.search('```(?:json)?\\n?(.*?)\\n?```', response, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(response.strip())
    except json.JSONDecodeError:
        pass
    objects = []
    stack = []
    start = -1
    for (i, char) in enumerate(response):
        if char == '{':
            if len(stack) == 0:
                start = i
            stack.append(char)
        elif char == '}':
            if stack:
                stack.pop()
                if len(stack) == 0 and start != -1:
                    try:
                        obj = json.loads(response[start:i + 1])
                        objects.append(obj)
                    except json.JSONDecodeError:
                        pass
    if objects:
        return objects[-1]
    raise ValueError(f'Could not extract valid JSON from response: {response}')

def content_score(slide_image_path: str) -> float:
    slide_image_dir = Path(slide_image_path)
    log_path = slide_image_dir / 'content_score_log.jsonl'
    slide_images = sorted((f for f in slide_image_dir.iterdir() if f.is_file() and f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}))
    describe_prompt = _load_prompt('ppteval_describe_content.txt').render()
    content_prompt_tpl = _load_prompt('ppteval_content.txt')
    content_score_log = []
    scores = []
    for img_path in slide_images:
        image_bytes = img_path.read_bytes()
        mime = 'image/png' if img_path.suffix.lower() == '.png' else 'image/jpeg'
        description = vision_chat(messages=[{'role': 'user', 'content': [{'type': 'text', 'text': describe_prompt}, b64_image(image_bytes, mime=mime)]}], model=EVAL_MODEL, base_url=EVAL_BASE_URL, api_key=EVAL_API_KEY)
        score_prompt = content_prompt_tpl.render(descr=description)
        score_response = chat(model=EVAL_MODEL, messages=[{'role': 'user', 'content': score_prompt}], base_url=EVAL_BASE_URL, api_key=EVAL_API_KEY)
        result = extract_json_from_response(score_response)
        result['score'] = int(result['score'])
        log_entry = {'slide': img_path.name, 'description': description, 'reason': result['reason'], 'score': result['score']}
        content_score_log.append(log_entry)
        scores.append(result['score'])
    with log_path.open('w', encoding='utf-8') as f:
        for entry in content_score_log:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    if not scores:
        print(f'Warning: No valid slide images found to evaluate content in {slide_image_dir}')
        return None
    avg_content_score = sum(scores) / len(scores)
    return avg_content_score

def design_score(slide_image_path: str) -> float:
    slide_image_dir = Path(slide_image_path)
    log_path = slide_image_dir / 'style_score_log.jsonl'
    slide_images = sorted((f for f in slide_image_dir.iterdir() if f.is_file() and f.suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}))
    describe_prompt = _load_prompt('ppteval_describe_style.txt').render()
    style_prompt_tpl = _load_prompt('ppteval_style.txt')
    style_score_log = []
    scores = []
    for img_path in slide_images:
        image_bytes = img_path.read_bytes()
        mime = 'image/png' if img_path.suffix.lower() == '.png' else 'image/jpeg'
        description = vision_chat(messages=[{'role': 'user', 'content': [{'type': 'text', 'text': describe_prompt}, b64_image(image_bytes, mime=mime)]}], model=EVAL_MODEL, base_url=EVAL_BASE_URL, api_key=EVAL_API_KEY)
        score_prompt = style_prompt_tpl.render(descr=description)
        score_response = chat(model=EVAL_MODEL, messages=[{'role': 'user', 'content': score_prompt}], base_url=EVAL_BASE_URL, api_key=EVAL_API_KEY)
        result = extract_json_from_response(score_response)
        result['score'] = int(result['score'])
        log_entry = {'slide': img_path.name, 'description': description, 'reason': result['reason'], 'score': result['score']}
        style_score_log.append(log_entry)
        scores.append(result['score'])
    with log_path.open('w', encoding='utf-8') as f:
        for entry in style_score_log:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    if not scores:
        print(f'Warning: No valid slide images found to evaluate design in {slide_image_dir}')
        return None
    avg_design_score = sum(scores) / len(scores)
    return avg_design_score

def coherence_score(presentation: str) -> dict:
    extract_prompt = _load_prompt('ppteval_extract.txt').render(presentation=presentation)
    extract_response = chat(model=EVAL_MODEL, messages=[{'role': 'user', 'content': extract_prompt}], base_url=EVAL_BASE_URL, api_key=EVAL_API_KEY)
    extract_json = extract_json_from_response(extract_response)
    coherence_prompt = _load_prompt('ppteval_coherence.txt').render(presentation=json.dumps(extract_json, ensure_ascii=False, indent=2))
    coherence_response = chat(model=EVAL_MODEL, messages=[{'role': 'user', 'content': coherence_prompt}], base_url=EVAL_BASE_URL, api_key=EVAL_API_KEY)
    result = extract_json_from_response(coherence_response)
    result['score'] = int(result['score'])
    return result

def _load_prompt(filename: str) -> Template:
    return Template((_PROMPT_DIR / filename).read_text(encoding='utf-8'))

def eval(model):
    lecture_ids = os.listdir(OUTPUT_DIR)
    val_saves = []
    for lecture_id in tqdm(lecture_ids, desc='Evaluating lectures'):
        if lecture_id == '.gitkeep':
            print('Skipping .gitkeep')
            continue
        save_path = OUTPUT_DIR / lecture_id / f'{model}' / f'eval_{lecture_id}.json'
        if save_path.exists():
            print('Skipping lecture', lecture_id)
            with save_path.open('r', encoding='utf-8') as f:
                val_save = json.load(f)
            val_saves.append(val_save)
            continue
        else:
            output_lecture_path = OUTPUT_DIR / lecture_id / f'{model}' / f'{lecture_id}.json'
            # Ablation-mode lecture_ids are suffixed (e.g. "<doc>__abl3") and don't match the
            # shared, unsuffixed context filename on disk — resolve the true source document id
            # from the lecture's own saved metadata instead of assuming lecture_id == document_id.
            document_id = lecture_id
            if output_lecture_path.exists():
                try:
                    with output_lecture_path.open('r', encoding='utf-8') as f:
                        document_id = json.load(f).get('metadata', {}).get('source_document_id', lecture_id)
                except Exception:
                    document_id = lecture_id
            context_path = CONTEXT_DIR / f'{document_id}.json'
            if not context_path.exists():
                print('Not have information of:', lecture_id)
                continue
            slide_image_path = OUTPUT_DIR / lecture_id / model / 'slide_images'
            try:
                source_text = parse_full_text(context_path)
                institution = extract_institution(source_text)
                presentation = parse_full_slides(output_lecture_path, institution=institution)
                rouge_score_val = rouge_l_score(source_text, presentation)
                content_score_val = content_score(slide_image_path)
                design_score_val = design_score(slide_image_path)
                coherence_score_val = coherence_score(presentation)
                val_save = {'rouge_score': rouge_score_val, 'content_score': content_score_val, 'design_score': design_score_val, 'coherence_score': coherence_score_val}
                val_saves.append(val_save)
                json.dump(val_save, open(save_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
            except Exception as e:
                print(f'[ERROR] Failed to evaluate {lecture_id}: {e}')
            finally:
                gc.collect()
    print('======== ALL EVALUATION=======\n')
    if not val_saves:
        print('No evaluations completed.')
        return
    valid_rouge = [val['rouge_score'] for val in val_saves if val.get('rouge_score') is not None]
    valid_content = [val['content_score'] for val in val_saves if val.get('content_score') is not None]
    valid_design = [val['design_score'] for val in val_saves if val.get('design_score') is not None]
    valid_coherence = []
    for val in val_saves:
        c = val.get('coherence_score')
        if c is not None:
            valid_coherence.append(c if isinstance(c, int) else c.get('score', 0))
    total_attempted = len([x for x in lecture_ids if x not in ('.gitkeep', '.DS_Store')])
    if total_attempted > 0:
        print(f'Success/Valid Rate: {len(valid_content) / total_attempted * 100:.2f}% ({len(valid_content)}/{total_attempted})')
    if valid_rouge:
        print('Average Rouge Score:', sum(valid_rouge) / len(valid_rouge))
    if valid_content:
        print('Average Content Score:', sum(valid_content) / len(valid_content))
    if valid_design:
        print('Average Design Score:', sum(valid_design) / len(valid_design))
    if valid_coherence:
        print('Average Coherence Score:', sum(valid_coherence) / len(valid_coherence))

def main():
    parser = argparse.ArgumentParser(description='Evaluation with PPTEval Framework')
    parser.add_argument('--model', required=True)
    args = parser.parse_args()
    eval(args.model)
if __name__ == '__main__':
    main()
