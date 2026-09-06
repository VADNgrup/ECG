from src.utils.llm import chat
from typing import Dict, Any, List
import re
from src.utils.parse_llm_response import parse_json_response
from src.models.context import DocumentContext
from src.models.slide import Slide, Table, SlideType

class PlanSpecerAgent:
    MAX_EVIDENCE_LENGTH = 9000
    MAX_RETRIES = 3

    def __init__(self, model: str):
        self.model = model

    def _chat(self, messages: list) -> str:
        return chat(self.model, messages, temperature=0.3, max_tokens=4096)

    @staticmethod
    def outline_md_to_number(outline_md: str) -> str:
        lines = outline_md.splitlines()
        counters: List[int] = []
        result_lines: List[str] = []
        for line in lines:
            match = re.match('^(#+)\\s+(.*)', line.strip())
            if not match:
                if line.strip():
                    result_lines.append(line)
                continue
            hashes = match.group(1)
            title = match.group(2)
            depth = len(hashes)
            if depth > len(counters):
                while len(counters) < depth:
                    counters.append(0)
            else:
                counters = counters[:depth]
            counters[-1] += 1
            number_str = '.'.join((str(c) for c in counters))
            if depth == 1:
                result_lines.append(f'{number_str}. {title}')
            else:
                result_lines.append(f'{number_str} {title}')
        return '\n'.join(result_lines)

    @staticmethod
    def _extract_expected_titles(numbered_outline: str) -> List[str]:
        titles = []
        for line in numbered_outline.splitlines():
            stripped = line.strip()
            if re.match('^\\d[\\d.]*[\\. ]', stripped):
                titles.append(stripped)
        return titles

    @staticmethod
    def _normalize_title(title: str) -> str:
        return re.sub('\\s+', ' ', title.strip().lower())

    @classmethod
    def _find_missing_titles(cls, raw_specs: List[Dict], expected_titles: List[str]) -> List[str]:

        def prefix_of(title: str) -> str:
            m = re.match('^([\\d.]+)', title.strip())
            return m.group(1).rstrip('.') if m else ''
        returned_prefixes = {prefix_of(d.get('slide_title', '')) for d in raw_specs if isinstance(d, dict)}
        missing = []
        for expected in expected_titles:
            if prefix_of(expected) not in returned_prefixes:
                missing.append(expected)
        return missing

    def _build_prompt(self, numbered_outline: str, evidence_text: str, context: DocumentContext) -> str:
        schema_example = '{\n  "slide_title": "<A concise, polished slide title. Paraphrase the outline heading if it is a raw sentence or fragment. MUST preserve its number prefix>",\n  "slide_type": "<one of: content, have_table, have_formula, comparison, two_sub_contents>",\n  "goal": "<1-2 sentence goal describing what this slide should convey>",\n  "table": {"table_markdown": "<markdown table string>", "table_caption": "<caption>"} or null,\n  "latex_block_formula": "<LaTeX block formula string>" or null\n}'
        return f'\n# ROLE\nYou are a lecture slide specification architect.\n\n# TASK\nGiven the FULL numbered lecture outline and relevant retrieved source evidence, produce a JSON array\nof slide specifications — one object per heading line (both major `1.` and sub `1.1`, `1.1.1`, etc.).\nEvery heading in the outline MUST have exactly ONE corresponding JSON object.\n\n# INPUT\n## Full Numbered Outline\n{numbered_outline}\n\n## Retrieved Source Evidence\n{evidence_text}\n\n## Tables extracted from document\n{context.tables}\n\n# IMPORTANT CONSTRAINTS\n1. `slide_type` must be one of: "content", "have_table", "have_formula", "comparison", "two_sub_contents".\n2. Use "have_table" ONLY if the source evidence contains a table supporting this slide.\n   If so, include `table` with the markdown and caption. Otherwise set `table` to null.\n   2.1. Prioritize extracting the tables mentioned in the evidence.\n   2.2. Only tables with more than 2 rows and 2 columns. Smaller tables → use "content".\n3. Use "have_formula" ONLY if the source evidence contains a block-level formula for this slide.\n   If so, include `latex_block_formula`. Otherwise set it to null.\n4. Use "comparison": `goal` must describe the two comparable entities briefly.\n5. Use "two_sub_contents": `goal` must describe the two distinct sub-topics shown side by side.\n6. Default to "content" when none of the above special types apply.\n\n# CRITICAL\n- `slide_title` MUST preserve the numbering prefix EXACTLY as it appears in the outline\n  (e.g. "1. Introduction", "1.1 Background", "2.1.3 Survey Design"). However, you MUST paraphrase and polish the rest of the title to make it sound professional and concise if the original heading from the outline is too raw, too long, or a sentence fragment.\n- `goal` MUST stay in the SAME language as `slide_title`.\n- The output array MUST contain one entry for EVERY line in the outline above — no omissions.\n\n# EXAMPLE\n[\n  {{\n    "slide_title": "1. Introduction",\n    "slide_type": "content",\n    "goal": "Introduce the topic and motivate the study.",\n    "table": null,\n    "latex_block_formula": null\n  }},\n  {{\n    "slide_title": "1.1 Background",\n    "slide_type": "content",\n    "goal": "Describe the historical and theoretical background.",\n    "table": null,\n    "latex_block_formula": null\n  }},\n  {{\n    "slide_title": "2. Methods",\n    "slide_type": "have_table",\n    "goal": "Summarise the methodology used.",\n    "table": {{\n      "table_markdown": "| Col1 | Col2 |\\n|------|------|\\n| Val1 | Val2 |",\n      "table_caption": "Overview of methods"\n    }},\n    "latex_block_formula": null\n  }}\n]\n\n# OUTPUT FORMAT\nReturn ONLY a valid JSON array. Each element must follow this schema:\n[{schema_example}]\n'

    def _retrieve_outline_evidence(self, context: DocumentContext, expected_titles: List[str]) -> str:
        return context.text_content.markdown[:self.MAX_EVIDENCE_LENGTH]

    @classmethod
    def _ranked_page_blocks(cls, context: DocumentContext, expected_titles: List[str]) -> List[str]:
        pages = cls._split_pages(context.text_content.markdown)
        if not pages:
            return []
        query_terms = cls._outline_terms(expected_titles)
        scored = []
        for page_num, page_text in pages:
            clean = cls._clean_page_text(page_text)
            lower = clean.lower()
            score = sum(lower.count(term) for term in query_terms)
            if re.search(r"\$\$.*?\$\$", page_text, flags=re.DOTALL):
                score += 8
            if re.search(r"(?:<=|>=|≤|≥|\\leq?|\\geq?)", page_text):
                score += 6
            if re.search(r"^\s*\|.+\|\s*$", page_text, flags=re.MULTILINE):
                score += 5
            if re.search(r"!\[[^\]]*\]\([^)]+\)", page_text):
                score += 3
            if score > 0:
                scored.append((score, page_num, clean[:1200]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [f"--- Page {page_num} structured evidence ---\n{text}" for _, page_num, text in scored[:6]]

    @staticmethod
    def _outline_terms(expected_titles: List[str]) -> List[str]:
        stop = {
            "introduction", "overview", "method", "solution", "using", "with", "from",
            "linear", "programming", "slide", "section", "content", "the", "and", "for",
        }
        terms = []
        for title in expected_titles:
            for term in re.findall(r"[A-Za-z0-9_]{4,}", title.lower()):
                if term not in stop and not term.isdigit():
                    terms.append(term)
        seen = set()
        result = []
        for term in terms:
            if term not in seen:
                seen.add(term)
                result.append(term)
        return result[:32]

    @staticmethod
    def _split_pages(markdown: str) -> List[tuple[int, str]]:
        matches = list(re.finditer(r"<!--\s*PAGE\s+(\d+)\s*-->", markdown, flags=re.IGNORECASE))
        if not matches:
            return [(1, markdown)]
        pages = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
            pages.append((int(match.group(1)), markdown[start:end].strip()))
        return pages

    @staticmethod
    def _clean_page_text(text: str) -> str:
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        text = re.sub(r"\*Figure:[^*]+\*", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"#\s*Dagon University Research Journal\s+\d{4},\s*Vol\.\s*\d+\s*\d*", " ", text, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", text).strip()

    def _parse_json_response(self, content: str) -> List[Dict]:
        invoke_fn = lambda msgs: type('R', (), {'content': self._chat(msgs)})()
        data = parse_json_response(content, invoke_fn)
        if isinstance(data, dict):
            for key in ("slide_specs", "slides", "specs"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
        return data

    @staticmethod
    def _dict_to_slide(d: Dict[str, Any]) -> Slide:
        slide_type_str = d.get('slide_type', 'content')
        try:
            slide_type = SlideType(slide_type_str)
        except ValueError:
            slide_type = SlideType.CONTENT
        table_data = d.get('table')
        table = None
        if table_data and isinstance(table_data, dict):
            table = Table(table_markdown=table_data.get('table_markdown', ''), table_caption=table_data.get('table_caption', ''))
        title = d.get('slide_title', '')
        clean_title = re.sub(r'^[\d.]+\s*', '', title).strip()
        goal = PlanSpecerAgent._normalise_goal_language(d.get('goal', ''), clean_title)
        return Slide(slide_title=clean_title, slide_type=slide_type, goal=goal, table=table, latex_block_formula=d.get('latex_block_formula'))

    def specify(self, outline_md: str, context: DocumentContext) -> List[Slide]:
        numbered_outline = self.outline_md_to_number(outline_md)
        expected_titles = self._extract_expected_titles(numbered_outline)
        evidence_text = self._retrieve_outline_evidence(context, expected_titles)
        print(f'\n[PlanSpecer] Outline has {len(expected_titles)} heading(s) to spec.')
        last_raw_specs: List[Dict] = []
        for attempt in range(1, self.MAX_RETRIES + 1):
            print(f'[PlanSpecer] Attempt {attempt}/{self.MAX_RETRIES} — calling LLM for full outline...')
            prompt = self._build_prompt(numbered_outline, evidence_text, context)
            try:
                raw_content = self._chat([{'role': 'user', 'content': prompt}])
            except Exception as e:
                print(f'[PlanSpecer] LLM call failed on attempt {attempt}: {e}')
                if attempt < self.MAX_RETRIES:
                    print('[PlanSpecer] Retrying...')
                    continue
                break
            try:
                raw_specs = self._parse_json_response(raw_content)
            except Exception as e:
                print(f'[PlanSpecer] JSON parse error on attempt {attempt}: {e}')
                if attempt < self.MAX_RETRIES:
                    print('[PlanSpecer] Retrying...')
                continue
            if not isinstance(raw_specs, list):
                print(f'[PlanSpecer] ✗ Expected a JSON array, got {type(raw_specs).__name__}. Retrying...')
                continue
            raw_specs = self._align_specs_to_outline(raw_specs, expected_titles)
            last_raw_specs = raw_specs
            missing = self._find_missing_titles(raw_specs, expected_titles)
            if missing:
                print(f"[PlanSpecer] {len(missing)} heading(s) missing from output (e.g. {missing[:3]}{('...' if len(missing) > 3 else '')}).")
                if attempt < self.MAX_RETRIES:
                    print('[PlanSpecer] Retrying...')
                continue
            print(f'[PlanSpecer] All {len(raw_specs)} specs validated successfully.')
            return self._build_slide_list(raw_specs)
        if last_raw_specs:
            filled_specs = self._fill_missing_specs(last_raw_specs, expected_titles)
            print(f'[PlanSpecer] WARNING: reached max retries ({self.MAX_RETRIES}). Using repaired result with {len(filled_specs)} spec(s).')
            return self._build_slide_list(filled_specs)
        fallback_specs = self._fallback_specs(expected_titles)
        print(f'[PlanSpecer] WARNING: reached max retries ({self.MAX_RETRIES}). Using outline fallback with {len(fallback_specs)} spec(s).')
        return self._build_slide_list(fallback_specs)

    def _fill_missing_specs(self, raw_specs: List[Dict], expected_titles: List[str]) -> List[Dict]:
        by_prefix = {}
        for spec in raw_specs:
            if not isinstance(spec, dict):
                continue
            m = re.match('^([\\d.]+)', str(spec.get('slide_title', '')).strip())
            if m:
                by_prefix[m.group(1).rstrip('.')] = spec
        repaired = []
        for title in expected_titles:
            m = re.match('^([\\d.]+)', title.strip())
            prefix = m.group(1).rstrip('.') if m else title
            repaired.append(by_prefix.get(prefix) or self._fallback_spec(title))
        return repaired

    def _align_specs_to_outline(self, raw_specs: List[Dict], expected_titles: List[str]) -> List[Dict]:
        aligned = []
        usable_specs = [spec for spec in raw_specs if isinstance(spec, dict)]
        if len(usable_specs) < len(expected_titles):
            return self._normalise_specs(usable_specs)
        for title, spec in zip(expected_titles, usable_specs):
            spec = dict(spec)
            llm_title = str(spec.get('slide_title', '')).strip()
            if not llm_title:
                spec['slide_title'] = title
            else:
                m = re.match(r'^([\d.]+)', title.strip())
                expected_prefix = m.group(1) if m else ''
                llm_m = re.match(r'^([\d.]+)', llm_title)
                llm_prefix = llm_m.group(1) if llm_m else ''
                
                if expected_prefix and llm_prefix != expected_prefix:
                    clean_llm = re.sub(r'^[\d.]+\s*', '', llm_title).strip()
                    spec['slide_title'] = f"{expected_prefix} {clean_llm}"
            aligned.append(spec)
        return self._normalise_specs(aligned)

    def _fallback_specs(self, expected_titles: List[str]) -> List[Dict]:
        return [self._fallback_spec(title) for title in expected_titles]

    @staticmethod
    def _fallback_spec(title: str) -> Dict[str, Any]:
        clean_title = re.sub(r'^\\d[\\d.]*\\s*', '', title).strip()
        goal = clean_title or title
        return {
            "slide_title": title,
            "slide_type": "content",
            "goal": goal,
            "table": None,
            "latex_block_formula": None,
        }

    @classmethod
    def _normalise_specs(cls, specs: List[Dict]) -> List[Dict]:
        normalised = []
        for spec in specs:
            spec = dict(spec)
            spec['goal'] = cls._normalise_goal_language(spec.get('goal', ''), spec.get('slide_title', ''))
            normalised.append(spec)
        return normalised

    @staticmethod
    def _normalise_goal_language(goal: str, slide_title: str) -> str:
        clean_goal = re.sub(r'\s+', ' ', str(goal or '')).strip()
        clean_title = re.sub(r'^\d+(?:\.\d+)*[.)]?\s*', '', str(slide_title or '')).strip()
        return clean_goal or clean_title

    def _build_slide_list(self, raw_specs: List[Dict]) -> List[Slide]:
        all_specs = [self._dict_to_slide(d) for d in raw_specs]
        for (idx, slide) in enumerate(all_specs, start=1):
            slide.slide_number = idx
        print(f'[PlanSpecer] Total slide specs: {len(all_specs)}')
        return all_specs
