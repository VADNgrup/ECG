import json
import re
from typing import Any, Dict, List

from src.models.slide import SlideContent
from src.utils.config import Config
from src.utils.language_utils import dominant_script
from src.utils.llm import chat
from src.utils.parse_llm_response import parse_json_response
from src.workflow.agents.content_quality import ContentQualityAgent


class DirectBulletWriterAgent:
    BATCH_SIZE = 4
    MAX_OUTPUT_TOKENS = 2560
    PROMPT_TOKEN_BUDGET = 5600

    def __init__(self, model: str):
        self.model = model
        self.content_tools = ContentQualityAgent(model)

    def _chat(self, messages: list, max_tokens: int | None = None) -> str:
        return chat(self.model, messages, temperature=0.25, max_tokens=max_tokens or self.MAX_OUTPUT_TOKENS)

    def write(self, packets: List[Dict[str, Any]], slide_specs: List) -> List[SlideContent]:
        if not packets:
            return []
        spec_by_number = {spec.slide_number: spec for spec in slide_specs}
        results: List[SlideContent] = []
        for batch in self._chunks(packets, self.BATCH_SIZE):
            results.extend(self._write_batch(batch, spec_by_number))
        return results

    def _write_batch(self, packets: List[Dict[str, Any]], spec_by_number: Dict[int, Any]) -> List[SlideContent]:
        prompt = self._build_prompt(packets)
        if len(packets) > 1 and self._estimate_tokens(prompt) > self.PROMPT_TOKEN_BUDGET:
            midpoint = max(1, len(packets) // 2)
            return self._write_batch(packets[:midpoint], spec_by_number) + self._write_batch(packets[midpoint:], spec_by_number)
        max_tokens = self._output_budget(prompt, len(packets))
        raw = self._chat([{"role": "user", "content": prompt}], max_tokens=max_tokens)
        invoke_fn = lambda msgs: type("R", (), {"content": self._chat(msgs, max_tokens=max_tokens)})()
        data = parse_json_response(raw, invoke_fn, expect_list=True)
        by_number: Dict[int, Any] = {}
        layout_by_number: Dict[int, str] = {}
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("slide_number") is not None:
                    num = int(item["slide_number"])
                    by_number[num] = item.get("content", [])
                    if item.get("layout"):
                        layout_by_number[num] = str(item["layout"])
        slides: List[SlideContent] = []
        for packet in packets:
            num = int(packet["slide_number"])
            spec = spec_by_number[num]
            content = self._normalise_content(by_number.get(num, []), packet, spec)
            layout_hint = layout_by_number.get(num)
            slides.append(SlideContent(slide=spec, content=content, layout_hint=layout_hint))
        return slides

    @staticmethod
    def _build_prompt(packets: List[Dict[str, Any]]) -> str:
        prompt_packets = [DirectBulletWriterAgent._prompt_packet(packet) for packet in packets]
        return (
            "# ROLE\n"
            "You write final presentation bullets from source-grounded slide packets.\n\n"
            "# TASK\n"
            "For each packet, produce final slide content directly as 4 to 6 bullets; never fewer than 3 unless the source itself is sparse. Prioritise quality and specificity over quantity — 4 sharp, source-grounded bullets beat 6 vague ones.\n\n"
            "# EVAL-ALIGNED TARGETS\n"
            "- Every slide must have a clear focus that obviously matches the slide title.\n"
            "- Text-only slides must still feel substantial enough for presentation, not like sparse placeholders with excessive white space.\n"
            "- If the packet includes a table or other structured visual evidence, include at least one bullet that interprets the takeaway of that visual instead of only restating raw labels.\n"
            "- Bullets should complement the attached evidence, not ignore it.\n\n"
            "# NON-NEGOTIABLE RULES\n"
            "- Use ONLY facts supported by the packet evidence or required_facts.\n"
            "- Preserve every required_fact that fits the slide title.\n"
            "- Write ALL bullets in the SAME language as the slide title. The slide title language takes priority over the evidence language. Technical terms, model names, acronyms, and numeric metrics may remain in their original form, but all verbs, connectors, and sentence structure must match the slide title language.\n"
            "- Keep each slide tightly focused on its anchor evidence; do not turn every slide into the same overview.\n"
            "- Each bullet must be slide-friendly: ideally one short sentence or clause, and must be no more than about 150 characters.\n"
            "- If a fact is dense, split it into two shorter bullets instead of writing one long bullet.\n"
            "- If coverage_items are provided, cover several distinct items rather than repeating one generic theme.\n"
            "- Cover multiple distinct source facts; do not spend the whole slide paraphrasing only one fact.\n"
            "- If a source fact starts with a numbered label like `1.` or `Step 2:`, rewrite it as a natural presentation bullet instead of copying the label verbatim, unless the slide title explicitly asks for a numbered list.\n"
            "- Avoid repeating the same named entities or facts across neighboring slides unless the packet title requires them.\n"
            "- Do not introduce numbers that are not in required_facts/evidence.\n"
            "- Do not copy page headers, journal headers, figure labels, markdown headings, or source page numbers as facts.\n"
            "- NEVER copy raw sentences from evidence verbatim. Always paraphrase into a complete, self-contained presentation phrase. A bullet that reads like a sentence fragment or ends mid-clause must be rewritten.\n"
            "- Keep bullets concise but not vague.\n"
            "- Avoid generic bullets that could apply to any document; each bullet should contain a source-specific noun, number, method, action, result, or implication.\n"
            "- Preserve formulas and units exactly enough to be mathematically faithful.\n"
            "- NEVER use block math `$$ ... $$` or `\\[ ... \\]` inside bullets. ALWAYS use inline math `$ ... $` for formulas.\n"
            "- When writing inline math, ensure the ENTIRE mathematical expression is enclosed within a single pair of `$ ... $` (e.g. `$X_1 = 270$`, not `$X_1$: 270`). Do NOT leave operators like `\\geq` outside of the `$` tags.\n"
            "- For software workflow slides, keep each action as a clean action bullet.\n\n"
            "# LAYOUT SELECTION\n"
            "For each slide, also choose the best layout from the list below.\n"
            'For layouts marked "title:body", each bullet MUST be formatted as "Short Title: Detailed explanation" '
            "(the first colon separates a 1–6 word label from the description; both sides must be non-empty).\n\n"
            "LAYOUTS:\n"
            "- only_content: Standard bullet list. Use for dense technical content, numbers, or formulas.\n"
            "- two_cols_content_layout: Two-column bullets. Best for 4–8 mixed bullets. Regular bullet format.\n"
            "- key_points_layout: Icon+title+body cards. Best for 4–5 key highlights. Requires title:body format.\n"
            "- conclusion_cards_layout: Heading+body cards. Best for 3–5 findings or conclusions. Requires title:body format.\n"
            "- numbered_conclusions_layout: Numbered headings. Best for 5–6 ordered conclusions. Requires title:body format.\n"
            "- three_cols_content_layout: Three-column cards. Use ONLY when exactly 3 bullets exist. Requires title:body format.\n"
            "- grid_2x2_layout: 2×2 grid cards. Use ONLY when exactly 4 bullets exist. Requires title:body format.\n"
            "- steps_horizontal_layout: Horizontal step flow. Best for 3–5 sequential stages. Requires title:body format.\n"
            "- research_question_layout: Main question + sub-questions. First bullet is the main research question (plain text), next 1–3 bullets are sub-questions. Use for problem statement or methodology slides.\n"
            "- quote_layout: Large pull-quote. Use when a slide has one dominant quotation or statement. First bullet is the quote (plain text), optional second bullet is the attribution/source.\n"
            "- agenda_layout: Numbered agenda list. Best for 3–6 session segments or topics. Requires title:body format (title = segment name, body = brief description).\n"
            "- section_divider_layout: Full-screen section title card. Use ONLY for slides whose entire purpose is to introduce a new section (no bullet content needed — write a single descriptive bullet that summarises the section).\n"
            "- editorial_layout: Magazine-style two-column layout. Use for narrative/opinion slides with a strong headline and explanatory paragraph. Write all bullets as one continuous paragraph separated by semicolons — they will be joined into the body text.\n"
            "- nested_bullets_layout: Hierarchical bullet list. Use when content has clear parent-child relationships. Regular bullet format (sub-items are handled automatically by the renderer).\n"
            "- stats_cards_layout: Big-number stat cards. Use ONLY when the slide content is dominated by 2–4 key statistics. Each bullet must be formatted as \"VALUE: Label description\" where VALUE is the numeric stat (e.g. '42%', '$3.2B', '18 months').\n"
            "- pricing_cards_layout: Feature comparison cards. Use for slides comparing 2–4 tiers, options, or alternatives. Requires title:body format.\n\n"
            "When you choose a title:body layout, adjust bullet count to match the layout requirement and rewrite bullets accordingly.\n\n"
            "# SLIDE PACKETS\n"
            f"{json.dumps(prompt_packets, ensure_ascii=False, indent=2)}\n\n"
            "# OUTPUT\n"
            'Return ONLY a valid JSON array like [{"slide_number": 1, "layout": "key_points_layout", "content": ["Title: detail", "Title: detail"]}].\n'
            'The "layout" field must be one of the layout names listed above.'
        )

    @staticmethod
    def _prompt_packet(packet: Dict[str, Any]) -> Dict[str, Any]:
        keep = {
            "slide_number": packet.get("slide_number"),
            "slide_title": packet.get("slide_title"),
            "goal": packet.get("goal"),
            "intent": packet.get("intent"),
            "coverage_mode": packet.get("coverage_mode"),
            "source_pages": packet.get("source_pages", []),
            "required_facts": packet.get("required_facts", [])[:8],
            "required_checks": packet.get("required_checks", [])[:4],
            "coverage_items": packet.get("coverage_items", [])[:6],
            "anchor_unit_id": packet.get("anchor_unit_id"),
            "support_unit_ids": packet.get("support_unit_ids", [])[:4],
        }
        if packet.get("table"):
            keep["table"] = packet.get("table")
        if packet.get("latex_block_formula"):
            keep["latex_block_formula"] = packet.get("latex_block_formula")
        evidence = str(packet.get("evidence") or "")
        if evidence:
            # OLD (partial clean):
            # cleaned_evidence = re.sub(r"[^\x00-\x7FÀ-ɏḀ-ỿ̀-ͯ]", " ", evidence)
            # cleaned_evidence = re.sub(r" {2,}", " ", cleaned_evidence).strip()
            cleaned_evidence = DirectBulletWriterAgent._clean_evidence_text(evidence)
            if Config.ABLATION_MODE in (1, 2, 3):
                # Ablation 3: no compact-context step anywhere in the pipeline — don't re-cap
                # the evidence here either. Oversized batches still auto-split via
                # PROMPT_TOKEN_BUDGET in _write_batch, so this can't blow up a single call.
                keep["evidence_excerpt"] = cleaned_evidence
            else:
                evidence_limit = 1600 if packet.get("coverage_mode") == "list_coverage" else 1200
                keep["evidence_excerpt"] = ContentQualityAgent._sentence_safe_truncate(cleaned_evidence, evidence_limit)
        return keep

    @staticmethod
    def _clean_evidence_text(text: str) -> str:
        # 1. Strip citation markers [1], [1, 2], [1-3]
        text = re.sub(r"\[\d+(?:[,\-]\s*\d+)*\]", "", text)
        # 2. Strip figure/table/equation labels
        text = re.sub(
            r"\b(?:Figure|Fig\.|Table|Eq\.?|Equation|Algorithm|Listing|Chart)\s*\d+[:\.]?\s*",
            "", text, flags=re.IGNORECASE,
        )
        # 3. Strip bare section numbers at line start (e.g. "3.2.1 ", "4. ")
        text = re.sub(r"(?:^|\n)\s*\d+(?:\.\d+){0,3}\.?\s+", " ", text)
        # 4. Strip non-Latin/Vietnamese characters (Greek, Cyrillic, CJK, Arabic, etc.)
        # Allow: ASCII + Latin Extended (U+00C0-U+024F) + combining marks (U+0300-U+036F) + Vietnamese (U+1EA0-U+1EFF)
        text = re.sub(r"[^\x00-\x7FÀ-ɏ̀-ͯẠ-ỿ]", " ", text)
        # 5. Keep only complete sentences (ending with . ? !)
        sentences = re.split(r"(?<=[.!?])\s+", text)
        complete = [s.strip() for s in sentences if s.strip() and re.search(r"[.!?]$", s.strip())]
        text = " ".join(complete)  # empty → evidence clears, forcing LLM to write from title+goal
        # 6. Normalise whitespace
        return re.sub(r"\s{2,}", " ", text).strip()

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, int(len(text) / 3.6))

    def _output_budget(self, prompt: str, slide_count: int) -> int:
        prompt_tokens = self._estimate_tokens(prompt)
        remaining = max(768, 10000 - prompt_tokens)
        per_slide = max(512, slide_count * 480)
        return min(self.MAX_OUTPUT_TOKENS, remaining, per_slide)

    def _normalise_content(self, content: Any, packet: Dict[str, Any], spec: Any) -> List[str]:
        bullets = []
        if isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    for line in item.splitlines():
                        clean = self.content_tools._clean_bullet(line)
                        if clean and not self.content_tools._is_bad_line(clean):
                            bullets.append(clean)
        elif isinstance(content, str):
            for line in content.splitlines():
                clean = self.content_tools._clean_bullet(line)
                if clean and not self.content_tools._is_bad_line(clean):
                    bullets.append(clean)
        if len(bullets) < 3:
            bullets.extend(self._fallback_bullets(packet, spec))
        if self._too_generic(bullets, packet):
            bullets = self._fallback_bullets(packet, spec) + bullets
        bullets = self._ensure_minimum_coverage(bullets, packet)
        bullets = self._remove_title_echoes(bullets, spec.slide_title)
        bullets = self._compact_slide_bullets(self._filter_language_mismatch(spec.slide_title, self._dedupe(bullets)))
        bullets = [bullet for bullet in bullets if len(bullet.strip()) >= 12]

        capitalized_bullets = []
        for b in bullets:
            b = b.strip()
            if b:
                # Fix raw LaTeX \text{} tags that cause KaTeX errors
                b = re.sub(r'\\+text\{\s*([^}]*)\}', r'\1', b)
                # Fix \USD or similar undefined commands
                b = b.replace(r'\USD', 'USD').replace(r'\usd', 'usd')
                # Replace block math $$ with inline math $ to avoid layout breakage
                b = re.sub(r'\$\$(.*?)\$\$', r'$\1$', b)
                b = b.replace('^2', '²').replace('^3', '³')
                capitalized_bullets.append(b[0].upper() + b[1:])
            else:
                capitalized_bullets.append(b)

        return capitalized_bullets[:8]

    def _fallback_bullets(self, packet: Dict[str, Any], spec: Any) -> List[str]:
        evidence = packet.get("evidence", "")
        deterministic = self.content_tools._deterministic_content(self._empty_slide(spec), evidence, packet)
        if deterministic:
            return deterministic
        facts = [fact for fact in packet.get("required_facts", []) if isinstance(fact, str)]
        if facts:
            return [self.content_tools._clean_bullet(fact) for fact in facts[:8]]
        return self.content_tools._fallback_items(evidence)[:8]

    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        return ContentQualityAgent._dedupe_bullets(items)

    def _too_generic(self, bullets: List[str], packet: Dict[str, Any]) -> bool:
        if not bullets:
            return True
        if packet.get("coverage_mode") in {"list_coverage", "reference_synthesis"}:
            return False
        generic_count = sum(1 for bullet in bullets if self.content_tools._is_generic_bullet(bullet))
        return generic_count >= max(2, len(bullets) // 2 + 1)

    def _ensure_minimum_coverage(self, bullets: List[str], packet: Dict[str, Any]) -> List[str]:
        required = [fact for fact in packet.get("required_facts", []) if isinstance(fact, str)]
        if not required:
            return bullets
        content_l = self.content_tools._normalise_for_match(" ".join(bullets))
        covered = sum(1 for fact in required[:8] if self.content_tools._fact_supported_in_content(content_l, fact))
        target = min(5, len(required[:8]))
        if covered >= target and len(bullets) >= target:
            return bullets
        additions: List[str] = []
        for fact in required[:8]:
            if self.content_tools._fact_supported_in_content(content_l, fact):
                continue
            cleaned = self.content_tools._clean_bullet(fact)
            if cleaned:
                additions.append(cleaned)
            if len(additions) + covered >= target:
                break
        return bullets + additions

    @staticmethod
    def _remove_title_echoes(bullets: List[str], slide_title: str) -> List[str]:
        clean_title = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(slide_title or "")).strip().lower()
        title_terms = {
            term for term in re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", clean_title)
            if term not in {"overview", "summary", "content", "section"}
        }
        result: List[str] = []
        for bullet in bullets:
            clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(bullet or "")).strip()
            lower = clean.lower().strip(".")
            if clean_title and lower == clean_title:
                continue
            bullet_terms = set(re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", lower))
            if title_terms and bullet_terms and bullet_terms <= title_terms and len(bullet_terms) >= 3:
                continue
            result.append(bullet)
        return result or bullets

    @staticmethod
    def _filter_language_mismatch(slide_title: str, bullets: List[str]) -> List[str]:
        title_script = dominant_script(slide_title)
        if title_script in {"unknown", "latin"}:
            return bullets
        if len(bullets) <= 1:
            return bullets
        aligned = [bullet for bullet in bullets if dominant_script(bullet) == title_script]
        return aligned if len(aligned) >= 2 else bullets

    @staticmethod
    def _compact_slide_bullets(bullets: List[str], max_chars: int = 150) -> List[str]:
        compacted: List[str] = []
        for bullet in bullets:
            text = " ".join(str(bullet).split()).strip()
            if not text:
                continue
            if len(text) <= max_chars:
                compacted.append(text)
                continue
            parts = re.split(
                r"(?<=[.;!?])\s+|\s+[—-]\s+|;\s+|:\s+|,\s+(?=[A-Z0-9])",
                text,
            )
            kept = False
            for part in parts:
                part = " ".join(part.split()).strip(" ;,-()")
                # OLD: if 16 <= len(part) <= max_chars:
                # Raised minimum from 16 → 40 to drop truncated fragments
                if 40 <= len(part) <= max_chars:
                    compacted.append(part)
                    kept = True
                if len(compacted) >= 8:
                    break
            if kept:
                continue
            compacted.append(text)
        return DirectBulletWriterAgent._merge_fragments(compacted)

    @staticmethod
    def _merge_fragments(items: List[str]) -> List[str]:
        merged: List[str] = []
        for item in items:
            text = " ".join(str(item or "").split()).strip()
            if not text:
                continue
            fragment = bool(re.match(r"^(?:and|or|but|with|while|including|such as|each|32gb|16gb|250gb|40gb)\b", text, flags=re.IGNORECASE))
            if merged and (fragment or (len(text.split()) <= 4 and re.search(r"\d|gb|mb|cores?|ram|disk", text, flags=re.IGNORECASE))):
                merged[-1] = f"{merged[-1].rstrip(' .;:,')}, {text.lstrip(' ,;:.')}"
                continue
            merged.append(text)
        return merged

    @staticmethod
    def _empty_slide(spec):
        from src.models.slide import SlideContent
        return SlideContent(slide=spec, content=[])

    @staticmethod
    def _chunks(items: List, size: int) -> List[List]:
        return [items[i : i + size] for i in range(0, len(items), size)]
