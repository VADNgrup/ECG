import json
import re
from dataclasses import asdict
from typing import Any, Dict, List

from src.models.context import DocumentContext
from src.models.slide import Slide, SlideContent
from src.utils.config import Config
from src.utils.language_utils import dominant_script
from src.utils.llm import chat
from src.utils.parse_llm_response import parse_json_response


class ContentQualityAgent:
    MAX_OUTPUT_TOKENS = 2048
    MAX_EVIDENCE_PER_SLIDE = 3600
    BATCH_SIZE = 3
    BAD_PATTERNS = [
        r"source-backed details required",
        r"\bdetails required\b",
        r"\bplaceholder\b",
        r"\btodo\b",
        r"\bn/?a\b",
        r"insufficient source",
        r"not enough information",
        r"dagon university research journal",
        r"preserving the deci",
        r"explain the .* preserving",
        r"\bkey\s*words?\b",
        r"\btarget\s+audience\b",
        r"\bcite\s+this\s+document\s+as\b",
        r"\blast\s+retrieved\b",
        r"\breferences?\b\s*$",
        r"required facts?:",
        r"required checks?:",
        r"workflow action:",
        r"document id:",
        r"source file:",
        r"these items cover distinct source points",
        r"software executes the modeled optimization",
        r"source evidence supports this slide",
        r"^#+\s",
        r"\s#+\s",
        r"\b(?:using|with|by|to|from|into|of|for|and|or|the|a|an)\s+[a-z]\.?(?:\s|$)",
    ]

    def __init__(self, model: str):
        self.model = model

    def _chat(self, messages: list) -> str:
        return chat(self.model, messages, temperature=0.2, max_tokens=self.MAX_OUTPUT_TOKENS)

    def repair(self, slides: List[SlideContent], slide_specs: List[Slide], context: DocumentContext, slide_packets: List[Dict[str, Any]] | None = None) -> List[SlideContent]:
        repaired_slides, _ = self.repair_with_report(slides, slide_specs, context, slide_packets=slide_packets)
        return repaired_slides

    def repair_with_report(
        self,
        slides: List[SlideContent],
        slide_specs: List[Slide],
        context: DocumentContext,
        slide_packets: List[Dict[str, Any]] | None = None,
    ) -> tuple[List[SlideContent], Dict[str, Any]]:
        packet_by_number = {int(packet.get("slide_number")): packet for packet in (slide_packets or []) if packet.get("slide_number") is not None}
        issues, evidence_by_number, soft_issues = self._detect_all_issues(slides, context, packet_by_number)
        if not issues:
            report = {
                "status": "clean" if not soft_issues else "warnings",
                "blocking_issues": {},
                "advisory_issues": {},
                "soft_issues": soft_issues,
                "repaired_slide_numbers": [],
                "metrics": self._quality_metrics(slides, packet_by_number),
            }
            if soft_issues:
                print(f"[ContentQA] No blocking content issues found. Soft warnings on {len(soft_issues)} slide(s).")
            else:
                print("[ContentQA] No blocking content issues found.")
            return slides, report
        print(f"[ContentQA] Repairing {len(issues)} slide(s): {sorted(issues)}")
        repaired_by_number: Dict[int, Any] = {}
        flagged = [slide for slide in slides if slide.slide.slide_number in issues]
        for batch in self._chunks(flagged, self.BATCH_SIZE):
            repaired_by_number.update(self._repair_batch(batch, slide_specs, context, issues, evidence_by_number, packet_by_number))
        repaired_slides = []
        for slide in slides:
            content = repaired_by_number.get(slide.slide.slide_number, slide.content)
            content = self._normalise_content(content, slide.content)
            if isinstance(content, list):
                content = self._drop_title_echoes(content, slide.slide.slide_title)
            repaired_slides.append(SlideContent(slide=slide.slide, content=content, layout_hint=slide.layout_hint))
        unresolved, _, _ = self._detect_all_issues(repaired_slides, context, packet_by_number)
        if unresolved:
            repaired_slides = self._apply_deterministic_fallbacks(repaired_slides, unresolved, evidence_by_number, packet_by_number)
            unresolved, _, _ = self._detect_all_issues(repaired_slides, context, packet_by_number)
        advisory_unresolved = self._advisory_only(unresolved)
        blocking_unresolved = self._blocking_only(unresolved)
        report = {
            "status": "failed" if blocking_unresolved else ("warnings" if advisory_unresolved or soft_issues else "passed"),
            "blocking_issues": blocking_unresolved,
            "advisory_issues": advisory_unresolved,
            "soft_issues": soft_issues,
            "repaired_slide_numbers": sorted(repaired_by_number),
            "metrics": self._quality_metrics(repaired_slides, packet_by_number),
        }
        if blocking_unresolved:
            print(f"[ContentQA] Warning: Could not repair blocking slide issues: {blocking_unresolved}")
        return repaired_slides, report

    def _repair_batch(
        self,
        slides: List[SlideContent],
        slide_specs: List[Slide],
        context: DocumentContext,
        issues: Dict[int, List[str]],
        evidence_by_number: Dict[int, str],
        packet_by_number: Dict[int, Dict[str, Any]],
    ) -> Dict[int, Any]:
        spec_by_number = {spec.slide_number: spec for spec in slide_specs}
        payload = []
        repaired: Dict[int, Any] = {}
        for slide in slides:
            packet = packet_by_number.get(slide.slide.slide_number)
            evidence = evidence_by_number.get(slide.slide.slide_number) or self._packet_evidence(packet) or self._retrieve_evidence(context, slide)
            deterministic = self._deterministic_content(slide, evidence, packet)
            if deterministic:
                repaired[slide.slide.slide_number] = deterministic
                continue
            spec = spec_by_number.get(slide.slide.slide_number, slide.slide)
            spec_data = asdict(spec)
            if hasattr(spec_data.get("slide_type"), "value"):
                spec_data["slide_type"] = spec_data["slide_type"].value
            payload.append(
                {
                    "slide_number": slide.slide.slide_number,
                    "slide_title": slide.slide.slide_title,
                    "slide_spec": spec_data,
                    "current_content": slide.content,
                    "detected_issues": issues.get(slide.slide.slide_number, []),
                    "required_checks": (packet or {}).get("required_checks", []),
                    "required_facts": (packet or {}).get("required_facts", []),
                    "source_evidence": evidence,
                }
            )
        if not payload:
            return repaired
        prompt = (
            "# ROLE\n"
            "You are a strict lecture content QA editor.\n\n"
            "# TASK\n"
            "Rewrite only the flagged slide contents so each slide is faithful to its source evidence, matches its title, and is ready for oral presentation.\n\n"
            "# EVAL-ALIGNED TARGETS\n"
            "- Each slide should feel focused, substantial, and easy for an audience to follow.\n"
            "- Avoid under-filled slides with too little information unless the visual itself carries the message.\n"
            "- When a table or structured visual is attached, make the bullets explain its key takeaway.\n"
            "- Text and visuals should complement each other rather than feeling unrelated.\n\n"
            "# RULES\n"
            "- Return 4 to 8 concise bullet phrases per slide when the source is rich.\n"
            "- Keep the repaired bullets in the SAME language as the slide title and source evidence. Do not translate.\n"
            "- Keep each bullet slide-friendly: ideally one short sentence or clause, usually no more than about 150 characters.\n"
            "- If a source fact is too dense, split it into shorter bullets instead of keeping one long line.\n"
            "- Remove all placeholders and vague filler.\n"
            "- Stay within the slide title and goal.\n"
            "- Replace generic bullets with source-specific facts, examples, values, actions, or implications.\n"
            "- Preserve concrete formulas, variables, numbers, software names, and interpretation points from the evidence.\n"
            "- Do not invent facts outside the evidence.\n"
            "- Do not copy source markdown headings, page headers, journal headers, or figure labels as bullets.\n"
            "- For slack/result slides, include the numerical result and its meaning when evidence provides it.\n"
            "- For objective slides, include the objective formula when evidence provides it.\n"
            "- For constraint slides, include the constraint inequalities when evidence provides them.\n"
            "- For software slides, name the tool and source workflow actions.\n\n"
            "# FLAGGED SLIDES\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "# OUTPUT\n"
            "Return ONLY valid JSON array:\n"
            '[{"slide_number": 1, "content": ["bullet 1", "bullet 2", "bullet 3"]}]'
        )
        raw = self._chat([{"role": "user", "content": prompt}])
        invoke_fn = lambda msgs: type("R", (), {"content": self._chat(msgs)})()
        data = parse_json_response(raw, invoke_fn, expect_list=True)
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                number = item.get("slide_number")
                if number is None:
                    continue
                repaired[int(number)] = item.get("content", [])
        for slide in slides:
            if slide.slide.slide_number not in repaired:
                packet = packet_by_number.get(slide.slide.slide_number)
                repaired[slide.slide.slide_number] = self._fallback_content(
                    slide,
                    evidence_by_number.get(slide.slide.slide_number) or self._packet_evidence(packet) or self._retrieve_evidence(context, slide),
                    packet,
                )
        return repaired

    def _retrieve_evidence(self, context: DocumentContext, slide: SlideContent) -> str:
        if Config.ABLATION_MODE == 1:
            return ""
        evidence = self._lexical_evidence(context, slide)
        if not evidence.strip():
            evidence = context.text_content.markdown
        return evidence[: self.MAX_EVIDENCE_PER_SLIDE]

    def _lexical_evidence(self, context: DocumentContext, slide: SlideContent) -> str:
        pages = self._split_pages(context.text_content.markdown)
        if not pages:
            return ""
        query = f"{slide.slide.slide_title} {slide.slide.goal}"
        query_l = query.lower()
        terms = self._query_terms(query_l)
        boosters = self._topic_boosters(query_l)
        must_terms = self._must_terms(query_l)
        scored = []
        for page_num, page_text in pages:
            text_l = page_text.lower()
            score = 0
            for term in terms:
                score += text_l.count(term)
            for term, weight in boosters.items():
                if term in text_l:
                    score += weight
            for term in must_terms:
                if term in text_l:
                    score += 15
            if "product mix" in query_l and "product mix" in text_l:
                score += 250
            if any(word in query_l for word in ["formulat", "model"]) and ("$$" in page_text or "\\le" in page_text or "<=" in page_text):
                score += 50
            if "objective" in query_l and re.search(r"\b[A-Za-z]\s*=\s*[^.\n]+", page_text):
                score += 30
            if any(word in query_l for word in ["optimal", "result", "solution value"]):
                if re.search(r"\b[A-Za-z]\s*_?\s*\d*\s*=\s*-?\d+(?:\.\d+)?", page_text):
                    score += 90
                if re.search(r"(maximum|minimum|profit|income)[^.\d$]{0,80}\$?\s*\d+", page_text, flags=re.IGNORECASE):
                    score += 50
            if "slack" in query_l and re.search(r"(slack|not used|unused|remaining)[^.\d]{0,80}\d+", page_text, flags=re.IGNORECASE):
                score += 90
            if score > 0:
                clean = self._normalise_evidence_block(page_text, 2400)
                scored.append((score, page_num, clean))
        if not scored:
            return ""
        scored.sort(key=lambda item: (-item[0], item[1]))
        page_lookup = {page_num: page_text for page_num, page_text in pages}
        selected = []
        for score, page_num, text in scored[:2]:
            selected.append((score, page_num, text))
            if any(word in query_l for word in ["formulat", "model", "problem"]):
                neighbor = page_lookup.get(page_num + 1)
                if neighbor:
                    neighbor_score = score - 1
                    if "$$" in neighbor or "\\le" in neighbor or "<=" in neighbor:
                        neighbor_score = score + 30
                    selected.append((neighbor_score, page_num + 1, self._normalise_evidence_block(neighbor, 2400)))
        selected.sort(key=lambda item: (-item[0], item[1]))
        blocks = []
        seen_pages = set()
        for score, page_num, text in selected[:3]:
            if page_num in seen_pages:
                continue
            seen_pages.add(page_num)
            blocks.append(f"--- Page {page_num} evidence ---\n{self._sentence_safe_truncate(text, 2400)}")
        return "\n\n".join(blocks)

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
    def _normalise_evidence_block(text: str, max_chars: int) -> str:
        text = ContentQualityAgent._clean_evidence_text(text)
        return ContentQualityAgent._sentence_safe_truncate(text, max_chars)

    @staticmethod
    def _sentence_safe_truncate(text: str, max_chars: int) -> str:
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) <= max_chars:
            return text
        boundary = -1
        for match in re.finditer(r"[.!?。！？](?:\s|$)", text[:max_chars]):
            boundary = match.end()
        min_boundary = min(80, max(20, int(max_chars * 0.45)))
        if boundary >= min_boundary:
            return text[:boundary].strip()
        for sep in ["; ", ": ", ", "]:
            pos = text.rfind(sep, 0, max_chars)
            if pos >= min_boundary:
                return text[:pos].strip()
        return text[:max_chars].rsplit(" ", 1)[0].strip()

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        stop = {
            "explain", "preserving", "source", "slide", "concrete", "facts", "title", "goal",
            "with", "from", "that", "this", "into", "using", "method", "solution", "values",
            "linear", "programming",
        }
        terms = []
        for term in re.findall(r"[a-z0-9_]+", query):
            if len(term) >= 4 and term not in stop:
                terms.append(term)
        return terms[:18]

    @staticmethod
    def _topic_boosters(query: str) -> Dict[str, int]:
        boosters: Dict[str, int] = {}
        if "objective" in query:
            boosters.update({"objective": 8, "maximize": 6, "minimize": 6, "profit": 4, "income": 4, " z ": 3})
        if "constraint" in query:
            boosters.update({"<=": 8, "\\le": 8, "constraint": 8, "subject to": 6, "available": 3})
        if "optimal" in query or "result" in query or "solution value" in query:
            boosters.update({"optimal": 8, "maximum": 5, "minimum": 5, "profit": 4, "income": 4, " = ": 3})
        if "slack" in query:
            boosters.update({"slack": 10, "not used": 6, "unused": 6, "remaining": 5, "binding": 4})
        if "software" in query or "prolp" in query:
            boosters.update({"software": 8, "input": 5, "entry data": 6, "solve": 6, "save": 5, "print": 5})
        if "graph" in query or "feasible" in query:
            boosters.update({"feasible region": 10, "straight line": 5, "boundary": 4})
        return boosters

    @staticmethod
    def _must_terms(query: str) -> List[str]:
        if "slack" in query:
            return ["slack", "not used", "unused", "remaining"]
        if "objective" in query:
            return ["objective", "maximize", "minimize"]
        if "constraint" in query:
            return ["subject to", "constraint", "<=", "\\le"]
        if "optimal" in query or "result" in query or "solution value" in query:
            return ["optimal", "maximum", "minimum", "income", "profit"]
        if "software" in query or "prolp" in query:
            return ["software", "input", "solve"]
        return []

    def _detect_issues(self, slides: List[SlideContent]) -> tuple[Dict[int, List[str]], Dict[int, List[str]]]:
        issues: Dict[int, List[str]] = {}
        soft_issues: Dict[int, List[str]] = {}
        for slide in slides:
            text = self._flatten(slide.content)
            blocking = []
            soft = []
            if self._bullet_count(slide.content) < 3:
                blocking.append("fewer than 3 usable bullets")
            if not text.strip():
                blocking.append("empty content")
            lines = [item for item in slide.content if isinstance(item, str)] if isinstance(slide.content, list) else [line for line in str(slide.content).splitlines() if line.strip()]
            if any(self._is_bad_line(line) for line in lines):
                blocking.append("placeholder or unresolved source note")
            lower_text = text.lower()
            if self._has_unbalanced_math(text):
                blocking.append("unbalanced inline math delimiter")
            if self._has_noisy_slack_values(text):
                blocking.append("unsupported slack value list")
            if blocking:
                issues[slide.slide.slide_number] = blocking
            if soft:
                soft_issues[slide.slide.slide_number] = soft
        return issues, soft_issues

    def _detect_all_issues(
        self,
        slides: List[SlideContent],
        context: DocumentContext,
        packet_by_number: Dict[int, Dict[str, Any]] | None = None,
    ) -> tuple[Dict[int, List[str]], Dict[int, str], Dict[int, List[str]]]:
        issues, soft_issues = self._detect_issues(slides)
        evidence_by_number: Dict[int, str] = {}
        packet_by_number = packet_by_number or {}
        for slide in slides:
            packet = packet_by_number.get(slide.slide.slide_number)
            evidence = self._packet_evidence(packet) if packet else self._retrieve_evidence(context, slide)
            evidence_by_number[slide.slide.slide_number] = evidence
            evidence_issues = self._packet_contract_issues(slide, packet, evidence) if packet else self._evidence_required_issues(slide, evidence)
            if evidence_issues:
                issues.setdefault(slide.slide.slide_number, []).extend(evidence_issues)
        return issues, evidence_by_number, soft_issues

    @staticmethod
    def _packet_evidence(packet: Dict[str, Any] | None) -> str:
        if not packet:
            return ""
        pieces = []
        facts = packet.get("required_facts") or []
        if facts:
            pieces.append("Required facts:\n" + "\n".join(str(fact) for fact in facts))
        checks = packet.get("required_checks") or []
        if checks:
            pieces.append("Required checks:\n" + json.dumps(checks, ensure_ascii=False, indent=2))
        evidence = packet.get("evidence")
        if evidence:
            pieces.append(str(evidence))
        return "\n\n".join(pieces)

    def _packet_contract_issues(self, slide: SlideContent, packet: Dict[str, Any] | None, evidence: str) -> List[str]:
        if not packet:
            return self._evidence_required_issues(slide, evidence)
        content_l = self._normalise_for_match(self._flatten(slide.content))
        issues = []
        checks = packet.get("required_checks") or []
        required_facts = [fact for fact in packet.get("required_facts", []) if isinstance(fact, str)]
        if not checks and required_facts:
            checks = [{"kind": "required_facts", "items": required_facts[:6]}]
        if (required_facts or packet.get("coverage_items")) and self._generic_bullet_count(slide.content) >= 2 and not self._is_section_intro(slide):
            issues.append("too many generic bullets")
        if self._focus_clarity_score(slide, packet) < 0.28:
            issues.append("unclear slide focus")
        if self._information_density_score(slide, packet) < 0.34:
            issues.append("low information density")
        if self._visual_integration_score(slide, packet) < 0.25:
            issues.append("visual aid not integrated")
        for check in checks:
            if not isinstance(check, dict):
                continue
            kind = str(check.get("kind") or "").strip().lower()
            if kind == "objective_formula":
                formula = str(check.get("formula") or "").strip()
                if formula and not self._contains_formula(content_l, formula):
                    issues.append("missing required objective formula")
            elif kind == "constraints":
                items = [str(item) for item in check.get("items", []) if str(item).strip()]
                if items and not self._contains_enough_constraints(content_l, items):
                    issues.append("missing required constraint inequalities")
            elif kind == "result_values":
                items = [str(item) for item in check.get("items", []) if str(item).strip()]
                if items and not self._contains_values(content_l, items):
                    issues.append("missing required result values")
            elif kind == "slack_facts":
                values = self._clean_slack_values([str(item) for item in check.get("values", []) if str(item).strip()])
                sentences = [str(item) for item in check.get("sentences", []) if str(item).strip()]
                if values and not self._contains_values(content_l, values):
                    issues.append("missing required slack values")
                if sentences and not any(self._sentence_overlap(content_l, sentence) for sentence in sentences):
                    issues.append("missing required slack interpretation")
            elif kind == "workflow_actions":
                items = [str(item) for item in check.get("items", []) if str(item).strip()]
                if items:
                    action_hits = sum(1 for action in items if any(token in content_l for token in self._action_tokens(action)))
                    if action_hits < min(2, len(items)):
                        issues.append("missing required workflow actions")
            elif kind == "coverage_items":
                continue
            elif kind == "source_coverage":
                items = [str(item) for item in check.get("items", []) if str(item).strip()]
                min_hits = int(check.get("min_hits") or min(3, len(items)))
                hits = sum(1 for fact in items if self._fact_supported_in_content(content_l, fact))
                if items and hits < min(min_hits, len(items)):
                    issues.append("low source coverage")
            elif kind == "role_anchors":
                items = [str(item).lower() for item in check.get("items", []) if str(item).strip()]
                min_hits = int(check.get("min_hits") or min(2, len(items)))
                hits = sum(1 for item in items if item in content_l)
                if items and hits < min(min_hits, len(items)):
                    issues.append("missing required role anchors")
            elif kind == "required_facts":
                items = [str(item) for item in check.get("items", []) if str(item).strip()]
                fact_hits = sum(1 for fact in items if self._fact_supported_in_content(content_l, fact))
                if fact_hits < min(2, len(items)):
                    issues.append("missing required packet facts")
        purity = self._topic_purity_score(slide, packet)
        if purity < 0.22:
            issues.append("low topic purity")
        return list(dict.fromkeys(issues))

    @staticmethod
    def _blocking_only(unresolved: Dict[int, List[str]]) -> Dict[int, List[str]]:
        advisory = {
            "fewer than 3 usable bullets",
            "missing required coverage items",
            "missing required role anchors",
            "missing required packet facts",
            "too many generic bullets",
            "low source coverage",
            "low topic purity",
            "low information density",
            "visual aid not integrated",
            "unclear slide focus",
        }
        blocking: Dict[int, List[str]] = {}
        for slide_number, issue_list in (unresolved or {}).items():
            kept = [issue for issue in issue_list if issue not in advisory]
            if kept:
                blocking[slide_number] = kept
        return blocking

    @staticmethod
    def _advisory_only(unresolved: Dict[int, List[str]]) -> Dict[int, List[str]]:
        advisory = {
            "fewer than 3 usable bullets",
            "missing required coverage items",
            "missing required role anchors",
            "missing required packet facts",
            "too many generic bullets",
            "low source coverage",
            "low topic purity",
            "low information density",
            "visual aid not integrated",
            "unclear slide focus",
        }
        kept: Dict[int, List[str]] = {}
        for slide_number, issue_list in (unresolved or {}).items():
            items = [issue for issue in issue_list if issue in advisory]
            if items:
                kept[slide_number] = items
        return kept

    def _evidence_required_issues(self, slide: SlideContent, evidence: str) -> List[str]:
        title_l = slide.slide.slide_title.lower()
        content_l = self._normalise_for_match(self._flatten(slide.content))
        evidence_l = self._normalise_for_match(evidence)
        issues = []
        evidence_objective = self._extract_objective_formula(evidence)
        if "objective" in title_l and evidence_objective:
            if not self._contains_formula(content_l, evidence_objective):
                issues.append("missing source objective formula")
        evidence_constraints = self._extract_constraints(evidence)
        if "constraint" in title_l and evidence_constraints:
            if not self._contains_enough_constraints(content_l, evidence_constraints):
                issues.append("missing source constraint inequalities")
        evidence_values = self._extract_result_values(evidence)
        if ("optimal" in title_l or "result" in title_l or "solution value" in title_l) and evidence_values:
            if not self._contains_values(content_l, evidence_values):
                issues.append("missing source optimal values")
        evidence_slack = self._extract_slack_facts(evidence)
        if ("slack" in title_l or "binding" in title_l) and evidence_slack:
            if not self._contains_values(content_l, evidence_slack["values"]) or "slack" not in content_l:
                issues.append("missing slack value and interpretation")
        evidence_actions = self._extract_software_actions(evidence)
        required_actions = self._extract_required_workflow_actions(evidence)
        if required_actions:
            evidence_actions = required_actions
        if ("software" in title_l or "prolp" in title_l or "input" in title_l or "saving" in title_l or required_actions) and evidence_actions:
            action_hits = sum(1 for action in evidence_actions if any(token in content_l for token in self._action_tokens(action)))
            if action_hits < min(2, len(evidence_actions)):
                issues.append("missing software workflow actions")
        return issues

    def _topic_purity_score(self, slide: SlideContent, packet: Dict[str, Any] | None) -> float:
        title_terms = set(self._query_terms(slide.slide.slide_title.lower()))
        content_terms = set(self._query_terms(self._flatten(slide.content).lower()))
        if not title_terms or not content_terms:
            return 1.0
        overlap = len(title_terms & content_terms) / max(1, min(len(title_terms), len(content_terms)))
        packet_score = 0.0
        if packet and packet.get("source_alignment", {}).get("topic_purity") is not None:
            try:
                packet_score = float(packet["source_alignment"]["topic_purity"])
            except (TypeError, ValueError):
                packet_score = 0.0
        return max(overlap, packet_score)

    def _quality_metrics(self, slides: List[SlideContent], packet_by_number: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
        topic_scores: List[float] = []
        coverage_scores: List[float] = []
        focus_scores: List[float] = []
        density_scores: List[float] = []
        visual_scores: List[float] = []
        for slide in slides:
            packet = packet_by_number.get(slide.slide.slide_number)
            topic_scores.append(self._topic_purity_score(slide, packet))
            coverage_scores.append(self._source_coverage_score(slide, packet))
            focus_scores.append(self._focus_clarity_score(slide, packet))
            density_scores.append(self._information_density_score(slide, packet))
            visual_scores.append(self._visual_integration_score(slide, packet))
        return {
            "topic_purity_mean": round(sum(topic_scores) / max(1, len(topic_scores)), 3),
            "source_coverage_mean": round(sum(coverage_scores) / max(1, len(coverage_scores)), 3),
            "focus_clarity_mean": round(sum(focus_scores) / max(1, len(focus_scores)), 3),
            "information_density_mean": round(sum(density_scores) / max(1, len(density_scores)), 3),
            "visual_integration_mean": round(sum(visual_scores) / max(1, len(visual_scores)), 3),
        }

    def _focus_clarity_score(self, slide: SlideContent, packet: Dict[str, Any] | None) -> float:
        title_terms = set(self._query_terms(slide.slide.slide_title.lower()))
        content_terms = set(self._query_terms(self._flatten(slide.content).lower()))
        if not title_terms or not content_terms:
            return 1.0
        overlap = len(title_terms & content_terms) / max(1, min(len(title_terms), len(content_terms)))
        generic_penalty = 0.18 if self._generic_bullet_count(slide.content) >= 2 else 0.0
        try:
            packet_score = float(packet.get("source_alignment", {}).get("topic_purity", 0.0)) if packet else 0.0
        except (TypeError, ValueError):
            packet_score = 0.0
        return max(0.0, min(1.0, max(overlap, packet_score) - generic_penalty))

    def _information_density_score(self, slide: SlideContent, packet: Dict[str, Any] | None) -> float:
        bullet_count = self._bullet_count(slide.content)
        text = self._flatten(slide.content)
        char_count = len(re.sub(r"\s+", " ", text).strip())
        has_structured_visual = bool(packet and (packet.get("table") or packet.get("latex_block_formula")))
        target_bullets = 3 if has_structured_visual else 4
        bullet_score = min(1.0, bullet_count / max(1, target_bullets))
        char_target = 120 if has_structured_visual else 180
        char_score = min(1.0, char_count / max(1, char_target))
        return max(0.0, min(1.0, (bullet_score * 0.6) + (char_score * 0.4)))

    def _visual_integration_score(self, slide: SlideContent, packet: Dict[str, Any] | None) -> float:
        if not packet:
            return 1.0
        table = packet.get("table") or {}
        has_formula = bool(packet.get("latex_block_formula"))
        if not table and not has_formula:
            return 1.0
        raw_content = self._flatten(slide.content)
        content_l = self._normalise_for_match(raw_content)
        if not content_l:
            return 0.0
        if has_formula:
            return 1.0 if ("$" in raw_content or "=" in raw_content or re.search(r"\d", raw_content)) else 0.0
        caption_terms = set(self._query_terms(str(table.get("table_caption") or slide.slide.slide_title).lower()))
        content_terms = set(self._query_terms(content_l))
        overlap = len(caption_terms & content_terms) / max(1, min(len(caption_terms), len(content_terms))) if caption_terms and content_terms else 0.0
        if re.search(r"\d", raw_content):
            overlap = max(overlap, 0.45)
        return max(0.0, min(1.0, overlap))

    def _source_coverage_score(self, slide: SlideContent, packet: Dict[str, Any] | None) -> float:
        if not packet:
            return 1.0
        required = [fact for fact in packet.get("required_facts", []) if isinstance(fact, str)]
        if not required:
            return 1.0
        content_l = self._normalise_for_match(self._flatten(slide.content))
        hits = sum(1 for fact in required[:6] if self._fact_supported_in_content(content_l, fact))
        return hits / max(1, min(3, len(required[:6])))

    @staticmethod
    def _normalise_for_match(text: str) -> str:
        text = text.lower()
        text = text.replace("≤", "<=").replace("\\leq", "<=").replace("\\le", "<=")
        text = re.sub(r"\s+", " ", text)
        text = text.replace("_", "")
        return text

    @classmethod
    def _extract_software_actions(cls, evidence: str) -> List[str]:
        actions = []
        chunks = []
        action_pattern = re.compile(
            r"\b(click|input|enter|solve|save|print|select|choose|upload|download|export|import|run|execute|submit|cancel|entry)\b",
            flags=re.IGNORECASE,
        )
        for line in cls._clean_evidence_text(evidence).splitlines():
            chunks.extend(re.split(r"(?<=[.!?])\s+", line))
        for line in chunks:
            clean = line.strip().lstrip("-*•").strip()
            if action_pattern.search(clean):
                clean = re.sub(r"\s+", " ", clean)
                if len(clean) > 140:
                    for part in re.split(r"\b(?:to|and)\b", clean, flags=re.IGNORECASE):
                        part = part.strip(" .;:")
                        if 5 <= len(part) <= 140 and action_pattern.search(part):
                            actions.append(part)
                elif 5 <= len(clean):
                    actions.append(clean)
        return actions[:6]

    @staticmethod
    def _action_tokens(action: str) -> List[str]:
        return [token for token in re.findall(r"[a-z]+", action.lower()) if len(token) >= 4]

    @staticmethod
    def _extract_formulas(text: str) -> List[str]:
        text = ContentQualityAgent._clean_evidence_text(text)
        formulas = re.findall(r"\$\$(.*?)\$\$", text, flags=re.DOTALL)
        formulas.extend(re.findall(r"\$([^$]+)\$", text))
        return [re.sub(r"\s+", " ", formula).strip() for formula in formulas if formula.strip()]

    @staticmethod
    def _clean_evidence_text(text: str) -> str:
        text = re.sub(r"--- Page\s+\d+\s+evidence ---", " ", text)
        text = re.sub(r"\[BLOCK\s+\d+\s*-\s*TEXT\]", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"#\s*Dagon University Research Journal\s+\d{4},\s*Vol\.\s*\d+\s*\d*", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
        text = re.sub(r"\*Figure:[^*]+\*", " ", text, flags=re.IGNORECASE)
        text = re.sub(r"\*\*\s*Key\s+words?\s*:?\s*\*\*.*?(?=\*\*\s*(?:Target\s+audience|References?|Figure|Table)\s*:?\s*\*\*|\n\s*\n|$)", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\*\*\s*Target\s+audience\s*:?\s*\*\*.*?(?=\*\*\s*(?:Key\s+words?|References?|Figure|Table)\s*:?\s*\*\*|\n\s*\n|$)", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\bKey\s+words?\s*:?\s*\*\*?.*?(?=\bTarget\s+audience\s*:?\s*\*\*?|\n\s*\n|$)", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\bTarget\s+audience\s*:?\s*\*\*?.*?(?=\bKey\s+words?\s*:?\s*\*\*?|\n\s*\n|$)", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\bCite\s+this\s+document\s+as\s*:.*?(?=\n\s*\n|$)", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\bLast\s+retrieved\s*:.*?(?=\n\s*\n|$)", " ", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\bReferences?\s*$", " ", text, flags=re.IGNORECASE | re.MULTILINE)
        text = re.sub(r"#{1,6}\s*", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _contains_formula(cls, content_l: str, formula: str) -> bool:
        tokens = re.findall(r"[a-z]\d*|\d+(?:\.\d+)?", cls._normalise_for_match(formula))
        important = [token for token in tokens if len(token) > 1 or token.isdigit()]
        if not important:
            return False
        hits = sum(1 for token in important if token in content_l)
        return hits >= max(2, min(len(important), 4))

    @classmethod
    def _contains_enough_constraints(cls, content_l: str, constraints: List[str]) -> bool:
        if "<=" not in content_l and ">=" not in content_l and "less than or equal" not in content_l and "greater than or equal" not in content_l:
            return False
        required = min(2, len(constraints))
        hits = sum(1 for constraint in constraints if cls._contains_formula(content_l, constraint))
        return hits >= required

    @staticmethod
    def _contains_values(content_l: str, values: List[str]) -> bool:
        checks = []
        for value in values:
            variable_match = re.search(r"([A-Za-z]\d*)\s*=\s*(-?\d+(?:\.\d+)?)", value)
            if variable_match:
                checks.append((variable_match.group(1).lower(), variable_match.group(2)))
            else:
                numeric = re.sub(r"[^0-9.\-]", "", value)
                if numeric:
                    number_match = re.search(r"-?\d+(?:\.\d+)?", value)
                    unit_match = re.search(r"(ft\^?2|lb|hours?|units?|%)", value, flags=re.IGNORECASE)
                    checks.append(("", numeric, number_match.group(0) if number_match else numeric, unit_match.group(1).lower() if unit_match else ""))
        if not checks:
            return False
        hits = 0
        for check in checks:
            variable = check[0]
            value = check[1]
            if variable:
                pattern = rf"{re.escape(variable)}\s*=\s*{re.escape(value)}"
                if re.search(pattern, content_l):
                    hits += 1
            else:
                display_number = check[2] if len(check) > 2 else value
                unit = check[3] if len(check) > 3 else ""
                if value in content_l or display_number in content_l:
                    if not unit or unit.replace("^", "") in content_l.replace("^", ""):
                        hits += 1
        return hits >= min(2, len(checks))

    @classmethod
    def _fact_supported_in_content(cls, content_l: str, fact: str) -> bool:
        fact_l = cls._normalise_for_match(fact)
        if "=" in fact_l and any(op in fact_l for op in ["<=", ">="]):
            return cls._contains_formula(content_l, fact)
        variable_match = re.search(r"([a-z]\d*)\s*=\s*(-?\d+(?:\.\d+)?)", fact_l)
        if variable_match:
            return bool(re.search(rf"{re.escape(variable_match.group(1))}\s*=\s*{re.escape(variable_match.group(2))}", content_l))
        numeric = re.findall(r"\d+(?:\.\d+)?", fact_l)
        keywords = [token for token in re.findall(r"[a-z]+", fact_l) if len(token) >= 4]
        numeric_hit = any(value in content_l for value in numeric) if numeric else False
        keyword_hit = any(token in content_l for token in keywords) if keywords else False
        return numeric_hit or keyword_hit

    @classmethod
    def _sentence_overlap(cls, content_l: str, sentence: str) -> bool:
        tokens = [token for token in re.findall(r"[a-z]+|\d+(?:\.\d+)?", cls._normalise_for_match(sentence)) if len(token) >= 4 or token.isdigit()]
        if not tokens:
            return False
        hits = sum(1 for token in tokens if token in content_l)
        return hits >= min(2, len(tokens))

    @classmethod
    def _coverage_items_supported(cls, slide: SlideContent, content_l: str, items: List[str], min_hits: int) -> bool:
        hits = sum(1 for item in items if cls._fact_supported_in_content(content_l, item) or cls._sentence_overlap(content_l, item))
        if hits >= min(min_hits, len(items)):
            return True
        if cls._cross_script_coverage(items, content_l):
            bullet_count = cls._bullet_count(slide.content)
            generic_count = cls._generic_bullet_count(slide.content)
            return bullet_count >= 3 and generic_count <= 1
        return False

    @staticmethod
    def _cross_script_coverage(items: List[str], content_l: str) -> bool:
        item_text = " ".join(items)
        item_cjk = len(re.findall(r"[\u4e00-\u9fff]", item_text))
        content_cjk = len(re.findall(r"[\u4e00-\u9fff]", content_l))
        item_latin = len(re.findall(r"[a-zA-Z]{3,}", item_text))
        content_latin = len(re.findall(r"[a-zA-Z]{3,}", content_l))
        if item_cjk >= 8 and content_latin >= 8 and content_cjk == 0:
            return True
        if item_latin >= 8 and content_cjk >= 8 and content_latin == 0:
            return True
        return False

    @staticmethod
    def _sentences_matching(text: str, keywords: List[str]) -> List[str]:
        sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
        result = []
        for sentence in sentences:
            clean = re.sub(r"\s+", " ", sentence).strip()
            lower = clean.lower()
            if clean and any(keyword in lower for keyword in keywords):
                result.append(clean)
        return result[:6]

    @classmethod
    def _normalise_content(cls, content: Any, fallback: Any) -> Any:
        if isinstance(content, list):
            items = []
            for item in content:
                if not isinstance(item, str):
                    continue
                for line in item.splitlines():
                    line = cls._clean_bullet(line)
                    if line and not cls._is_bad_line(line):
                        items.append(line)
            items = cls._compact_slide_bullets(cls._dedupe_bullets(items))
            items = cls._filter_script_mismatch(items, fallback if isinstance(fallback, str) else "")
            if len(items) >= 3:
                return items[:5]
        fallback_items = cls._fallback_items(fallback)
        fallback_seed = fallback if isinstance(fallback, str) and fallback.strip() else ""
        return cls._compact_slide_bullets(cls._ensure_min_bullets(fallback_items, fallback_seed))[:5]

    @classmethod
    def _filter_script_mismatch(cls, items: List[str], reference_text: str) -> List[str]:
        ref_script = dominant_script(reference_text)
        if ref_script in {"unknown", "latin"}:
            return items
        if len(items) <= 1:
            return items
        aligned = [item for item in items if dominant_script(item) == ref_script]
        return aligned if len(aligned) >= 2 else items

    @staticmethod
    def _compact_slide_bullets(items: List[str], max_chars: int = 150) -> List[str]:
        compacted: List[str] = []
        for item in items:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            if len(text) <= max_chars:
                compacted.append(text)
                continue
            parts = re.split(
                r"(?<=[.;!?])\s+|\s+[—-]\s+|;\s+|:\s+|,\s+(?=[A-ZÀ-Ỹ0-9])|\s*\((?=[^)]{12,}\))",
                text,
            )
            kept = False
            for part in parts:
                part = re.sub(r"\s+", " ", part).strip(" ;,-()")
                if 16 <= len(part) <= max_chars:
                    compacted.append(part)
                    kept = True
                if len(compacted) >= 10:
                    break
            if kept:
                continue
            compacted.append(text)
        return ContentQualityAgent._merge_fragments(compacted)

    @staticmethod
    def _merge_fragments(items: List[str]) -> List[str]:
        merged: List[str] = []
        for item in items:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            fragment = bool(re.match(r"^(?:and|or|but|with|while|including|such as|each|32gb|16gb|250gb|40gb)\b", text, flags=re.IGNORECASE))
            if merged and (fragment or (len(text.split()) <= 4 and re.search(r"\d|gb|mb|cores?|ram|disk", text, flags=re.IGNORECASE))):
                merged[-1] = f"{merged[-1].rstrip(' .;:,')}, {text.lstrip(' ,;:.')}"
                continue
            merged.append(text)
        return merged

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

    @classmethod
    def _fallback_content(cls, slide: SlideContent, evidence: str, packet: Dict[str, Any] | None = None) -> List[str]:
        deterministic = cls._deterministic_content(slide, evidence, packet)
        if deterministic:
            return deterministic
        candidates = cls._fallback_items(evidence)
        title_terms = [t for t in re.findall(r"[A-Za-z0-9]+", slide.slide.slide_title.lower()) if len(t) > 3]
        ranked = []
        for item in candidates:
            if cls._is_bad_line(item):
                continue
            score = sum(1 for term in title_terms if term in item.lower())
            ranked.append((score, item))
        ranked.sort(key=lambda pair: (-pair[0], len(pair[1])))
        bullets = [item for _, item in ranked[:5]]
        return cls._drop_title_echoes(bullets, slide.slide.slide_title)[:5]

    @staticmethod
    def _drop_title_echoes(items: List[str], slide_title: str) -> List[str]:
        clean_title = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(slide_title or "")).strip().lower()
        title_terms = {
            term for term in re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", clean_title)
            if term not in {"overview", "summary", "content", "section"}
        }
        result = []
        for item in items:
            clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(item or "")).strip()
            lower = clean.lower().strip(".")
            if clean_title and lower == clean_title:
                continue
            item_terms = set(re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", lower))
            if title_terms and item_terms and item_terms <= title_terms and len(item_terms) >= 3:
                continue
            result.append(item)
        return result or items

    @classmethod
    def _apply_deterministic_fallbacks(
        cls,
        slides: List[SlideContent],
        unresolved: Dict[int, List[str]],
        evidence_by_number: Dict[int, str],
        packet_by_number: Dict[int, Dict[str, Any]] | None = None,
    ) -> List[SlideContent]:
        repaired = []
        unresolved_numbers = set(unresolved)
        packet_by_number = packet_by_number or {}
        for slide in slides:
            if slide.slide.slide_number in unresolved_numbers:
                content = cls._deterministic_content(
                    slide,
                    evidence_by_number.get(slide.slide.slide_number, ""),
                    packet_by_number.get(slide.slide.slide_number),
                )
                if content:
                    repaired.append(SlideContent(slide=slide.slide, content=content, layout_hint=slide.layout_hint))
                    continue
            repaired.append(slide)
        return repaired

    @classmethod
    def _deterministic_content(cls, slide: SlideContent, evidence: str, packet: Dict[str, Any] | None = None) -> List[str]:
        packet_checks = (packet or {}).get("required_checks", [])
        packet_content = cls._deterministic_from_packet_checks(packet_checks, evidence)
        if packet_content:
            return packet_content

        actions = cls._extract_software_actions(evidence)
        action_check = next((check for check in packet_checks if check.get("kind") == "workflow_actions"), None)
        required_actions = [str(item) for item in action_check.get("items", []) if str(item).strip()] if action_check else cls._extract_required_workflow_actions(evidence)
        if required_actions:
            actions = required_actions + [action for action in actions if action not in required_actions]
        if actions:
            bullets = [cls._trim_bullet(action) for action in actions[:5]]
            return cls._pad_bullets(bullets, "Workflow actions are derived from the source procedure")
            
        if packet and packet.get("required_facts"):
            title_seed = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", slide.slide.slide_title).strip()
            return cls._pad_bullets([str(item) for item in packet.get("required_facts", [])[:5]], title_seed[:90] or "Source-backed summary")
            
        return []

    @classmethod
    def _deterministic_from_packet_checks(cls, packet_checks: List[Dict[str, Any]], evidence: str) -> List[str]:
        if not packet_checks:
            return []
        check = packet_checks[0]
        kind = str(check.get("kind") or "").strip().lower()
        
        if kind in ["exact_formulas", "objective_formula", "constraints"]:
            items = []
            if check.get("formula"):
                items.append(str(check.get("formula")))
            items.extend([str(item) for item in check.get("items", []) if str(item).strip()])
            if items:
                bullets = [f"Formula: ${item}$" for item in items[:4]]
                return cls._pad_bullets(bullets, "Mathematical relationships extracted")

        if kind in ["key_values", "result_values", "slack_facts"]:
            values = [str(item) for item in check.get("items", []) if str(item).strip()]
            if not values and "values" in check:
                values = [str(item) for item in check.get("values", []) if str(item).strip()]
            sentences = [str(item) for item in check.get("sentences", []) if str(item).strip()]
            bullets = [cls._trim_bullet(s) for s in sentences[:2]]
            if values:
                bullets.extend([f"Key value: {v}" for v in values[:3]])
            if bullets:
                return cls._pad_bullets(bullets, "Numerical values guide decision-making")

        if kind == "workflow_actions":
            actions = [str(item) for item in check.get("items", []) if str(item).strip()]
            if actions:
                return cls._pad_bullets([cls._trim_bullet(action) for action in actions[:5]], "Workflow actions are derived from the source procedure")
        if kind == "coverage_items":
            items = [str(item) for item in check.get("items", []) if str(item).strip()]
            if items:
                return cls._pad_bullets([cls._trim_bullet(item) for item in items[:5]], "The listed items come from source evidence")
        if kind == "role_anchors":
            items = [str(item) for item in check.get("items", []) if str(item).strip()]
            if items:
                bullets = []
                for sentence in cls._fallback_items(evidence):
                    lower = sentence.lower()
                    if any(item.lower() in lower for item in items):
                        bullets.append(sentence)
                return cls._pad_bullets(bullets[:5], "Role-specific source facts anchor this slide")
        return []

    @staticmethod
    def _extract_required_workflow_actions(evidence: str) -> List[str]:
        actions = []
        normalized = re.sub(r"\s+", " ", evidence)
        for match in re.findall(r"Workflow action:\s*(.*?)(?=Workflow action:|$)", normalized, flags=re.IGNORECASE):
            action = match.strip(" .;:-")
            if action:
                actions.append(action)
        if actions:
            return actions[:6]
        for line in evidence.splitlines():
            clean = line.strip()
            if clean.lower().startswith("workflow action:"):
                action = clean.split(":", 1)[1].strip()
                if action:
                    actions.append(action)
        return actions[:6]

    @staticmethod
    def _objective_direction(evidence: str) -> str:
        lower = evidence.lower()
        if "maximize" in lower or "maximise" in lower:
            return "Direction: maximize objective value"
        if "minimize" in lower or "minimise" in lower:
            return "Direction: minimize objective value"
        return ""

    @staticmethod
    def _variable_bullets(evidence: str) -> List[str]:
        bullets = []
        for match in re.findall(r"([A-Za-z]\s*_?\s*\d+)\s*[,=:]\s*([^.\n;]{3,80})", evidence):
            var = re.sub(r"\s+", "", match[0])
            desc = re.sub(r"\s+", " ", match[1]).strip()
            if desc:
                bullets.append(f"${var}$: {desc}")
        return bullets[:2]

    @staticmethod
    def _pad_bullets(bullets: List[str], fallback: str) -> List[str]:
        clean = []
        for bullet in bullets:
            bullet = ContentQualityAgent._trim_bullet(bullet)
            if bullet and bullet not in clean and not ContentQualityAgent._is_bad_line(bullet):
                clean.append(bullet)
        return ContentQualityAgent._ensure_min_bullets(clean, fallback)[:5]

    @staticmethod
    def _trim_bullet(text: str) -> str:
        text = ContentQualityAgent._clean_bullet(text)
        text = re.sub(r"\s+", " ", text).strip(" .;:-")
        return ContentQualityAgent._sentence_safe_truncate(text, 160)

    @staticmethod
    def _fallback_items(value: Any) -> List[str]:
        text = ContentQualityAgent._flatten(value)
        parts = re.split(r"(?<=[.!?])\s+|\n+", text)
        items = []
        for part in parts:
            item = part.strip().lstrip("-*•").strip()
            item = ContentQualityAgent._clean_bullet(item)
            item = ContentQualityAgent._sentence_safe_truncate(item, 160)
            token_count = len(re.findall(r"[A-Za-zÀ-ỹ0-9]{2,}", item))
            cjk_count = len(re.findall(r"[\u4e00-\u9fff]", item))
            if 8 <= len(item) <= 160 and (token_count >= 2 or cjk_count >= 4) and not ContentQualityAgent._is_bad_line(item):
                items.append(item)
        items = ContentQualityAgent._dedupe_bullets(items)
        if items:
            return items
        clean_text = ContentQualityAgent._sentence_safe_truncate(ContentQualityAgent._clean_evidence_text(text), 160)
        if clean_text and not ContentQualityAgent._is_bad_line(clean_text):
            return [clean_text]
        return []

    @classmethod
    def _is_bad_line(cls, line: str) -> bool:
        raw = line.strip()
        lower = raw.lower()
        if not lower:
            return True
        if "**" in line:
            return True
        if re.match(r"^(given|once|when|where|while|their|be)\b", lower) and len(raw.split()) <= 8:
            return True
        if re.match(r"^(which|and|or|but|while|through|leading|resulting|providing|financially|supported by|accompanied by)\b", lower):
            return True
        if re.fullmatch(r"\d{1,3}\.?", lower):
            return True
        if re.match(r"^\d+(?:\.\d+)+\.?\s+", raw):  # 1.2. or 3.5.3.1. headings (any depth)
            return True
        if re.match(
            r"^(chương|mục|phần|tiết|bài|bảng\s+\d|hình\s+\d"
            r"|chapter|section|part|appendix|annex"
            r"|kapitel|abschnitt|abbildung"
            r"|chapitre|partie|tableau|figure"
            r"|cap[ií]tulo|secci[oó]n|parte|figura"
            r"|sezione|capitolo|figura|tabella"
            r"|hoofdstuk|sectie|deel"
            r"|rozdzia[lł]|sekcja"
            r"|第\s*\d|図\s*\d|表\s*\d"
            r"|제\s*\d|절\s*\d"
            r"|бөлім|раздел|глава"
            r")\s+\d+\b",
            lower,
        ):
            return True
        if re.fullmatch(r"(berlin,\s*)?den\s+\d{1,2}\.?", lower):
            return True
        if re.fullmatch(r"[a-zäöüß]+,\s*den\s+\d{1,2}\.?", lower):
            return True
        if "![ " in lower or lower.startswith("![") or re.search(r"\.(jpeg|jpg|png|webp)\)", lower):
            return True
        if re.search(r"\*figure:|figure:", lower):
            return True
        if re.search(r"\b(key\s*words?|target\s+audience|cite\s+this\s+document\s+as|last\s+retrieved)\b", lower):
            return True
        if re.fullmatch(r'[A-ZÀ-Ỹ][a-zA-ZÀ-ỹ]+(?:\s+[A-ZÀ-Ỹ][a-zA-ZÀ-ỹ]+){0,4}\s*\*?', raw) and len(raw.split()) <= 5:
            return True
        if re.match(r"^(figure|fig\.?|table|references?)\s*[:\d]", lower):
            return True
        if lower.startswith("#"):
            return True
        if cls._has_noisy_slack_values(line):
            return True
        return any(re.search(pattern, lower) for pattern in cls.BAD_PATTERNS)

    @classmethod
    def _ensure_min_bullets(cls, items: List[str], fallback: str) -> List[str]:
        clean = cls._dedupe_bullets([item for item in items if item and not cls._is_bad_line(item)])
        if len(clean) < 2:
            fallback = cls._trim_bullet(fallback)
            if fallback and not cls._is_bad_line(fallback):
                clean = cls._dedupe_bullets(clean + [fallback])
        return clean[:5]

    @classmethod
    def _dedupe_bullets(cls, items: List[str]) -> List[str]:
        result = []
        seen = set()
        for item in items:
            clean = re.sub(r"\s+", " ", str(item or "")).strip()
            if not clean:
                continue
            key = cls._bullet_key(clean)
            if key in seen:
                continue
            if any(cls._bullet_overlap(clean, existing) >= 0.72 for existing in result):
                continue
            seen.add(key)
            result.append(clean)
        return result

    @staticmethod
    def _bullet_key(text: str) -> str:
        tokens = [token for token in re.findall(r"[a-zA-ZÀ-ỹ0-9]{4,}", text.lower()) if token not in {"through", "using", "which", "with", "from", "this", "that", "into"}]
        if tokens:
            return " ".join(tokens[:10])
        cjk = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        return cjk[:24] or re.sub(r"\s+", " ", text.lower())

    @staticmethod
    def _bullet_overlap(left: str, right: str) -> float:
        left_tokens = set(re.findall(r"[a-zA-ZÀ-ỹ0-9]{4,}", left.lower()))
        right_tokens = set(re.findall(r"[a-zA-ZÀ-ỹ0-9]{4,}", right.lower()))
        if left_tokens and right_tokens:
            return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))
        left_cjk = set(re.findall(r"[\u4e00-\u9fff]", left))
        right_cjk = set(re.findall(r"[\u4e00-\u9fff]", right))
        return len(left_cjk & right_cjk) / min(len(left_cjk), len(right_cjk)) if left_cjk and right_cjk else 0.0

    @classmethod
    def _is_generic_bullet(cls, line: str) -> bool:
        lower = cls._normalise_for_match(line)
        if len(lower.split()) < 5:
            return False
        specific_markers = [
            r"\d",
            r"\$",
            r"\b[A-Za-z]\d+\b",
            r"\b[A-Z][A-Za-z]+(?:[A-Z][A-Za-z]+)+\b",
            r"\b[A-Z]{2,}\b",
        ]
        if any(re.search(pattern, line) for pattern in specific_markers):
            return False
        generic_patterns = [
            r"\bis important\b",
            r"\bplays? an? important role\b",
            r"\bhelps? (to )?\w+",
            r"\bprovides? (a )?(clear )?(overview|framework|foundation)\b",
            r"\bfocuses? on\b",
            r"\brelates? to\b",
            r"\bcan be used\b",
            r"\bis used to\b",
            r"\bsupports? .{0,30}(practice|process|decision|understanding)\b",
            r"\bhighlights? (the )?(importance|benefits|key)\b",
        ]
        return any(re.search(pattern, lower) for pattern in generic_patterns)

    @classmethod
    def _generic_bullet_count(cls, content: Any) -> int:
        if isinstance(content, list):
            return sum(1 for item in content if isinstance(item, str) and cls._is_generic_bullet(item))
        if isinstance(content, str):
            return sum(1 for line in content.splitlines() if cls._is_generic_bullet(line))
        return 0

    @staticmethod
    def _is_section_intro(slide: SlideContent) -> bool:
        title = slide.slide.slide_title.strip().lower()
        goal = slide.slide.goal.strip().lower()
        scope = f"{title} {goal}"
        return bool(re.match(r"^\d+\.\s+", title)) and not re.match(r"^\d+\.\d+", title) and not any(
            term in scope for term in ["result", "constraint", "objective", "slack", "workflow", "software", "formula"]
        )

    @staticmethod
    def _clean_bullet(line: str) -> str:
        line = line.strip().lstrip("-*•").strip()
        line = re.sub(r"^(?:principle|step|item|guideline|recommendation)\s+\d+\s*:\s*", "", line, flags=re.IGNORECASE)
        line = ContentQualityAgent._normalise_currency_text(line)
        line = ContentQualityAgent._sanitize_math_delimiters(line)
        # Bullets are inline context — convert display math to inline math so KaTeX doesn't
        # render centered block equations inside <li> elements
        line = re.sub(r'\$\$(.+?)\$\$', lambda m: f'${m.group(1).strip()}$', line, flags=re.DOTALL)
        line = re.sub(r'\\\[(.+?)\\\]', lambda m: f'${m.group(1).strip()}$', line, flags=re.DOTALL)
        line = re.sub(r"\s+", " ", line).strip()
        if line and line[0].islower():
            line = line[0].upper() + line[1:]
        return line

    @staticmethod
    def _has_unbalanced_math(text: str) -> bool:
        return ContentQualityAgent._sanitize_math_delimiters(text).count("$") % 2 == 1

    @staticmethod
    def _normalise_currency_text(text: str) -> str:
        return re.sub(
            r"(?<![A-Za-z0-9_])(?:US\$|\$)\s*(\d+(?:[.,]\d+)?(?:\s*(?:k|m|bn|million|billion|thousand|%))?)(?![A-Za-z0-9_\\])",
            lambda match: f"USD {match.group(1).strip()}",
            text,
            flags=re.IGNORECASE,
        )

    @staticmethod
    def _sanitize_math_delimiters(text: str) -> str:
        if "$" not in text:
            return text
        text = re.sub(r"(?<!\S)\$(?=\s|$)", "", text)
        text = re.sub(r"(?<=\s)\$(?!\S)", "", text)
        if text.count("$") % 2 == 0:
            return text
        text = re.sub(r"\$(?!.*\$)", "", text, count=1)
        if text.count("$") % 2 == 0:
            return text
        return re.sub(r"\$", "", text, count=1)

    @staticmethod
    def _has_noisy_slack_values(text: str) -> bool:
        for line in text.splitlines():
            lower = line.lower()
            if re.search(r"slack value(?:s)?\s+(?:include|are|is|:)", lower):
                numbers = re.findall(r"\d+(?:\.\d+)?", line)
                if len(numbers) > 1:
                    return True
        return False

    @staticmethod
    def _clean_slack_values(values: List[str]) -> List[str]:
        clean_values = []
        unit_values = []
        for value in values:
            clean = re.sub(r"\s+", " ", str(value).replace("$", "")).strip(" ,.;:")
            if not clean:
                continue
            if re.search(r"(ft\^?2|lb|hours?|units?|%)", clean, flags=re.IGNORECASE):
                unit_values.append(clean)
            clean_values.append(clean)
        selected = unit_values or clean_values[:1]
        result = []
        for value in selected:
            if value not in result:
                result.append(value)
        return result[:2]

    @staticmethod
    def _flatten(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(ContentQualityAgent._flatten(item) for item in value)
        if isinstance(value, dict):
            chunks = []
            for key, val in value.items():
                chunks.append(str(key))
                chunks.append(ContentQualityAgent._flatten(val))
            return " ".join(chunks)
        return str(value or "")

    @staticmethod
    def _bullet_count(content: Any) -> int:
        if isinstance(content, list):
            return len([item for item in content if isinstance(item, str) and item.strip()])
        if isinstance(content, dict):
            return sum(ContentQualityAgent._bullet_count(v) for v in content.values())
        if isinstance(content, str):
            if "|" in content and "\n" in content:
                return 3
            return len([line for line in content.splitlines() if line.strip()])
        return 0

    @staticmethod
    def _chunks(items: List[SlideContent], size: int) -> List[List[SlideContent]]:
        return [items[i : i + size] for i in range(0, len(items), size)]
