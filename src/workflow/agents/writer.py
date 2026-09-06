from src.utils.llm import chat
from typing import Dict, Optional, List
import json
from src.utils.parse_llm_response import parse_json_response
from dataclasses import asdict
from src.models.context import DocumentContext
from src.models.slide import SlideContent, Slide

class WriterAgent:

    def __init__(self, model: str):
        self.model = model

    def _chat(self, messages: list, temperature: float=0.4, max_tokens: int=None) -> str:
        return chat(self.model, messages, temperature=temperature, max_tokens=max_tokens)

    def _extract_relevant_text(self, context: DocumentContext) -> str:
        full_text = context.text_content.markdown
        return full_text if len(full_text) <= 8000 else full_text[: 8000]

    def _build_batch_system_prompt(self) -> str:
        return '# ROLE\nYou are an expert lecture slide writer specializing in engaging,\npedagogically clear slides derived from structured academic material.\n\n# TASK\nYou will receive a list of slide specifications and the full source material.\nGenerate content for ALL slides at once in a single response.\n\n# CORE PRINCIPLE\nAll generated content must satisfy three quality criteria:\n- Faithfulness: Each slide must accurately reflect the meaning, tone, and technical\n  content of the source material - do not paraphrase into generic textbook language.\n- Coverage: Together, all slides should cover the key ideas, arguments, and details\n  present in the source material. No important concept should be omitted.\n- Coherence: The slides must be internally consistent and logically connected from\n  beginning to end - terminology, notation, and narrative flow must remain uniform\n  across the entire deck.\n\n# SLIDE HIERARCHY\nThe slide titles follow a numbered outline structure. Use the title prefix to\ndetermine the role of each slide and write content accordingly:\n- Major slide, that has title matches `N.` (e.g. "1. Introduction", "3. Results"):\n  Write a high-level overview. Introduce the theme, state the key questions\n  or objectives, and briefly preview what the sub-slides will elaborate on.\n  Do NOT go into granular detail - save that for the sub-slides.\n- Sub-slide, that has title matches `N.M` or `N.M.K` (e.g. "1.1 Background",\n  "2.3 Experimental Setup"):\n  Write specific, detailed content that expands on exactly one aspect of\n  its parent major slide. Assume the audience already saw the parent overview.\n\n# SLIDE CONSTRUCTION RULES\n- Follow the language, tone, and style of the source material.\n- Stay strictly within the scope of each slide\'s description / goal.\n- Write 3 to 6 main bullet points per slide, occasionally using sub-bullets for logical grouping.\n- Emphasize logical transitions (cause-and-effect) and coherence. You are allowed to write sentences up to 25 words if it helps maintain a complex concept\'s integrity without fragmenting it.\n- Preserve vivid phrasing, examples, or questions when possible.\n- Use proper LaTeX for any mathematical expression.\n\n# MATHEMATICS & NOTATION\n- Inline mathematical expressions are wrapped in LaTeX delimiters by using $...$\n- Correct LaTeX commands are used consistently:\n  - Trigonometric functions: $\\sin$, $\\cos$, $\\tan$\n  - Greek letters: $\\alpha$, $\\beta$, $\\pi$\n  - Fractions: $\\frac{a}{b}$\n  - Superscripts: $x^2$, $\\sin^2 x$\n  - Subscripts: $x_1$, $a_n$\n  - Symbols: $\\neq$, $\\leq$, $\\geq$, $\\pm$, $\\infty$\n- Plain-text mathematical notation is never used.\n\n# CONTENT FORMAT FOR SLIDE TYPE\n- For "content" / "have_table" / "have_formula":\n    "content": ["Point 1", "Point 2", "Point 3", ...]\n- For "two_sub_contents":\n    "content": {"Sub Title 1": ["Point 1", "Point 2", ...], "Sub Title 2": ["Point 1", "Point 2", ...]}\n- For "comparison":\n    "content": "A markdown table summarising the two comparable entities."\n\n# OUTPUT FORMAT\nReturn ONLY valid JSON — an array with one object per slide, in the same\norder as the input specifications:\n[\n  {\n    "slide_number": 1,\n    "content": ["Point 1 supporting the slide title", "Point 2 supporting the slide title", "Point 3 supporting the slide title", "Point 4 (optional)", "Point 5 (optional)"]\n  },\n  {\n    "slide_number": 2,\n    "content": ["Point 1 supporting the slide title", "Point 2 supporting the slide title", "Point 3 supporting the slide title", "Point 4 (optional)", "Point 5 (optional)"]\n  }\n]\n'

    def draft_slides(self, slide_specs: List[Slide], context: DocumentContext) -> List[SlideContent]:
        text_excerpt = self._extract_relevant_text(context)
        specs_payload = []
        for (i, spec) in enumerate(slide_specs, 1):
            d = asdict(spec)
            if hasattr(d.get('slide_type'), 'value'):
                d['slide_type'] = d['slide_type'].value
            else:
                d['slide_type'] = str(d.get('slide_type', ''))
            d['slide_number'] = i
            specs_payload.append(d)
        user_prompt = f'SOURCE MATERIAL:\n{text_excerpt}\n\nSLIDE SPECIFICATIONS:\n{json.dumps(specs_payload, ensure_ascii=False, indent=2)}'
        raw = self._chat([{'role': 'system', 'content': self._build_batch_system_prompt()}, {'role': 'user', 'content': user_prompt}], temperature=0.4, max_tokens=16000)
        return self._parse_batch_response(raw, slide_specs)

    def draft_slides_with_feedback(self, slide_specs_with_feedback: List[tuple], context: DocumentContext) -> List[SlideContent]:
        text_excerpt = self._extract_relevant_text(context)
        specs_payload = []
        slide_specs_ordered: List[Slide] = []
        for (seq_num, (orig_idx, spec, feedback)) in enumerate(slide_specs_with_feedback, 1):
            d = asdict(spec)
            if hasattr(d.get('slide_type'), 'value'):
                d['slide_type'] = d['slide_type'].value
            else:
                d['slide_type'] = str(d.get('slide_type', ''))
            d['slide_number'] = seq_num
            if feedback:
                d['rewrite_feedback'] = feedback
            specs_payload.append(d)
            slide_specs_ordered.append(spec)
        user_prompt = f'SOURCE MATERIAL:\n{text_excerpt}\n\nSLIDE SPECIFICATIONS (with optional rewrite_feedback per slide):\n{json.dumps(specs_payload, ensure_ascii=False, indent=2)}'
        raw = self._chat([{'role': 'system', 'content': self._build_batch_system_prompt()}, {'role': 'user', 'content': user_prompt}], temperature=0.4, max_tokens=16000)
        return self._parse_batch_response(raw, slide_specs_ordered)

    def _parse_batch_response(self, raw: str, slide_specs: List[Slide]) -> List[SlideContent]:
        invoke_fn = lambda msgs: type('R', (), {'content': self._chat(msgs)})()
        data = parse_json_response(raw, invoke_fn, expect_list=True)
        by_number: Dict[int, object] = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    num = item.get('slide_number')
                    if num is not None:
                        by_number[int(num)] = item.get('content', [])
        results: List[SlideContent] = []
        for (i, spec) in enumerate(slide_specs, 1):
            content = by_number.get(i, [])
            results.append(SlideContent(slide=spec, content=content))
        return results