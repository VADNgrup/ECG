from dataclasses import asdict
import json
import re
from typing import Any, Dict, List

from src.models.context import DocumentContext
from src.models.slide import Slide
from src.workflow.agents.content_quality import ContentQualityAgent
from src.utils.config import Config
from src.utils.llm import chat
from src.utils.parse_llm_response import parse_json_response


class SlidePacketBuilderAgent:
    MAX_EVIDENCE_CHARS = 4800
    MAX_SUPPORT_UNITS = 4

    _LINE_NOISE_RE = re.compile(
        r"^(?:"
        r"#{1,4}\s"
        r"|(?:Figure|Fig\.|Table|Algorithm|Listing)\s*\d"
        r"|\*(?:Figure|Fig\.|Table|Hình|Bảng)"
        r"|\$\$"
        r"|!\["
        r"|\|\s*[-:]{3}"
        r"|-{3,}$"
        r")",
        re.IGNORECASE,
    )

    def __init__(self, model: str):
        self.model = model
        self.content_tools = ContentQualityAgent(model)

    def build_packets(self, slide_specs: List[Slide], context: DocumentContext) -> List[Dict[str, Any]]:
        if Config.ABLATION_MODE == 1:
            return self._build_packets_ablation1(slide_specs)
        return self._build_packets_direct(slide_specs, context)

    def _build_packets_direct(self, slide_specs: List[Slide], context: DocumentContext) -> List[Dict[str, Any]]:
        """Map slides directly to document pages (context.text_content.markdown split by page markers)
        and extract evidence straight from raw page text.
        """
        pages = self._split_pages(context.text_content.markdown)
        slide_to_pages = self._llm_map_pages(slide_specs, pages)

        packets: List[Dict[str, Any]] = []
        for spec in slide_specs:
            mapped_pages = sorted(set(slide_to_pages.get(spec.slide_number, [])))
            page_texts = [text for (num, text) in pages if num in mapped_pages]
            evidence_text = "\n\n".join(page_texts)

            packet = {
                "slide_number": spec.slide_number,
                "slide_title": spec.slide_title,
                "slide_type": spec.slide_type.value if hasattr(spec.slide_type, "value") else str(spec.slide_type),
                "goal": spec.goal,
                "intent": self._infer_intent(spec, None),
                "coverage_mode": "normal",
                "source_pages": mapped_pages,
                "home_pages": mapped_pages,
                "required_facts": [],
                "required_checks": [],
                "coverage_items": [],
                "evidence": evidence_text,
                "section_assets": [],
            }
            if spec.table:
                packet["table"] = asdict(spec.table)
            if spec.latex_block_formula:
                packet["latex_block_formula"] = spec.latex_block_formula
            packets.append(packet)
        return packets

        # Flatten all sections
        all_sections = []
        for card in compact.get("page_cards", []):
            page_num = card.get("page", 1)
            for section in card.get("sections", []):
                all_sections.append({
                    "page": page_num,
                    "heading": section.get("heading") or f"Page {page_num}",
                    "level": section.get("level", 1),
                    "lines": section.get("lines", []),
                    "assets": section.get("assets", []),
                })
                
        # Call LLM to map slide_number -> list of section indices
        slide_to_indices = self._llm_map_sections(slide_specs, all_sections)
                
        packets: List[Dict[str, Any]] = []
        used_section_indices: set[int] = set()
        
        for spec in slide_specs:
            mapped_indices = slide_to_indices.get(spec.slide_number, [])
            
            home_pages = set()
            evidence_lines = []
            section_assets = []
            
            for idx in mapped_indices:
                if 0 <= idx < len(all_sections):
                    sec = all_sections[idx]
                    home_pages.add(sec["page"])
                    evidence_lines.extend(sec["lines"])
                    section_assets.extend(sec["assets"])
                    used_section_indices.add(idx)
                
            evidence_text = "\n".join(evidence_lines)
            
            # Use original fallback for evidence if empty
            if not evidence_text:
                thesis = compact.get("document_insights", {}).get("document_thesis", "")
                evidence_text = thesis
                
            # Clean evidence text
            evidence_text = self.content_tools._sentence_safe_truncate(evidence_text, self.MAX_EVIDENCE_CHARS)
            
            packet = {
                "slide_number": spec.slide_number,
                "slide_title": spec.slide_title,
                "slide_type": spec.slide_type.value if hasattr(spec.slide_type, "value") else str(spec.slide_type),
                "goal": spec.goal,
                "intent": self._infer_intent(spec, None),
                "coverage_mode": "normal",
                "source_pages": sorted(list(home_pages)),
                "home_pages": sorted(list(home_pages)),
                "required_facts": [],
                "required_checks": [],
                "coverage_items": evidence_lines[:6] if evidence_lines else [],
                "evidence": evidence_text,
                "section_assets": section_assets,
            }
            if spec.table:
                packet["table"] = asdict(spec.table)
            if spec.latex_block_formula:
                packet["latex_block_formula"] = spec.latex_block_formula
            packets.append(packet)
            
        return packets

    def _build_packets_ablation1(self, slide_specs: List[Slide]) -> List[Dict[str, Any]]:
        """Ablation 1: skip section mapping (_llm_map_sections) entirely — no source-grounding evidence.

        DirectBulletWriterAgent.write() hard-requires non-empty packets, so we still emit one
        packet per spec, but with all evidence/grounding fields empty. This isolates exactly the
        packet builder's contribution (source-grounding) for the ablation.
        """
        packets: List[Dict[str, Any]] = []
        for spec in slide_specs:
            packet = {
                "slide_number": spec.slide_number,
                "slide_title": spec.slide_title,
                "slide_type": spec.slide_type.value if hasattr(spec.slide_type, "value") else str(spec.slide_type),
                "goal": spec.goal,
                "intent": self._infer_intent(spec, None),
                "coverage_mode": "normal",
                "source_pages": [],
                "home_pages": [],
                "required_facts": [],
                "required_checks": [],
                "coverage_items": [],
                "evidence": "",
                "section_assets": [],
            }
            if spec.table:
                packet["table"] = asdict(spec.table)
            if spec.latex_block_formula:
                packet["latex_block_formula"] = spec.latex_block_formula
            packets.append(packet)
        return packets



    def _llm_map_pages(self, slide_specs: List[Slide], pages: List[tuple]) -> Dict[int, List[int]]:
        catalog_lines = []
        for page_num, page_text in pages:
            snippet = " ".join(page_text.split())[:150]
            catalog_lines.append(f"[Page {page_num}] {snippet}")
        catalog_text = "\n".join(catalog_lines)

        slides_lines = [f"Slide {spec.slide_number}: {spec.slide_title} (Goal: {spec.goal})" for spec in slide_specs]
        slides_text = "\n".join(slides_lines)

        prompt = f"""You are an expert presentation content curator. Your task is to accurately map slides to the raw document pages that provide their underlying facts.

# DOCUMENT PAGES (raw text preview)
{catalog_text}

# SLIDES TO BE GENERATED
{slides_text}

# INSTRUCTIONS
1. For each slide, identify the page numbers that contain the exact facts needed to fulfill the slide's Goal.
2. Every slide MUST be mapped to at least one page number.
3. Maintain a logical, sequential progression through the document where possible.
4. Output ONLY a raw JSON dictionary. Do not include markdown blocks. Keys are slide numbers (strings), values are arrays of page numbers (integers).

Example output:
{{"1": [1, 2], "2": [3]}}"""
        messages = [{"role": "user", "content": prompt}]

        print("[SlidePacketBuilder] (ablation3) Calling LLM to map raw pages to slides...")
        try:
            def _invoke_llm(msgs):
                return chat(self.model, msgs, temperature=0.1, max_tokens=1024)

            response = _invoke_llm(messages)
            mapping_dict = parse_json_response(response, _invoke_llm, expect_list=False)

            result = {}
            if isinstance(mapping_dict, dict):
                for k, v in mapping_dict.items():
                    try:
                        slide_num = int(k)
                        if isinstance(v, list):
                            result[slide_num] = [int(i) for i in v]
                    except ValueError:
                        continue
            return result
        except Exception as e:
            print(f"[SlidePacketBuilder] Failed to map pages using LLM: {e}")
            return {}

    @staticmethod
    def _split_pages(markdown: str) -> List[tuple]:
        matches = list(re.finditer(r"<!--\s*PAGE\s+(\d+)\s*-->", markdown, flags=re.IGNORECASE))
        if not matches:
            return [(1, markdown)]
        pages = []
        for idx, match in enumerate(matches):
            start = match.end()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
            pages.append((int(match.group(1)), markdown[start:end].strip()))
        return pages

    def _llm_map_sections(self, slide_specs: List[Slide], all_sections: List[Dict]) -> Dict[int, List[int]]:
        catalog_lines = []
        for idx, sec in enumerate(all_sections):
            # Provide enough context to be accurate without being overly verbose
            snippet = " ".join(sec.get("lines", [])[:2])
            if len(snippet) > 150:
                snippet = snippet[:147] + "..."
            catalog_lines.append(f"[{idx}] (Level {sec.get('level', 1)}) {sec['heading']} | {snippet}")
            
        catalog_text = "\n".join(catalog_lines)
        
        slides_lines = []
        for spec in slide_specs:
            slides_lines.append(f"Slide {spec.slide_number}: {spec.slide_title} (Goal: {spec.goal})")
            
        slides_text = "\n".join(slides_lines)
        
        # Robust prompt balancing token efficiency and strict instruction quality
        prompt = f"""You are an expert presentation content curator. Your task is to accurately map slides to the document sections that provide their underlying facts.

# DOCUMENT SECTIONS CATALOG
{catalog_text}

# SLIDES TO BE GENERATED
{slides_text}

# INSTRUCTIONS
1. For each slide, identify the indices of the sections that contain the exact facts needed to fulfill the slide's Goal.
2. If you select a parent section (e.g., Level 1), you should generally also include its immediate child sub-sections (e.g., Level 2) if they contain relevant details.
3. Every slide MUST be mapped to at least one section index.
4. Maintain a logical, sequential progression through the document where possible.
5. Output ONLY a raw JSON dictionary. Do not include markdown blocks. Keys are slide numbers (strings), values are arrays of section indices (integers).

Example output:
{{"1": [0, 1, 2], "2": [3, 4]}}"""
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        print("[SlidePacketBuilder] Calling LLM to map sections to slides...")
        try:
            def _invoke_llm(msgs):
                return chat(self.model, msgs, temperature=0.1, max_tokens=1024)
                
            response = _invoke_llm(messages)
            mapping_dict = parse_json_response(response, _invoke_llm, expect_list=False)
            
            result = {}
            if isinstance(mapping_dict, dict):
                for k, v in mapping_dict.items():
                    try:
                        slide_num = int(k)
                        if isinstance(v, list):
                            result[slide_num] = [int(i) for i in v]
                    except ValueError:
                        continue
            return result
        except Exception as e:
            print(f"[SlidePacketBuilder] Failed to map sections using LLM: {e}")
            return {}

    @staticmethod
    def _normalize_label(text: str) -> str:
        clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(text or "")).strip()
        return clean.lower()

    def _rank_units_for_slide(
        self,
        spec: Slide,
        units: List[Dict[str, Any]],
        must_have_points: List[Dict[str, Any]],
        document_insights: Dict[str, Any],
        all_slide_terms: dict[int, set[str]] | None = None,
    ) -> List[Dict[str, Any]]:
        scope = f"{spec.slide_title} {spec.goal}"
        title_terms = self._query_terms(scope)
        slide_numbers = self._numbers_in_text(scope)
        must_have_boost_ids = self._must_have_boost_ids(scope, must_have_points, document_insights)
        slide_bucket = self._semantic_bucket(scope)
        wants_structured_detail = self._title_wants_structured_detail(scope)
        ranked: List[Dict[str, Any]] = []
        for unit in units:
            score = 0.0
            unit_text = self._unit_text(unit)
            unit_terms = self._query_terms(unit_text)
            overlap = len(title_terms & unit_terms)
            if overlap:
                score += overlap * 2.8
            score += self._token_overlap_score(title_terms, unit_terms) * 6.0
            score += float(unit.get("salience_score", 0.0)) * 5.0
            if unit.get("slideworthy"):
                score += 1.5
            if unit.get("unit_id") in must_have_boost_ids:
                score += 5.0
            if slide_numbers and unit.get("number") in slide_numbers:
                score += 8.0
            if self._same_outline_family(spec.slide_title, unit):
                score += 2.0
            score += self._bucket_score(slide_bucket, self._semantic_bucket(unit_text))
            if self._looks_noise_unit(unit):
                score -= 6.0
            if self._is_metadataish(spec.slide_title) and unit.get("type") in {"page_focus", "must_have_hint"}:
                score -= 2.0
            if wants_structured_detail and re.search(r"\d", unit_text):
                score += 2.0
            if wants_structured_detail and ("|" in str(unit.get("summary", "")) or unit.get("type") == "table_row"):
                score += 1.5
            if wants_structured_detail and unit.get("type") == "must_have_hint" and not re.search(r"\d", unit_text):
                score -= 2.5
            # Cross-slide penalty: penalise units that match another slide's topic better
            if all_slide_terms and unit_terms:
                current_overlap = self._token_overlap_score(title_terms, unit_terms)
                best_other = max(
                    (self._token_overlap_score(other_terms, unit_terms)
                     for num, other_terms in all_slide_terms.items()
                     if num != spec.slide_number and other_terms),
                    default=0.0,
                )
                if best_other > current_overlap + 0.20:
                    score = max(score - 5.0, -3.0)
                elif best_other > current_overlap + 0.10:
                    score = max(score - 2.0, -3.0)
            ranked.append({"score": score, "unit": unit})
        ranked.sort(
            key=lambda item: (
                -item["score"],
                -float(item["unit"].get("salience_score", 0.0)),
                self._first_source_page(item["unit"]),
                item["unit"].get("title", ""),
            )
        )
        return ranked

    def _choose_anchor_unit(self, spec: Slide, ranked_units: List[Dict[str, Any]], used_anchor_ids: set[str], used_anchor_pages: set[int] | None = None) -> Dict[str, Any] | None:
        viable: List[tuple[float, Dict[str, Any]]] = []
        weak_title = self._is_weak_slide_title(spec.slide_title)
        used_anchor_pages = used_anchor_pages or set()
        for row in ranked_units:
            unit = row["unit"]
            unit_id = str(unit.get("unit_id") or "")
            if not unit_id:
                continue
            if self._looks_noise_unit(unit):
                continue
            fit = self._anchor_fit_score(spec, unit)
            if weak_title and unit.get("type") in {"must_have_hint", "detail_bullet", "detail_row", "table_row"}:
                fit -= 0.22
            if fit < 0.18 and viable:
                continue
            reuse_penalty = 7.0 if unit_id in used_anchor_ids else 0.0
            # Page-level penalty: units from already-anchored pages get penalised even if different unit_id
            unit_pages = set(unit.get("source_pages", [self._first_source_page(unit)]))
            page_penalty = 9.0 if unit_pages & used_anchor_pages else 0.0
            viable.append((float(row["score"]) + fit * 10.0 - reuse_penalty - page_penalty, unit))
        if weak_title:
            broad = [
                (score, unit) for score, unit in viable
                if unit.get("type") in {"section", "page_focus"} and not self._looks_noise_unit(unit)
            ]
            if broad:
                broad.sort(key=lambda item: (-item[0], -float(item[1].get("salience_score", 0.0)), self._first_source_page(item[1])))
                return broad[0][1]
        if viable:
            viable.sort(key=lambda item: (-item[0], -float(item[1].get("salience_score", 0.0)), self._first_source_page(item[1])))
            return viable[0][1]
        return ranked_units[0]["unit"] if ranked_units else None

    def _choose_support_units(self, spec: Slide, anchor_unit: Dict[str, Any] | None, ranked_units: List[Dict[str, Any]], slide_overlap_floor: float = 0.36, home_pages: set | None = None) -> List[Dict[str, Any]]:
        if not anchor_unit:
            return []
        home_pages = home_pages or set()
        support: List[Dict[str, Any]] = []
        anchor_page = self._first_source_page(anchor_unit)
        anchor_type = anchor_unit.get("type")
        anchor_merge = anchor_unit.get("merge_key") or anchor_unit.get("outline_label") or anchor_unit.get("title")
        anchor_terms = self._query_terms(self._unit_text(anchor_unit))
        anchor_bucket = self._semantic_bucket(self._unit_text(anchor_unit))
        anchor_score = 0.0
        for row in ranked_units:
            if row["unit"].get("unit_id") == anchor_unit.get("unit_id"):
                anchor_score = float(row["score"])
                break
        for row in ranked_units:
            unit = row["unit"]
            if unit.get("unit_id") == anchor_unit.get("unit_id"):
                continue
            if self._looks_noise_unit(unit):
                continue
            if unit.get("type") in {"page_focus", "must_have_hint"}:
                continue
            unit_page = self._first_source_page(unit)
            page_gap = abs(unit_page - anchor_page)
            # Units outside the anchor's home section require much stricter thresholds
            if home_pages and unit_page not in home_pages and page_gap > 1:
                unit_terms_check = self._query_terms(self._unit_text(unit))
                slide_overlap_check = self._token_overlap_score(self._query_terms(f"{spec.slide_title} {spec.goal}"), unit_terms_check)
                family_overlap_check = self._token_overlap_score(anchor_terms, unit_terms_check)
                if slide_overlap_check < 0.55 or family_overlap_check < 0.45:
                    continue
            same_family = (unit.get("merge_key") or unit.get("outline_label") or unit.get("title")) == anchor_merge
            same_type = unit.get("type") == anchor_type
            unit_terms = self._query_terms(self._unit_text(unit))
            unit_bucket = self._semantic_bucket(self._unit_text(unit))
            family_overlap = self._token_overlap_score(anchor_terms, unit_terms)
            slide_overlap = self._token_overlap_score(self._query_terms(f"{spec.slide_title} {spec.goal}"), unit_terms)
            if page_gap > 2 and (slide_overlap < 0.45 or family_overlap < 0.28):
                continue
            if anchor_type == "must_have_hint" and page_gap > 1 and slide_overlap < 0.45:
                continue
            score_ratio = float(row["score"]) / max(anchor_score, 1.0)
            keep = (
                (same_family and family_overlap >= 0.18)
                or (same_type and page_gap <= 1 and family_overlap >= 0.26 and self._bucket_score(anchor_bucket, unit_bucket) >= 0.0)
                or (page_gap == 0 and family_overlap >= 0.32 and score_ratio >= 0.7 and self._bucket_score(anchor_bucket, unit_bucket) >= 0.0)
                or (slide_overlap >= slide_overlap_floor and family_overlap >= 0.2)  # OLD: 0.36
            )
            if not keep:
                continue
            if any(existing.get("unit_id") == unit.get("unit_id") for existing in support):
                continue
            current_purity = self._topic_purity(spec, anchor_unit, support)
            next_purity = self._topic_purity(spec, anchor_unit, [*support, unit])
            if support and next_purity + 0.06 < current_purity:
                continue
            if next_purity < 0.34 and family_overlap < 0.4:
                continue
            support.append(unit)
            if len(support) >= self.MAX_SUPPORT_UNITS:
                break
        return support

    def _anchor_fit_score(self, spec: Slide, unit: Dict[str, Any]) -> float:
        slide_terms = self._query_terms(f"{spec.slide_title} {spec.goal}")
        label_terms = self._query_terms(" ".join(str(part) for part in [
            unit.get("title", ""),
            unit.get("outline_label", ""),
            unit.get("merge_key", ""),
        ] if part))
        summary_terms = self._query_terms(str(unit.get("summary", "")))
        if not slide_terms:
            return 0.0
        label_fit = self._token_overlap_score(slide_terms, label_terms)
        summary_fit = self._token_overlap_score(slide_terms, summary_terms)
        fit = label_fit * 0.7 + summary_fit * 0.3
        unit_type = str(unit.get("type") or "")
        if self._is_weak_slide_title(spec.slide_title) and unit_type in {"must_have_hint", "detail_bullet", "detail_row", "table_row"}:
            fit -= 0.25
        if unit_type in {"table_row", "detail_row"} and not self._title_wants_structured_detail(spec.slide_title):
            fit -= 0.18
        if unit.get("presentation_role") == "topic_candidate":
            fit += 0.08
        return max(0.0, min(1.0, fit))

    def _required_facts(
        self,
        spec: Slide,
        anchor_unit: Dict[str, Any] | None,
        support_units: List[Dict[str, Any]],
        compact: Dict[str, Any],
    ) -> List[str]:
        facts: List[str] = []
        table = getattr(spec, "table", None)
        if table and getattr(table, "table_markdown", ""):
            facts.extend(self._facts_from_markdown_table(getattr(table, "table_caption", ""), getattr(table, "table_markdown", "")))
        if anchor_unit:
            facts.extend(self._facts_from_unit(anchor_unit, anchor=True))
        for unit in support_units:
            facts.extend(self._facts_from_unit(unit, anchor=False))
        if not facts:
            facts.extend(self._facts_from_document_insights(compact))
        source_pages = self._source_pages(anchor_unit, support_units)
        facts.extend(self._facts_from_matching_must_have(spec, compact, facts, source_pages))
        return self._select_language_coherent_facts(spec.slide_title, self._dedupe_facts(facts))[:8]

    def _facts_from_matching_must_have(
        self, spec: Slide, compact: Dict[str, Any], existing_facts: List[str], source_pages: List[int] | None = None
    ) -> List[str]:
        if len(existing_facts) >= 4:
            return []
        slide_terms = self._query_terms(f"{spec.slide_title} {spec.goal}")
        existing_text = " ".join(existing_facts).lower()
        units_by_id = {u["unit_id"]: u for u in (compact.get("presentation_units") or compact.get("content_units") or [])}
        additions: List[str] = []
        for point in compact.get("must_have_points", []):
            # Skip must_have_points from distant pages to avoid intro facts bleeding into later slides
            if source_pages:
                point_pages: set[int] = set()
                for uid in point.get("source_unit_ids", []):
                    unit = units_by_id.get(uid)
                    if unit:
                        for p in unit.get("source_pages", [unit.get("page")] if unit.get("page") else []):
                            if isinstance(p, int):
                                point_pages.add(p)
                if point_pages and not any(abs(pp - sp) <= 1 for pp in point_pages for sp in source_pages):
                    continue
            text = f"{point.get('label', '')}: {point.get('summary', '')}"
            point_terms = self._query_terms(text)
            if not point_terms:
                continue
            overlap = self._token_overlap_score(slide_terms, point_terms)
            shared = slide_terms & point_terms
            if overlap < 0.4 and len(shared) < 2:
                continue
            clean = self._clean_fact(text)
            if not clean or clean.lower() in existing_text:
                continue
            additions.append(clean)
            if len(existing_facts) + len(additions) >= 4:
                break
        return additions

    @staticmethod
    def _facts_from_markdown_table(caption: str, table_markdown: str) -> List[str]:
        lines = [line.strip() for line in str(table_markdown or "").splitlines() if line.strip().startswith("|")]
        if len(lines) < 3:
            return []
        headers = [cell.strip(" `") for cell in lines[0].strip("|").split("|")]
        facts: List[str] = []
        for line in lines[2:]:
            cells = [cell.strip(" `") for cell in line.strip("|").split("|")]
            if len(cells) < 3:
                continue
            label = cells[0]
            values = cells[1:]
            if len(values) == 2 and all(re.search(r"\d", value) for value in values):
                facts.append(f"{label}: {headers[1]} {values[0]} versus {headers[2]} {values[1]}.")
                continue
            paired = [f"{headers[idx]} {value}" for idx, value in enumerate(cells[1:], start=1) if idx < len(headers) and value]
            if paired:
                facts.append(f"{label}: {'; '.join(paired)}.")
        if facts:
            return facts[:5]
        return [caption] if caption else []

    def _coverage_items(self, anchor_unit: Dict[str, Any] | None, support_units: List[Dict[str, Any]]) -> List[str]:
        numbered_units = [unit for unit in [anchor_unit, *support_units] if unit and unit.get("number") is not None]
        if not numbered_units:
            return []
        items = []
        for unit in sorted(numbered_units, key=lambda item: int(item.get("number") or 0)):
            label = str(unit.get("number"))
            summary = self._clean_fact(unit.get("summary") or unit.get("title") or "")
            if not summary:
                continue
            items.append(f"{label}. {summary}")
        return self._dedupe_facts(items)[:6]

    def _required_checks(self, required_facts: List[str], coverage_items: List[str]) -> List[Dict[str, Any]]:
        checks: List[Dict[str, Any]] = []
        if required_facts:
            checks.append({"kind": "required_facts", "items": required_facts[:6]})
            checks.append({"kind": "source_coverage", "items": required_facts[:6], "min_hits": min(3, len(required_facts[:6]))})
        if coverage_items:
            checks.append({"kind": "coverage_items", "items": coverage_items[:6], "min_hits": min(3, len(coverage_items))})
        return checks

    @staticmethod
    def _reconstruct_sentences(raw_lines: List[str], title_terms: set[str] | None = None) -> List[str]:
        sentences: List[str] = []
        current = ""
        in_relevant_section = True  # include pre-heading content by default
        any_heading_matched = False  # tracks if any heading on this page matched anchor terms

        for line in raw_lines:
            line = line.strip()
            if not line:
                continue

            # Detect markdown headings before _LINE_NOISE_RE (which also matches ^#{1,4}\s)
            heading_match = re.match(r'^#{1,4}\s+(.+)$', line)
            if heading_match:
                if current and len(current) >= 60:  # save partial content before heading resets accumulator
                    sentences.append(current.strip())
                current = ""
                if title_terms:
                    heading_terms = SlidePacketBuilderAgent._query_terms(heading_match.group(1))
                    if heading_terms:
                        overlap = len(title_terms & heading_terms)
                        ratio = overlap / max(1, min(len(title_terms), len(heading_terms)))
                        in_relevant_section = ratio >= 0.5
                        if in_relevant_section:
                            any_heading_matched = True
                continue

            if SlidePacketBuilderAgent._LINE_NOISE_RE.match(line):
                if current and len(current) >= 60:  # save substantial partial content before reset
                    sentences.append(current.strip())
                current = ""
                continue

            # Only filter by section if at least one heading on this page matched anchor terms.
            # When no heading matches (parent heading, different terminology), include all content.
            if title_terms and not in_relevant_section and any_heading_matched:
                current = ""
                continue

            current = (current + " " + line) if current else line
            if re.search(r"[.!?]$", current):
                if len(current) > 40:
                    sentences.append(current.strip())
                current = ""

        return sentences

    def _build_evidence(
        self,
        anchor_unit: Dict[str, Any] | None,
        support_units: List[Dict[str, Any]],
        compact: Dict[str, Any],
    ) -> str:
        lines: List[str] = []
        if anchor_unit:
            lines.append(self._unit_evidence_block(anchor_unit, "anchor"))
        anchor_summary_words = set(re.findall(r'[A-Za-zÀ-ỹ]{3,}', (anchor_unit.get("summary", "") if anchor_unit else "").lower()))
        for unit in support_units:
            unit_summary_words = set(re.findall(r'[A-Za-zÀ-ỹ]{3,}', unit.get("summary", "").lower()))
            if unit_summary_words and anchor_summary_words:
                contained = len(unit_summary_words & anchor_summary_words) / len(unit_summary_words)
                if contained >= 0.85:
                    continue
            lines.append(self._unit_evidence_block(unit, "support"))
        if not lines:
            thesis = compact.get("document_insights", {}).get("document_thesis", "")
            if thesis:
                lines.append(thesis)
        # Supplement with page card summaries when unit evidence is thin
        current_len = sum(len(line) for line in lines if line)
        if current_len < 800:
            source_pages = self._source_pages(anchor_unit, support_units)
            page_cards = {card.get("page"): card for card in compact.get("page_cards", [])}
            page_insights = {item.get("page"): item for item in compact.get("page_insights", [])}
            for page in source_pages[:2]:
                card = page_cards.get(page)
                if card and card.get("summary"):
                    lines.append(f"PAGE {page} CONTENT:\n{card['summary']}")
                insight = page_insights.get(page)
                if insight and insight.get("must_have_points"):
                    points = insight["must_have_points"][:4]
                    lines.append(f"PAGE {page} KEY POINTS:\n" + "\n".join(f"- {p}" for p in points))
        # Enrich with reconstructed sentences from raw page lines, filtered by anchor section
        anchor_title_terms = self._query_terms(
            " ".join(filter(None, [
                anchor_unit.get("title", "") if anchor_unit else "",
                anchor_unit.get("outline_label", "") if anchor_unit else "",
                anchor_unit.get("merge_key", "") if anchor_unit else "",
            ]))
        ) or None
        page_cards_map = {card.get("page"): card for card in compact.get("page_cards", [])}
        anchor_pages = set(anchor_unit.get("source_pages", []) if anchor_unit else [])
        sent_blocks: List[str] = []
        for page in self._source_pages(anchor_unit, support_units)[:3]:
            card = page_cards_map.get(page)
            if not card:
                continue
            relevant_lines = self._select_section_lines(card, anchor_title_terms, page in anchor_pages)
            sents = self._reconstruct_sentences(relevant_lines, None)[:8]
            if sents:
                sent_blocks.append("\n".join(sents))
        if sent_blocks:
            covered_words = set(re.findall(r'[A-Za-zÀ-ỹ]{3,}', "\n".join(lines).lower()))
            filtered_blocks: List[str] = []
            for block in sent_blocks:
                block_sents = [s for s in block.splitlines() if s.strip()]
                new_sents = []
                for sent in block_sents:
                    sent_words = set(re.findall(r'[A-Za-zÀ-ỹ]{3,}', sent.lower()))
                    if not sent_words:
                        continue
                    overlap = len(sent_words & covered_words) / len(sent_words)
                    if overlap < 0.70:
                        new_sents.append(sent)
                        covered_words |= sent_words
                if new_sents:
                    filtered_blocks.append("\n".join(new_sents))
            if filtered_blocks:
                lines.append("SOURCE LINES:\n" + "\n\n".join(filtered_blocks))
        return self.content_tools._sentence_safe_truncate("\n\n".join(item for item in lines if item), self.MAX_EVIDENCE_CHARS)

    def _source_alignment(
        self,
        spec: Slide,
        anchor_unit: Dict[str, Any] | None,
        support_units: List[Dict[str, Any]],
        ranked_units: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "slide_number": spec.slide_number,
            "slide_title": spec.slide_title,
            "anchor_unit_id": anchor_unit.get("unit_id") if anchor_unit else None,
            "anchor_title": anchor_unit.get("title") if anchor_unit else "",
            "support_unit_ids": [unit.get("unit_id") for unit in support_units if unit.get("unit_id")],
            "candidate_unit_ids": [row["unit"].get("unit_id") for row in ranked_units[:6] if row["unit"].get("unit_id")],
            "topic_purity": round(self._topic_purity(spec, anchor_unit, support_units), 3),
        }

    def _forbidden_unit_ids(
        self,
        anchor_unit: Dict[str, Any] | None,
        support_units: List[Dict[str, Any]],
        ranked_units: List[Dict[str, Any]],
    ) -> List[str]:
        if not anchor_unit:
            return []
        allowed = {anchor_unit.get("unit_id"), *(unit.get("unit_id") for unit in support_units)}
        forbidden: List[str] = []
        anchor_page = self._first_source_page(anchor_unit)
        anchor_bucket = self._semantic_bucket(self._unit_text(anchor_unit))
        for row in ranked_units[4:12]:
            unit = row["unit"]
            unit_id = unit.get("unit_id")
            if not unit_id or unit_id in allowed:
                continue
            bucket_penalty = self._bucket_score(anchor_bucket, self._semantic_bucket(self._unit_text(unit)))
            if bucket_penalty < 0 or abs(self._first_source_page(unit) - anchor_page) > 2:
                forbidden.append(unit_id)
            if len(forbidden) >= 8:
                break
        return forbidden

    def _topic_purity(self, spec: Slide, anchor_unit: Dict[str, Any] | None, support_units: List[Dict[str, Any]]) -> float:
        if not anchor_unit:
            return 0.0
        slide_terms = self._query_terms(f"{spec.slide_title} {spec.goal}")
        anchor_terms = self._query_terms(self._unit_text(anchor_unit))
        base = self._token_overlap_score(slide_terms, anchor_terms)
        if not support_units:
            return base
        support_scores = [self._token_overlap_score(anchor_terms, self._query_terms(self._unit_text(unit))) for unit in support_units]
        return max(0.0, min(1.0, (base * 0.65) + (sum(support_scores) / max(1, len(support_scores)) * 0.35)))

    def _facts_from_unit(self, unit: Dict[str, Any], anchor: bool) -> List[str]:
        facts: List[str] = []
        label = unit.get("outline_label") or unit.get("title") or ""
        summary = self._clean_fact(unit.get("summary") or "")
        if "|" in str(unit.get("summary") or ""):
            facts.extend(self._facts_from_table_summary(label, str(unit.get("summary") or "")))
        if unit.get("number") is not None and summary:
            facts.append(f"{unit.get('number')}. {summary}")
        elif anchor and label and summary and label.lower() not in summary.lower():
            # Only prepend label when it adds substantive context (not a short section heading)
            if len(label.split()) >= 5:
                facts.append(f"{label}: {summary}")
            else:
                facts.append(summary)
        elif summary:
            facts.append(summary)
        title = self._clean_fact(unit.get("title") or "")
        # Skip bare section titles (short headings with no sentence structure) as standalone facts
        if anchor and title and not re.fullmatch(r"item\s+\d+", title, flags=re.IGNORECASE) and title != label and title not in facts:
            if len(title.split()) >= 5 or re.search(r"[.!?]$", title):
                facts.append(title)
        return facts

    @staticmethod
    def _facts_from_table_summary(label: str, summary: str) -> List[str]:
        rows = []
        for raw in str(summary or "").split("|"):
            cell = SlidePacketBuilderAgent._clean_fact(raw)
            if cell:
                rows.append(cell)
        joined = " | ".join(rows)
        facts: List[str] = []
        for match in re.finditer(r"(#? ?[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ ]{8,80})\s+\|\s+([\d,]+)\s+\|\s+([\d,]+)", joined):
            metric = match.group(1).strip()
            left = match.group(2)
            right = match.group(3)
            facts.append(f"{metric}: {left} in source baseline versus {right} in DOIBoost.")
        if facts:
            return facts[:4]
        if label and rows:
            return [f"{label}: {'; '.join(rows[:6])}"]
        return []

    @staticmethod
    def _facts_from_document_insights(compact: Dict[str, Any]) -> List[str]:
        insights = compact.get("document_insights", {})
        facts: List[str] = []
        if insights.get("document_thesis"):
            facts.append(str(insights["document_thesis"]))
        for label in insights.get("must_include_labels", [])[:4]:
            if isinstance(label, str):
                facts.append(label)
        return facts

    def _infer_intent(self, spec: Slide, anchor_unit: Dict[str, Any] | None) -> str:
        scope = f"{spec.slide_title} {spec.goal}".lower()
        if anchor_unit and anchor_unit.get("number") is not None:
            return "numbered_topic"
        if any(term in scope for term in ["workflow", "procedure", "steps", "quy trình", "các bước"]):
            return "procedure"
        if any(term in scope for term in ["overview", "tổng quan", "introduction", "giới thiệu", "background"]):
            return "overview"
        return "focused_topic"

    def _must_have_boost_ids(
        self,
        scope: str,
        must_have_points: List[Dict[str, Any]],
        document_insights: Dict[str, Any],
    ) -> set[str]:
        scope_terms = self._query_terms(scope)
        boost_ids: set[str] = set()
        preferred_labels = set(document_insights.get("must_include_labels", [])[:8])
        for point in must_have_points:
            label = str(point.get("label") or "")
            point_terms = self._query_terms(f"{label} {point.get('summary', '')}")
            overlap = len(scope_terms & point_terms)
            overlap_ratio = overlap / max(1, min(len(scope_terms), len(point_terms)))
            if overlap_ratio >= 0.25 or label in preferred_labels:
                for unit_id in point.get("source_unit_ids", [])[:3]:
                    if unit_id:
                        boost_ids.add(str(unit_id))
        return boost_ids

    @staticmethod
    def _same_outline_family(slide_title: str, unit: Dict[str, Any]) -> bool:
        slide_label = SlidePacketBuilderAgent._normalize_label(slide_title)
        unit_label = SlidePacketBuilderAgent._normalize_label(
            unit.get("outline_label") or unit.get("title") or ""
        )
        return bool(slide_label and unit_label and (slide_label == unit_label or slide_label in unit_label or unit_label in slide_label))

    @staticmethod
    def _looks_noise_unit(unit: Dict[str, Any]) -> bool:
        title = f"{unit.get('title', '')} {unit.get('summary', '')}".lower()
        if unit.get("quality") == "noise":
            return True
        if re.search(r"\bcite this document as\b|\bretrieved from\b|\bdoi\b|\breferences?\b|\backnowledgements?\b|\btarget audience\b|\bkey words?\b", title):
            return True
        if re.fullmatch(r"\s*\d+(?:\.\d+)?\s*(?:kb|mb|gb|tb)(?:\s*\([^)]*\))?\s*", title, flags=re.IGNORECASE):
            return True
        return False

    @staticmethod
    def _is_metadataish(text: str) -> bool:
        lower = text.lower()
        return any(term in lower for term in ["contact", "liên hệ", "thông tin cá nhân", "references", "cite", "doi"])

    @staticmethod
    def _semantic_bucket(text: str) -> str:
        terms = SlidePacketBuilderAgent._query_terms(text)
        stop = {
            "the", "and", "for", "with", "from", "this", "that", "into", "about", "document", "slide",
            "overview", "summary", "section", "content", "information", "using", "used", "uses",
            "các", "và", "của", "cho", "với", "trong", "thông", "tin", "tổng", "quan", "nội", "dung",
        }
        salient = [term for term in sorted(terms) if term not in stop and not term.isdigit()]
        return "|".join(salient[:4]) if salient else "general"

    @staticmethod
    def _bucket_score(slide_bucket: str, unit_bucket: str) -> float:
        if slide_bucket == "general" or unit_bucket == "general":
            return 0.0
        if slide_bucket == unit_bucket:
            return 2.4
        left = set(slide_bucket.split("|"))
        right = set(unit_bucket.split("|"))
        overlap = len(left & right) / max(1, min(len(left), len(right)))
        if overlap >= 0.5:
            return 1.8
        if overlap > 0:
            return 0.5
        return -1.4

    @staticmethod
    def _is_weak_slide_title(title: str) -> bool:
        clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(title or "")).strip().lower()
        terms = [term for term in SlidePacketBuilderAgent._query_terms(clean) if term not in {"overview", "summary", "content", "section", "document", "dataset", "data", "tổng", "quan"}]
        return len(terms) <= 1 and ":" not in clean

    @staticmethod
    def _title_wants_structured_detail(title: str) -> bool:
        clean = str(title or "").lower()
        terms = SlidePacketBuilderAgent._query_terms(clean)
        return bool(terms & {"table", "data", "dataset", "source", "metric", "metrics", "result", "results", "impact", "quality", "schema", "model", "bảng", "dữ", "liệu", "kết", "quả"})

    @staticmethod
    def _unit_text(unit: Dict[str, Any]) -> str:
        return " ".join(
            str(part)
            for part in [
                unit.get("title", ""),
                unit.get("summary", ""),
                unit.get("outline_label", ""),
                unit.get("merge_key", ""),
            ]
            if part
        )

    @staticmethod
    def _query_terms(text: str) -> set[str]:
        latin_terms = {
            term.lower()
            for term in re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", text or "")
            if len(term) >= 3
        }
        if latin_terms:
            return latin_terms
        cjk_terms = re.findall(r"[\u4e00-\u9fff]{1,4}", text or "")
        return set(cjk_terms)

    @staticmethod
    def _numbers_in_text(text: str) -> set[int]:
        return {int(item) for item in re.findall(r"\b(\d{1,2})\b", text or "")}

    @staticmethod
    def _token_overlap_score(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, min(len(left), len(right)))

    @staticmethod
    def _select_section_lines(
        card: Dict[str, Any],
        title_terms: set[str] | None,
        is_anchor_page: bool,
    ) -> List[str]:
        """Return lines from sections relevant to title_terms.
        is_anchor_page=True: fall back to all lines if no heading matches (VTM parent-heading case).
        is_anchor_page=False: return [] if no heading matches (prevents bleeding from unrelated pages).
        """
        sections = card.get("sections")
        if not sections:
            return card.get("lines", [])

        has_continuation = sections[0].get("heading") is None
        continuation_lines: List[str] = sections[0].get("lines", []) if has_continuation else []

        matched_lines: List[str] = []
        for section in sections:
            heading = section.get("heading")
            if heading is None:
                continue
            if not title_terms:
                matched_lines.extend(section.get("lines", []))
                continue
            heading_terms = SlidePacketBuilderAgent._query_terms(heading)
            if not heading_terms:
                matched_lines.extend(section.get("lines", []))
                continue
            overlap = len(title_terms & heading_terms)
            ratio = overlap / max(1, min(len(title_terms), len(heading_terms)))
            if ratio >= 0.5:
                matched_lines.extend(section.get("lines", []))

        if matched_lines:
            return continuation_lines + matched_lines
        if is_anchor_page:
            return card.get("lines", [])
        # Support page, no heading matched — still return pre-first-heading content (continuation
        # from the previous page's last matched section).
        return continuation_lines

    @staticmethod
    def _find_anchor_sections(anchor_unit: Dict[str, Any] | None, compact: Dict[str, Any]) -> set[int]:
        """Return pages whose compact sections match the anchor unit's heading,
        including direct child sub-sections (one heading level deeper only).
        """
        if not anchor_unit:
            return set()
        anchor_terms = SlidePacketBuilderAgent._query_terms(
            " ".join(filter(None, [
                anchor_unit.get("outline_label", ""),
                anchor_unit.get("title", ""),
                anchor_unit.get("merge_key", ""),
            ]))
        )
        home_pages: set[int] = set(anchor_unit.get("source_pages", []))
        if not anchor_terms:
            return home_pages
        # Build flat ordered list of all sections across all pages
        all_sections: list[tuple[int, dict]] = []
        for card in sorted(compact.get("page_cards", []), key=lambda c: c.get("page", 0)):
            for section in card.get("sections", []):
                all_sections.append((card.get("page", 0), section))
        # Find matched sections (ratio >= 0.40) and record flat index + level
        matched: list[tuple[int, int]] = []
        for i, (page, section) in enumerate(all_sections):
            heading = section.get("heading") or ""
            if not heading:
                continue
            heading_terms = SlidePacketBuilderAgent._query_terms(heading)
            if not heading_terms:
                continue
            ratio = len(anchor_terms & heading_terms) / max(1, min(len(anchor_terms), len(heading_terms)))
            if ratio >= 0.40:
                home_pages.add(page)
                matched.append((i, section.get("level", 1)))
        # Include direct children (level == match_level+1) until a sibling/parent appears
        for match_pos, match_level in matched:
            for j in range(match_pos + 1, len(all_sections)):
                child_page, child_section = all_sections[j]
                child_level = child_section.get("level", 1)
                if child_level <= match_level:
                    break
                if child_level == match_level + 1:
                    home_pages.add(child_page)
        return home_pages

    @staticmethod
    def _source_pages(anchor_unit: Dict[str, Any] | None, support_units: List[Dict[str, Any]]) -> List[int]:
        pages = []
        for unit in [anchor_unit, *support_units]:
            if not unit:
                continue
            for page in unit.get("source_pages", []):
                if page not in pages:
                    pages.append(page)
        return pages

    @staticmethod
    def _unit_evidence_block(unit: Dict[str, Any], label: str) -> str:
        title = unit.get("title", "")
        summary = unit.get("summary", "")
        pages = ",".join(str(page) for page in unit.get("source_pages", []))
        return (
            f"{label.upper()} UNIT\n"
            f"type: {unit.get('type', '')}\n"
            f"title: {title}\n"
            f"summary: {summary}\n"
            f"pages: {pages}\n"
        ).strip()

    @staticmethod
    def _dedupe_facts(items: List[str]) -> List[str]:
        result: List[str] = []
        seen: set[str] = set()
        for item in items:
            clean = SlidePacketBuilderAgent._clean_fact(item)
            if not clean:
                continue
            key = re.sub(r"\s+", " ", clean.lower())
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)
        return result

    @staticmethod
    def _select_language_coherent_facts(slide_title: str, facts: List[str]) -> List[str]:
        if not facts:
            return []
        title_script = SlidePacketBuilderAgent._dominant_script(slide_title)
        if title_script == "other":
            return facts
        aligned = [fact for fact in facts if SlidePacketBuilderAgent._dominant_script(fact) == title_script]
        return aligned if len(aligned) >= 2 else facts

    @staticmethod
    def _clean_fact(text: str) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip(" ;:-")
        clean = re.sub(r"^Item\s+\d+\s*:\s*", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"^Item\s+\d+\s*$", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"^Page\s+\d+\s+", "", clean, flags=re.IGNORECASE)
        if re.search(r"\bcite this document as\b|\bretrieved from\b|\bdoi\b", clean, flags=re.IGNORECASE):
            return ""
        return ContentQualityAgent._sentence_safe_truncate(clean, 220)

    @staticmethod
    def _normalize_label(text: str) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip().lower()
        clean = re.sub(r"^\d+(?:\.\d+)*\s*", "", clean)
        return clean

    @staticmethod
    def _dominant_script(text: str) -> str:
        counts = {
            "latin": len(re.findall(r"[A-Za-zÀ-ỹ]", text or "")),
            "cjk": len(re.findall(r"[\u4e00-\u9fff]", text or "")),
            "cyrillic": len(re.findall(r"[\u0400-\u04FF]", text or "")),
            "arabic": len(re.findall(r"[\u0600-\u06FF]", text or "")),
        }
        script, score = max(counts.items(), key=lambda item: item[1])
        return script if score > 0 else "other"

    @staticmethod
    def _first_source_page(unit: Dict[str, Any] | None) -> int:
        if not unit:
            return 999
        pages = unit.get("source_pages") or []
        numeric_pages = [int(page) for page in pages if isinstance(page, int) or str(page).isdigit()]
        return min(numeric_pages) if numeric_pages else 999
