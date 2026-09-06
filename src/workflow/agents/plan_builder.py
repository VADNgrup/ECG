import re
from typing import Any, Dict, List

from src.models.context import DocumentContext
from src.models.slide import Slide, Table, SlideType
from src.utils.config import Config
from src.utils.llm import chat
from src.utils.parse_llm_response import parse_json_response


class PlanBuilderAgent:
    """Merged Planner + PlanSpecer: context → lecture_title + slide_specs in one step."""

    MAX_EVIDENCE_LENGTH = 9000
    MAX_RETRIES = 3

    def __init__(self, model: str):
        self.model = model

    # ──────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────

    def build(self, context: DocumentContext, feedback: str = None) -> Dict[str, Any]:
        """Returns {"lecture_title": str, "slide_specs": List[Slide]}."""
        outline_md = self._build_outline(context, feedback)
        title      = self._generate_title(outline_md, context)
        specs      = self._specify(outline_md, context)
        return {"lecture_title": title, "slide_specs": specs}

    # ──────────────────────────────────────────────────────────────
    # Phase 1: outline generation
    # ──────────────────────────────────────────────────────────────

    def _build_outline(self, context: DocumentContext, feedback: str = None) -> str:
        markdown = context.text_content.markdown
        prompt = (
            "You are an expert presentation planner. Read the FULL raw document text below and "
            "propose a lecture slide outline.\n\n"
            f"# DOCUMENT (raw full text)\n{markdown}\n\n"
            "# INSTRUCTIONS\n"
            "1. Identify the most important topics/sections that should each become one presentation slide.\n"
            "2. Propose between 4 and 8 headings, ordered to match the document's logical flow.\n"
            "3. Each heading should be short (under 12 words), specific to this document, and in the "
            "SAME language as the document.\n"
            "4. Output ONLY the headings, one per line, each prefixed with '# '. No extra commentary, no numbering.\n\n"
            "Example output:\n# Heading One\n# Heading Two"
        )
        try:
            response = chat(self.model, [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=400)
            headings = [line.strip() for line in response.splitlines() if line.strip().startswith("#")]
            headings = [re.sub(r"^#+\s*", "", h).strip() for h in headings]
            headings = [h for h in headings if h][:8]
            if headings:
                return "\n".join(f"# {h}" for h in headings)
        except Exception as e:
            print(f"[PlanBuilder] Outline LLM call failed: {e}")
        return "# Overview\n# Key Topics"

    def _generate_title(self, outline_md: str, context: DocumentContext | None = None) -> str:
        headings = [re.sub(r"^#+\s+", "", line).strip() for line in outline_md.splitlines() if line.strip().startswith("#")]
        if headings:
            return headings[0]
        return "Generated Lecture"

    @classmethod
    def _deterministic_outline(cls, compact: Dict[str, Any]) -> str:
        document_insights    = compact.get("document_insights", {})
        points               = compact.get("must_have_points", [])[:8]
        presentation_units   = compact.get("presentation_units", []) or compact.get("content_units", [])
        primary_subject      = compact.get("primary_subject", "").strip()
        point_labels = [
            text
            for point in points
            for text in (point.get("label", ""), point.get("summary", ""))
            if text
        ]
        suggested_outline = [cls._normalize_heading(item) for item in document_insights.get("suggested_outline", [])]
        suggested_outline = [
            item for item in suggested_outline
            if item and cls._is_source_supported_heading(item, primary_subject, point_labels)
        ]
        if not points:
            points = [
                {
                    "label": section.get("title", ""),
                    "summary": section.get("summary", ""),
                    "source_pages": [section.get("page", 1)],
                    "source_unit_ids": [],
                }
                for section in compact.get("section_map", [])[:6]
            ]
        compiled_outline = cls._compile_outline(points, presentation_units)
        if suggested_outline:
            headings = suggested_outline if len(suggested_outline) >= 4 else cls._dedupe_headings(suggested_outline + compiled_outline)
        else:
            headings = compiled_outline or cls._group_numbered_points(points) or cls._group_general_points(points)
        headings = cls._coalesce_headings(cls._dedupe_headings(headings))
        headings = cls._remove_weak_duplicate_headings(headings, point_labels, primary_subject)
        if primary_subject and not cls._looks_weak_heading(primary_subject) and not cls._is_redundant_subject(primary_subject, headings):
            headings = [primary_subject] + headings
        headings = [heading for heading in headings if heading and not cls._is_noise_heading(heading)]
        headings = cls._remove_weak_duplicate_headings(headings, point_labels, primary_subject)
        protected_headings = set(h.lower() for h in suggested_outline + compiled_outline)
        headings = cls._remove_sparse_headings(headings, presentation_units, points, document_insights.get("must_include_labels", []), protected=protected_headings)
        if len(headings) < 2:
            headings = ([primary_subject] if primary_subject else []) + headings
        if not headings:
            headings = ["Overview", "Key Topics"]
        return "\n".join(f"# {heading}" for heading in headings[:8])

    @classmethod
    def _group_general_points(cls, points: List[Dict[str, Any]]) -> List[str]:
        labels = [cls._normalize_heading(point.get("label", "")) for point in points]
        return [label for label in labels if label][:6]

    @classmethod
    def _compile_outline(cls, points: List[Dict[str, Any]], presentation_units: List[Dict[str, Any]]) -> List[str]:
        candidates = cls._outline_candidates(points, presentation_units)
        if not candidates:
            return []
        clusters: List[Dict[str, Any]] = []
        for candidate in candidates:
            placed = False
            for cluster in clusters:
                if cls._same_outline_cluster(candidate, cluster):
                    cluster["items"].append(candidate)
                    cluster["score"] = max(cluster["score"], candidate["score"])
                    cluster["pages"].extend(candidate["pages"])
                    placed = True
                    break
            if not placed:
                clusters.append({"items": [candidate], "score": candidate["score"], "pages": list(candidate["pages"])})
        clusters.sort(key=lambda item: (-item["score"], min(item["pages"] or [999]), item["items"][0]["label"]))
        top_clusters = clusters[:8]
        top_clusters.sort(key=lambda item: (min(item["pages"] or [999]), -item["score"]))
        headings: List[str] = []
        for cluster in top_clusters:
            heading = cls._cluster_heading(cluster["items"])
            if heading and not cls._is_noise_heading(heading):
                headings.append(heading)
        return headings[:6]

    @classmethod
    def _outline_candidates(cls, points: List[Dict[str, Any]], presentation_units: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        seen = set()
        for point in points:
            label = cls._normalize_heading(point.get("label", ""))
            if not label or cls._is_noise_heading(label):
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "label": label,
                "summary": cls._normalize_heading(point.get("summary", "")),
                "score": float(point.get("salience_score", 0.0)) + 0.8,
                "pages": point.get("source_pages", []) or [999],
                "kind": "point",
            })
        for unit in presentation_units:
            if not unit.get("slideworthy"):
                continue
            if str(unit.get("presentation_role")) == "support_candidate":
                continue
            label = cls._normalize_heading(unit.get("outline_label") or unit.get("title") or "")
            if not label or cls._is_noise_heading(label):
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            candidates.append({
                "label": label,
                "summary": cls._normalize_heading(unit.get("summary", "")),
                "score": float(unit.get("salience_score", 0.0)),
                "pages": unit.get("source_pages", []) or [999],
                "kind": "unit",
            })
        return candidates

    @classmethod
    def _same_outline_cluster(cls, candidate: Dict[str, Any], cluster: Dict[str, Any]) -> bool:
        anchor = cluster["items"][0]
        left  = set(re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", candidate["label"].lower()))
        right = set(re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", anchor["label"].lower()))
        if left and right:
            overlap = len(left & right) / max(1, min(len(left), len(right)))
            if overlap >= 0.45:
                return True
        return bool(cls._shared_prefix(candidate["label"], anchor["label"]))

    @classmethod
    def _cluster_heading(cls, items: List[Dict[str, Any]]) -> str:
        labels = cls._dedupe_headings([item["label"] for item in items if item.get("label")])
        if not labels:
            return ""
        if len(labels) == 1:
            return labels[0]
        shared = labels[0]
        for label in labels[1:]:
            prefix = cls._shared_prefix(shared, label)
            if prefix:
                shared = prefix
            else:
                shared = ""
                break
        if shared and not cls._looks_generic(shared):
            return shared
        labels.sort(key=lambda item: (len(item.split()), len(item)))
        return labels[0]

    @classmethod
    def _group_numbered_points(cls, points: List[Dict[str, Any]]) -> List[str]:
        numbered = []
        for point in points:
            label  = point.get("label", "")
            number = point.get("number")
            if number is None:
                text  = f"{label} {point.get('summary', '')}"
                match = re.search(r"\b(\d{1,2})\b", text)
                number = int(match.group(1)) if match else None
            if number is not None:
                numbered.append((int(number), cls._normalize_heading(label) or cls._normalize_heading(point.get("summary", ""))))
        if len(numbered) >= 4:
            numbers = sorted({num for num, _ in numbered})
            groups  = []
            chunk   = 3 if len(numbers) > 6 else 2
            for idx in range(0, len(numbers), chunk):
                subset = numbers[idx: idx + chunk]
                if not subset:
                    continue
                groups.append(f"{subset[0]}" if len(subset) == 1 else f"{subset[0]}-{subset[-1]}")
            return groups
        return [cls._normalize_heading(point.get("label", "")) for point in points if cls._normalize_heading(point.get("label", ""))]

    @staticmethod
    def _shared_prefix(a: str, b: str) -> str:
        prefix = []
        for left, right in zip(a.split(), b.split()):
            if left.lower() != right.lower():
                break
            prefix.append(left)
        result = " ".join(prefix)
        return result if len(prefix) >= 2 else ""

    def _title_from_compact(self, compact: Dict[str, Any], outline_md: str) -> str:
        document_insights  = compact.get("document_insights", {})
        primary_subject    = compact.get("primary_subject", "").strip()
        points             = compact.get("must_have_points", [])[:6]
        labels = [
            text
            for point in points
            for text in (point.get("label", ""), point.get("summary", ""))
            if text
        ]
        source_hint     = f"{primary_subject} {compact.get('document_id', '')} {compact.get('source_file', '')}"
        suggested_title = document_insights.get("suggested_title", "").strip()
        if suggested_title and not self._is_noise_heading(suggested_title) and self._is_source_supported_title(suggested_title, source_hint, labels):
            return suggested_title
        prompt = (
            "Generate a concise presentation title in the SAME language as the source.\n"
            "The title must describe the WHOLE document, not one subsection.\n"
            "Avoid citation metadata, author contact lines, file sizes, and generic headings.\n"
            "Return only the title, max 14 words.\n\n"
            f"PRIMARY SUBJECT: {primary_subject}\n"
            f"DOCUMENT THESIS: {document_insights.get('document_thesis', '')}\n"
            f"MUST-HAVE POINTS: {labels}\n"
            f"OUTLINE:\n{outline_md}\n"
        )
        try:
            title = chat(self.model, [{"role": "user", "content": prompt}], temperature=0.1, max_tokens=80).strip()
            title = title.strip('"').strip("'").strip()
            if title and not self._is_noise_heading(title) and self._is_source_supported_title(title, source_hint, labels):
                return title
        except Exception:
            pass
        if primary_subject and labels:
            if self._looks_generic(primary_subject) or self._is_noise_heading(primary_subject):
                return labels[0]
            return primary_subject
        return labels[0] if labels else primary_subject or "Generated Lecture"

    @staticmethod
    def _is_source_supported_title(title: str, primary_subject: str, labels: List[str]) -> bool:
        title_terms  = PlanBuilderAgent._term_set(title)
        source_terms = PlanBuilderAgent._term_set(f"{primary_subject} {' '.join(labels)}")
        if not title_terms or not source_terms:
            return True
        overlap       = len(title_terms & source_terms)
        unsupported   = title_terms - source_terms
        overlap_ratio = overlap / max(1, len(title_terms))
        return overlap >= max(1, min(3, len(title_terms) // 2)) and overlap_ratio >= 0.42 and len(unsupported) <= max(2, len(title_terms) // 2)

    @classmethod
    def _is_source_supported_heading(cls, heading: str, primary_subject: str, labels: List[str]) -> bool:
        return cls._is_source_supported_title(heading, primary_subject, labels)

    @classmethod
    def _dedupe_headings(cls, headings: List[str]) -> List[str]:
        result, seen = [], set()
        for heading in headings:
            clean = cls._normalize_heading(heading)
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)
        return result

    @classmethod
    def _coalesce_headings(cls, headings: List[str]) -> List[str]:
        clusters: List[List[str]] = []
        for heading in headings:
            placed = False
            for cluster in clusters:
                candidate = {"label": heading}
                existing  = {"items": [{"label": cluster[0]}]}
                if cls._same_outline_cluster(candidate, existing):
                    cluster.append(heading)
                    placed = True
                    break
            if not placed:
                clusters.append([heading])
        collapsed = []
        for cluster in clusters:
            if len(cluster) == 1:
                collapsed.append(cluster[0])
                continue
            items   = [{"label": item, "summary": "", "score": 0.0, "pages": [999], "kind": "merged"} for item in cluster]
            heading = cls._cluster_heading(items)
            if heading:
                collapsed.append(heading)
        return collapsed

    @classmethod
    def _remove_weak_duplicate_headings(cls, headings: List[str], labels: List[str], primary_subject: str) -> List[str]:
        if len(headings) <= 3:
            return headings
        rich_terms       = cls._term_set(" ".join([primary_subject, *labels]))
        specific_headings = [h for h in headings if not cls._looks_weak_heading(h)]
        specific_terms    = [cls._term_set(h) for h in specific_headings]
        result: List[str] = []
        for heading in headings:
            terms = cls._term_set(heading)
            if not terms:
                continue
            if cls._looks_weak_heading(heading):
                covered_by_specific = any(terms & other for other in specific_terms)
                unsupported_generic  = rich_terms and not (terms & rich_terms)
                if covered_by_specific or unsupported_generic:
                    continue
            result.append(heading)
        return result or headings

    @classmethod
    def _remove_sparse_headings(cls, headings: List[str], presentation_units: List[Dict[str, Any]], points: List[Dict[str, Any]], must_include_labels: List[str] | None = None, protected: set | None = None) -> List[str]:
        if len(headings) <= 4:
            return headings
        protected = protected or set()
        scored: List[tuple[str, bool]] = []
        for heading in headings:
            if heading.lower() in protected:
                scored.append((heading, False))
                continue
            support_score      = cls._heading_support_score(heading, presentation_units)
            broad_score        = cls._heading_broad_support_score(heading, presentation_units)
            point_strength     = cls._heading_point_strength(heading, points)
            must_strength      = cls._heading_label_strength(heading, must_include_labels or [])
            weak               = (support_score < 1.25 and point_strength < 1.2) or (broad_score < 0.75 and point_strength < 1.2)
            detail_only        = cls._looks_detail_only_heading(heading, presentation_units, points)
            unsupported_by_narrative = broad_score < 0.5 and must_strength < 0.55
            scored.append((heading, weak or detail_only))
            if unsupported_by_narrative:
                scored[-1] = (heading, True)
        removable = sum(1 for _, should_remove in scored if should_remove)
        if len(headings) - removable < 4:
            removable = max(0, len(headings) - 4)
        kept: List[str] = []
        removed = 0
        for heading, should_remove in scored:
            if should_remove and removed < removable:
                removed += 1
                continue
            kept.append(heading)
        return kept or headings

    @classmethod
    def _heading_support_score(cls, heading: str, presentation_units: List[Dict[str, Any]]) -> float:
        heading_terms = cls._term_set(heading)
        if not heading_terms:
            return 0.0
        score = 0.0
        for unit in presentation_units:
            if unit.get("quality") == "noise":
                continue
            unit_terms = cls._term_set(" ".join(str(part) for part in [
                unit.get("title", ""), unit.get("outline_label", ""), unit.get("summary", ""),
            ] if part))
            if not unit_terms:
                continue
            overlap = len(heading_terms & unit_terms) / max(1, min(len(heading_terms), len(unit_terms)))
            if overlap < 0.25:
                continue
            weight = 1.0 if unit.get("type") in {"section", "page_focus", "numbered_item"} else 0.55
            if unit.get("slideworthy"):
                weight += 0.25
            score += weight * min(1.0, overlap + 0.25)
            if score >= 2.0:
                break
        return score

    @classmethod
    def _heading_broad_support_score(cls, heading: str, presentation_units: List[Dict[str, Any]]) -> float:
        heading_terms = cls._filtered_heading_terms(heading, presentation_units)
        if not heading_terms:
            return 0.0
        score = 0.0
        for unit in presentation_units:
            if unit.get("quality") == "noise" or unit.get("type") not in {"section", "page_focus", "numbered_item"}:
                continue
            unit_terms = cls._term_set(" ".join(str(part) for part in [
                unit.get("title", ""), unit.get("outline_label", ""), unit.get("summary", ""),
            ] if part))
            shared = heading_terms & unit_terms
            if len(heading_terms) <= 3 and len(shared) < 2:
                continue
            overlap = len(shared) / max(1, min(len(heading_terms), len(unit_terms)))
            if overlap >= 0.3:
                score += min(1.0, overlap + 0.25)
            if score >= 1.5:
                break
        return score

    @classmethod
    def _heading_point_strength(cls, heading: str, points: List[Dict[str, Any]]) -> float:
        heading_terms = cls._term_set(heading)
        best = 0.0
        for point in points:
            point_terms = cls._term_set(f"{point.get('label', '')} {point.get('summary', '')}")
            if not point_terms:
                continue
            overlap = len(heading_terms & point_terms) / max(1, min(len(heading_terms), len(point_terms)))
            if overlap < 0.28:
                continue
            page_count  = len({page for page in point.get("source_pages", []) if page})
            unit_count  = len([uid for uid in point.get("source_unit_ids", []) if uid])
            breadth     = min(1.0, 0.35 * page_count + 0.2 * unit_count)
            strength    = overlap + breadth
            if page_count <= 1 and unit_count <= 1:
                strength = min(strength, 0.9)
            best = max(best, strength)
        return best

    @classmethod
    def _heading_label_strength(cls, heading: str, labels: List[str]) -> float:
        heading_terms = cls._term_set(heading)
        best = 0.0
        for label in labels:
            label_terms = cls._term_set(str(label))
            if not label_terms:
                continue
            best = max(best, len(heading_terms & label_terms) / max(1, min(len(heading_terms), len(label_terms))))
        return best

    @classmethod
    def _looks_detail_only_heading(cls, heading: str, presentation_units: List[Dict[str, Any]], points: List[Dict[str, Any]]) -> bool:
        heading_terms     = cls._term_set(heading)
        if not heading_terms:
            return True
        best_point_units  = 0
        best_point_pages  = 0
        for point in points:
            point_terms = cls._term_set(f"{point.get('label', '')} {point.get('summary', '')}")
            overlap = len(heading_terms & point_terms) / max(1, min(len(heading_terms), len(point_terms))) if point_terms else 0.0
            if overlap >= 0.45:
                best_point_units = max(best_point_units, len([uid for uid in point.get("source_unit_ids", []) if uid]))
                best_point_pages = max(best_point_pages, len({page for page in point.get("source_pages", []) if page}))
        if best_point_units >= 2 or best_point_pages >= 2:
            return False
        if len(heading_terms) <= 3 and best_point_units <= 1:
            return True
        broad   = cls._heading_broad_support_score(heading, presentation_units)
        support = cls._heading_support_score(heading, presentation_units)
        return broad < 0.9 and support < 2.8

    @classmethod
    def _filtered_heading_terms(cls, heading: str, presentation_units: List[Dict[str, Any]]) -> set[str]:
        terms = cls._term_set(heading)
        if not terms:
            return set()
        broad_units = [u for u in presentation_units if u.get("quality") != "noise" and u.get("type") in {"section", "page_focus", "numbered_item"}]
        if len(broad_units) < 4:
            return terms
        counts = {term: 0 for term in terms}
        for unit in broad_units:
            unit_terms = cls._term_set(" ".join(str(part) for part in [
                unit.get("title", ""), unit.get("outline_label", ""), unit.get("summary", ""),
            ] if part))
            for term in terms & unit_terms:
                counts[term] += 1
        filtered = {term for term in terms if counts.get(term, 0) / max(1, len(broad_units)) <= 0.18}
        return filtered or terms

    @staticmethod
    def _normalize_heading(text: str) -> str:
        clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(text or "")).strip()
        clean = re.sub(r"[*_`#]+", "", clean)
        clean = re.sub(r"\s+", " ", clean)
        clean = re.sub(r"\s*[:;,-]\s*$", "", clean)
        return clean[:120]

    @staticmethod
    def _looks_generic(text: str) -> bool:
        lower = text.lower()
        return lower in {"overview", "summary", "introduction", "background", "section", "content"} or len(lower.split()) <= 2

    @staticmethod
    def _looks_weak_heading(text: str) -> bool:
        clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(text or "")).strip().lower()
        terms = PlanBuilderAgent._term_set(clean)
        stop = {
            "the", "and", "for", "with", "from", "this", "that", "overview", "summary", "introduction",
            "background", "methodology", "methods", "results", "conclusion", "content", "section", "dataset",
            "document", "information", "general", "tổng", "quan", "nội", "dung", "thông", "tin",
        }
        meaningful = [term for term in terms if term not in stop and not term.isdigit()]
        return len(meaningful) <= 1 and ":" not in clean

    @staticmethod
    def _term_set(text: str) -> set[str]:
        terms = set()
        for raw in re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}|\d+(?:\.\d+)?%?", text or ""):
            term = PlanBuilderAgent._normalise_term(raw)
            if term:
                terms.add(term)
        return terms

    @staticmethod
    def _normalise_term(term: str) -> str:
        clean = str(term or "").lower().strip("_-/")
        if not clean:
            return ""
        number = re.match(r"^(\d+)(?:\.\d+)?%?$", clean)
        if number:
            return number.group(1)
        for suffix in ("ing", "ed", "es", "s"):
            if len(clean) > len(suffix) + 4 and clean.endswith(suffix):
                clean = clean[: -len(suffix)]
                break
        return clean if len(clean) >= 3 else ""

    @staticmethod
    def _is_redundant_subject(primary_subject: str, headings: List[str]) -> bool:
        lower = primary_subject.lower()
        if any(lower == h.lower() for h in headings[:2]):
            return True
        subject_terms = PlanBuilderAgent._term_set(primary_subject)
        for heading in headings[:2]:
            heading_terms = PlanBuilderAgent._term_set(heading)
            if subject_terms and heading_terms:
                overlap = len(subject_terms & heading_terms) / max(1, min(len(subject_terms), len(heading_terms)))
                if overlap >= 0.45:
                    return True
        return False

    @staticmethod
    def _is_noise_heading(text: str) -> bool:
        lower = text.lower().strip()
        words = lower.split()
        if not words:
            return True
        bad_endings = {
            "the", "a", "an", "and", "or", "to", "of", "in", "on", "with", "for", "by", "as",
            "which", "that", "at", "from", "into", "onto", "upon", "is", "are", "was", "were",
            "be", "been", "being", "am", "it", "this", "these", "those",
        }
        noise_prefixes = (
            "cite this document as", "last retrieved",
            "abstract", "tóm tắt", "摘要", "аннотация", "абстракт", "초록", "บทคัดย่อ",
            "references", "bibliography", "tài liệu tham khảo", "参考文献", "参考", "литература", "список литературы", "библиография", "참고문헌", "เอกสารอ้างอิง",
            "acknowledgement", "acknowledgment", "lời cảm ơn", "cảm ơn", "致谢", "鸣谢", "благодарности", "감사의 글", "กิตติกรรมประกาศ",
            "funding", "tài trợ", "资金", "经费", "финансирование", "грант", "자금", "조달", "การระดมทุน", "กองทุน",
            "conflict of interest", "xung đột lợi ích", "利益冲突", "конфликт интересов", "이해 상충", "ความขัดแย้งทางผลประโยชน์",
            "keywords", "từ khóa", "关键词", "关键字", "ключевые слова", "주제어", "키워드", "คำสำคัญ",
            "author", "tác giả", "作者", "автор", "저자", "ผู้แต่ง", "ผู้เขียน",
        )
        noise_exacts = {
            "doi",
            "additional information", "other information", "more information", "miscellaneous", "general information",
            "thông tin thêm", "thông tin khác", "khác", "ghi chú",
            "附加信息", "其他信息", "更多信息", "杂项", "一般信息", "备注",
            "дополнительная информация", "другая информация", "прочая информация", "общая информация", "заметки",
            "추가 정보", "기타 정보", "자세한 정보", "일반 정보", "참고",
            "ข้อมูลเพิ่มเติม", "ข้อมูลอื่น ๆ", "ข้อมูลทั่วไป", "หมายเหตุ",
        }
        table_fig_pattern = r'^(table|figure|bảng|hình|biểu đồ|fig\.?|tbl\.?|表|表格|图表|图|图像|图片|附图|таблица|табл\.?|рисунок|рис\.?|표|그림|ตาราง|รูป|รูปภาพ|ภาพ)\s*(?:\d+|[ivx]+)\b'
        return (
            lower.startswith(noise_prefixes)
            or lower in noise_exacts
            or bool(re.search(r'\bvol\.?\s*\d+\b', lower) and re.search(r'\b\d{4}\b', lower))
            or bool(re.match(r'^[a-z]+\s+[a-z]+\s+(là|is)\s+', lower))
            or bool(re.match(table_fig_pattern, lower))
            or 'nhà nghiên cứu tại' in lower
            or 'researcher at' in lower
            or '연구원' in lower
            or 'исследователь в' in lower
            or '研究员' in lower
            or 'นักวิจัยที่' in lower
            or words[-1] in bad_endings
            or len(words) > 18
        )

    # ──────────────────────────────────────────────────────────────
    # Phase 2: LLM slide specification (from PlanSpecerAgent)
    # ──────────────────────────────────────────────────────────────

    def _chat(self, messages: list) -> str:
        return chat(self.model, messages, temperature=0.3, max_tokens=4096)

    @staticmethod
    def _outline_md_to_number(outline_md: str) -> str:
        lines    = outline_md.splitlines()
        counters: List[int] = []
        result_lines: List[str] = []
        for line in lines:
            match = re.match(r'^(#+)\s+(.*)', line.strip())
            if not match:
                if line.strip():
                    result_lines.append(line)
                continue
            hashes = match.group(1)
            title  = match.group(2)
            depth  = len(hashes)
            if depth > len(counters):
                while len(counters) < depth:
                    counters.append(0)
            else:
                counters = counters[:depth]
            counters[-1] += 1
            number_str = '.'.join(str(c) for c in counters)
            result_lines.append(f'{number_str}. {title}' if depth == 1 else f'{number_str} {title}')
        return '\n'.join(result_lines)

    @staticmethod
    def _extract_expected_titles(numbered_outline: str) -> List[str]:
        return [line.strip() for line in numbered_outline.splitlines() if re.match(r'^\d[\d.]*[\. ]', line.strip())]

    @staticmethod
    def _normalize_title(title: str) -> str:
        return re.sub(r'\s+', ' ', title.strip().lower())

    @classmethod
    def _find_missing_titles(cls, raw_specs: List[Dict], expected_titles: List[str]) -> List[str]:
        def prefix_of(title: str) -> str:
            m = re.match(r'^([\d.]+)', title.strip())
            return m.group(1).rstrip('.') if m else ''
        returned_prefixes = {prefix_of(d.get('slide_title', '')) for d in raw_specs if isinstance(d, dict)}
        return [t for t in expected_titles if prefix_of(t) not in returned_prefixes]

    def _build_spec_prompt(self, numbered_outline: str, evidence_text: str, context: DocumentContext) -> str:
        schema_example = (
            '{\n'
            '  "slide_title": "<concise polished title — MUST preserve number prefix>",\n'
            '  "slide_type": "<content | have_table | have_formula | comparison | two_sub_contents>",\n'
            '  "goal": "<1-2 sentence goal>",\n'
            '  "table": {"table_markdown": "...", "table_caption": "..."} or null,\n'
            '  "latex_block_formula": "<LaTeX block formula>" or null\n'
            '}'
        )
        return (
            f'\n# ROLE\nYou are a lecture slide specification architect.\n\n'
            f'# TASK\nGiven the FULL numbered lecture outline and relevant retrieved source evidence, produce a JSON array\n'
            f'of slide specifications — one object per heading line (both major `1.` and sub `1.1`, `1.1.1`, etc.).\n'
            f'Every heading in the outline MUST have exactly ONE corresponding JSON object.\n\n'
            f'# INPUT\n## Full Numbered Outline\n{numbered_outline}\n\n'
            f'## Retrieved Source Evidence\n{evidence_text}\n\n'
            f'## Tables extracted from document\n{context.tables}\n\n'
            f'# IMPORTANT CONSTRAINTS\n'
            f'1. `slide_type` must be one of: "content", "have_table", "have_formula", "comparison", "two_sub_contents".\n'
            f'2. Use "have_table" ONLY if the source evidence contains a table supporting this slide.\n'
            f'   If so, include `table` with the markdown and caption. Otherwise set `table` to null.\n'
            f'   2.1. Prioritize extracting the tables mentioned in the evidence.\n'
            f'   2.2. Only tables with more than 2 rows and 2 columns. Smaller tables → use "content".\n'
            f'3. Use "have_formula" ONLY if the source evidence contains a block-level formula for this slide.\n'
            f'   If so, include `latex_block_formula`. Otherwise set it to null.\n'
            f'4. Use "comparison": `goal` must describe the two comparable entities briefly.\n'
            f'5. Use "two_sub_contents": `goal` must describe the two distinct sub-topics shown side by side.\n'
            f'6. Default to "content" when none of the above special types apply.\n\n'
            f'# CRITICAL\n'
            f'- `slide_title` MUST preserve the numbering prefix EXACTLY as it appears in the outline.\n'
            f'  However, you MUST paraphrase and polish the rest of the title to sound professional and concise.\n'
            f'- `goal` MUST stay in the SAME language as `slide_title`.\n'
            f'- The output array MUST contain one entry for EVERY line in the outline above — no omissions.\n\n'
            f'# OUTPUT FORMAT\nReturn ONLY a valid JSON array. Each element must follow this schema:\n[{schema_example}]\n'
        )

    def _retrieve_outline_evidence(self, context: DocumentContext, expected_titles: List[str]) -> str:
        return context.text_content.markdown[:self.MAX_EVIDENCE_LENGTH]

    @classmethod
    def _ranked_page_blocks(cls, context: DocumentContext, expected_titles: List[str]) -> List[str]:
        pages       = cls._split_pages(context.text_content.markdown)
        if not pages:
            return []
        query_terms = cls._outline_terms(expected_titles)
        scored      = []
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
        seen, result = set(), []
        for title in expected_titles:
            for term in re.findall(r"[A-Za-z0-9_]{4,}", title.lower()):
                if term not in stop and not term.isdigit() and term not in seen:
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
            end   = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown)
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
            table = Table(
                table_markdown=table_data.get('table_markdown', ''),
                table_caption=table_data.get('table_caption', ''),
            )
        title      = d.get('slide_title', '')
        clean_title = re.sub(r'^[\d.]+\s*', '', title).strip()
        goal        = PlanBuilderAgent._normalise_goal_language(d.get('goal', ''), clean_title)
        return Slide(slide_title=clean_title, slide_type=slide_type, goal=goal, table=table, latex_block_formula=d.get('latex_block_formula'))

    def _specify(self, outline_md: str, context: DocumentContext) -> List[Slide]:
        numbered_outline = self._outline_md_to_number(outline_md)
        expected_titles  = self._extract_expected_titles(numbered_outline)
        evidence_text    = self._retrieve_outline_evidence(context, expected_titles)
        print(f'\n[PlanBuilder] Outline has {len(expected_titles)} heading(s) to spec.')
        last_raw_specs: List[Dict] = []
        for attempt in range(1, self.MAX_RETRIES + 1):
            print(f'[PlanBuilder] Attempt {attempt}/{self.MAX_RETRIES} — calling LLM for full outline...')
            prompt = self._build_spec_prompt(numbered_outline, evidence_text, context)
            try:
                raw_content = self._chat([{'role': 'user', 'content': prompt}])
            except Exception as e:
                print(f'[PlanBuilder] LLM call failed on attempt {attempt}: {e}')
                if attempt < self.MAX_RETRIES:
                    continue
                break
            try:
                raw_specs = self._parse_json_response(raw_content)
            except Exception as e:
                print(f'[PlanBuilder] JSON parse error on attempt {attempt}: {e}')
                if attempt < self.MAX_RETRIES:
                    continue
                continue
            if not isinstance(raw_specs, list):
                print(f'[PlanBuilder] ✗ Expected a JSON array, got {type(raw_specs).__name__}. Retrying...')
                continue
            raw_specs      = self._align_specs_to_outline(raw_specs, expected_titles)
            last_raw_specs = raw_specs
            missing        = self._find_missing_titles(raw_specs, expected_titles)
            if missing:
                print(f"[PlanBuilder] {len(missing)} heading(s) missing (e.g. {missing[:3]}{('...' if len(missing) > 3 else '')}).")
                if attempt < self.MAX_RETRIES:
                    continue
                continue
            print(f'[PlanBuilder] All {len(raw_specs)} specs validated successfully.')
            return self._build_slide_list(raw_specs)
        if last_raw_specs:
            filled = self._fill_missing_specs(last_raw_specs, expected_titles)
            print(f'[PlanBuilder] WARNING: max retries reached. Using repaired result ({len(filled)} specs).')
            return self._build_slide_list(filled)
        fallback = self._fallback_specs(expected_titles)
        print(f'[PlanBuilder] WARNING: max retries reached. Using outline fallback ({len(fallback)} specs).')
        return self._build_slide_list(fallback)

    def _fill_missing_specs(self, raw_specs: List[Dict], expected_titles: List[str]) -> List[Dict]:
        by_prefix = {}
        for spec in raw_specs:
            if not isinstance(spec, dict):
                continue
            m = re.match(r'^([\d.]+)', str(spec.get('slide_title', '')).strip())
            if m:
                by_prefix[m.group(1).rstrip('.')] = spec
        repaired = []
        for title in expected_titles:
            m      = re.match(r'^([\d.]+)', title.strip())
            prefix = m.group(1).rstrip('.') if m else title
            repaired.append(by_prefix.get(prefix) or self._fallback_spec(title))
        return repaired

    def _align_specs_to_outline(self, raw_specs: List[Dict], expected_titles: List[str]) -> List[Dict]:
        usable_specs = [spec for spec in raw_specs if isinstance(spec, dict)]
        if len(usable_specs) < len(expected_titles):
            return self._normalise_specs(usable_specs)
        aligned = []
        for title, spec in zip(expected_titles, usable_specs):
            spec      = dict(spec)
            llm_title = str(spec.get('slide_title', '')).strip()
            if not llm_title:
                spec['slide_title'] = title
            else:
                m               = re.match(r'^([\d.]+)', title.strip())
                expected_prefix = m.group(1) if m else ''
                llm_m           = re.match(r'^([\d.]+)', llm_title)
                llm_prefix      = llm_m.group(1) if llm_m else ''
                if expected_prefix and llm_prefix != expected_prefix:
                    clean_llm           = re.sub(r'^[\d.]+\s*', '', llm_title).strip()
                    spec['slide_title'] = f"{expected_prefix} {clean_llm}"
            aligned.append(spec)
        return self._normalise_specs(aligned)

    def _fallback_specs(self, expected_titles: List[str]) -> List[Dict]:
        return [self._fallback_spec(title) for title in expected_titles]

    @staticmethod
    def _fallback_spec(title: str) -> Dict[str, Any]:
        clean_title = re.sub(r'^\d[\d.]*\s*', '', title).strip()
        return {
            "slide_title":        title,
            "slide_type":         "content",
            "goal":               clean_title or title,
            "table":              None,
            "latex_block_formula": None,
        }

    @classmethod
    def _normalise_specs(cls, specs: List[Dict]) -> List[Dict]:
        return [{**spec, 'goal': cls._normalise_goal_language(spec.get('goal', ''), spec.get('slide_title', ''))} for spec in specs]

    @staticmethod
    def _normalise_goal_language(goal: str, slide_title: str) -> str:
        clean_goal  = re.sub(r'\s+', ' ', str(goal or '')).strip()
        clean_title = re.sub(r'^\d+(?:\.\d+)*[.)]?\s*', '', str(slide_title or '')).strip()
        return clean_goal or clean_title

    def _build_slide_list(self, raw_specs: List[Dict]) -> List[Slide]:
        all_specs = [self._dict_to_slide(d) for d in raw_specs]
        for idx, slide in enumerate(all_specs, start=1):
            slide.slide_number = idx
        print(f'[PlanBuilder] Total slide specs: {len(all_specs)}')
        return all_specs
