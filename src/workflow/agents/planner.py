import re
from typing import Any, Dict, List

from src.models.context import DocumentContext
from src.utils.llm import chat


class PlannerAgent:

    def __init__(self, model: str):
        self.model = model

    def create_outline(self, context: DocumentContext, feedback: str = None) -> Dict[str, Any]:
        headings = re.findall(r"^#{1,4}\s+(.+)$", context.text_content.markdown, flags=re.MULTILINE)
        headings = [h.strip() for h in headings if h.strip()][:8]
        if not headings:
            headings = ["Overview", "Key Topics"]
        outline = "\n".join(f"# {h}" for h in headings)
        return {"outline": outline}

    def generate_title(self, outline_md: str, context: DocumentContext | None = None) -> str:
        headings = [re.sub(r"^#+\s+", "", line).strip() for line in outline_md.splitlines() if line.strip().startswith("#")]
        if headings:
            return headings[0]
        return "Generated Lecture"

    @classmethod
    def _deterministic_outline(cls, compact: Dict[str, Any]) -> str:
        document_insights = compact.get("document_insights", {})
        points = compact.get("must_have_points", [])[:8]
        presentation_units = compact.get("presentation_units", []) or compact.get("content_units", [])
        primary_subject = compact.get("primary_subject", "").strip()
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

        # Protect both suggested and compiled outlines so we don't aggressively filter out good headings
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
        labels = [label for label in labels if label]
        return labels[:6]

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
                clusters.append({
                    "items": [candidate],
                    "score": candidate["score"],
                    "pages": list(candidate["pages"]),
                })
        # 1. Sort by score descending to get the most salient clusters
        clusters.sort(key=lambda item: (-item["score"], min(item["pages"] or [999]), item["items"][0]["label"]))
        
        # 2. Take the top 8 most important clusters
        top_clusters = clusters[:8]
        
        # 3. Re-sort the top clusters chronologically by page order
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
        left = set(re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", candidate["label"].lower()))
        right = set(re.findall(r"[A-Za-zÀ-ỹ][A-Za-zÀ-ỹ0-9_/-]{2,}", anchor["label"].lower()))
        if left and right:
            overlap = len(left & right) / max(1, min(len(left), len(right)))
            if overlap >= 0.45:
                return True
        return bool(cls._shared_prefix(candidate["label"], anchor["label"]))

    @classmethod
    def _cluster_heading(cls, items: List[Dict[str, Any]]) -> str:
        labels = [item["label"] for item in items if item.get("label")]
        labels = cls._dedupe_headings(labels)
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
            label = point.get("label", "")
            number = point.get("number")
            if number is None:
                text = f"{label} {point.get('summary', '')}"
                match = re.search(r"\b(\d{1,2})\b", text)
                number = int(match.group(1)) if match else None
            if number is not None:
                numbered.append((int(number), cls._normalize_heading(label) or cls._normalize_heading(point.get("summary", ""))))
        if len(numbered) >= 4:
            numbers = sorted({num for num, _ in numbered})
            groups = []
            chunk = 3 if len(numbers) > 6 else 2
            for idx in range(0, len(numbers), chunk):
                subset = numbers[idx: idx + chunk]
                if not subset:
                    continue
                if len(subset) == 1:
                    groups.append(f"{subset[0]}")
                else:
                    groups.append(f"{subset[0]}-{subset[-1]}")
            return groups
        return [cls._normalize_heading(point.get("label", "")) for point in points if cls._normalize_heading(point.get("label", ""))]

    @classmethod
    def _compress_heading_group(cls, labels: List[str], fallback: str) -> str:
        labels = [label for label in labels if label]
        if not labels:
            return fallback
        if len(labels) == 1:
            return labels[0]
        first = labels[0]
        second = labels[1]
        if cls._shared_prefix(first, second):
            return cls._shared_prefix(first, second)
        return fallback

    @staticmethod
    def _shared_prefix(a: str, b: str) -> str:
        a_words = a.split()
        b_words = b.split()
        prefix = []
        for left, right in zip(a_words, b_words):
            if left.lower() != right.lower():
                break
            prefix.append(left)
        result = " ".join(prefix)
        return result if len(prefix) >= 2 else ""

    def _title_from_compact(self, compact: Dict[str, Any], outline_md: str) -> str:
        document_insights = compact.get("document_insights", {})
        primary_subject = compact.get("primary_subject", "").strip()
        points = compact.get("must_have_points", [])[:6]
        labels = [
            text
            for point in points
            for text in (point.get("label", ""), point.get("summary", ""))
            if text
        ]
        source_hint = f"{primary_subject} {compact.get('document_id', '')} {compact.get('source_file', '')}"
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
        title_terms = PlannerAgent._term_set(title)
        source_terms = PlannerAgent._term_set(f"{primary_subject} {' '.join(labels)}")
        if not title_terms or not source_terms:
            return True
        overlap = len(title_terms & source_terms)
        unsupported = title_terms - source_terms
        overlap_ratio = overlap / max(1, len(title_terms))
        return overlap >= max(1, min(3, len(title_terms) // 2)) and overlap_ratio >= 0.42 and len(unsupported) <= max(2, len(title_terms) // 2)

    @classmethod
    def _is_source_supported_heading(cls, heading: str, primary_subject: str, labels: List[str]) -> bool:
        return cls._is_source_supported_title(heading, primary_subject, labels)

    @classmethod
    def _dedupe_headings(cls, headings: List[str]) -> List[str]:
        result = []
        seen = set()
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
                existing = {"items": [{"label": cluster[0]}]}
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
            items = [{"label": item, "summary": "", "score": 0.0, "pages": [999], "kind": "merged"} for item in cluster]
            heading = cls._cluster_heading(items)
            if heading:
                collapsed.append(heading)
        return collapsed

    @classmethod
    def _remove_weak_duplicate_headings(cls, headings: List[str], labels: List[str], primary_subject: str) -> List[str]:
        if len(headings) <= 3:
            return headings
        rich_terms = cls._term_set(" ".join([primary_subject, *labels]))
        result: List[str] = []
        specific_headings = [heading for heading in headings if not cls._looks_weak_heading(heading)]
        specific_terms = [cls._term_set(heading) for heading in specific_headings]
        for heading in headings:
            terms = cls._term_set(heading)
            if not terms:
                continue
            if cls._looks_weak_heading(heading):
                covered_by_specific = any(terms & other for other in specific_terms)
                unsupported_generic = rich_terms and not (terms & rich_terms)
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
            support_score = cls._heading_support_score(heading, presentation_units)
            broad_score = cls._heading_broad_support_score(heading, presentation_units)
            point_strength = cls._heading_point_strength(heading, points)
            must_strength = cls._heading_label_strength(heading, must_include_labels or [])
            weak = (support_score < 1.25 and point_strength < 1.2) or (broad_score < 0.75 and point_strength < 1.2)
            detail_only = cls._looks_detail_only_heading(heading, presentation_units, points)
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
                unit.get("title", ""),
                unit.get("outline_label", ""),
                unit.get("summary", ""),
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
                unit.get("title", ""),
                unit.get("outline_label", ""),
                unit.get("summary", ""),
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
            page_count = len({page for page in point.get("source_pages", []) if page})
            unit_count = len([unit_id for unit_id in point.get("source_unit_ids", []) if unit_id])
            breadth = min(1.0, 0.35 * page_count + 0.2 * unit_count)
            strength = overlap + breadth
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
        heading_terms = cls._term_set(heading)
        if not heading_terms:
            return True
        best_point_units = 0
        best_point_pages = 0
        for point in points:
            point_terms = cls._term_set(f"{point.get('label', '')} {point.get('summary', '')}")
            overlap = len(heading_terms & point_terms) / max(1, min(len(heading_terms), len(point_terms))) if point_terms else 0.0
            if overlap >= 0.45:
                best_point_units = max(best_point_units, len([unit_id for unit_id in point.get("source_unit_ids", []) if unit_id]))
                best_point_pages = max(best_point_pages, len({page for page in point.get("source_pages", []) if page}))
        if best_point_units >= 2 or best_point_pages >= 2:
            return False
        if len(heading_terms) <= 3 and best_point_units <= 1:
            return True
        broad = cls._heading_broad_support_score(heading, presentation_units)
        support = cls._heading_support_score(heading, presentation_units)
        return broad < 0.9 and support < 2.8

    @classmethod
    def _filtered_heading_terms(cls, heading: str, presentation_units: List[Dict[str, Any]]) -> set[str]:
        terms = cls._term_set(heading)
        if not terms:
            return set()
        broad_units = [
            unit for unit in presentation_units
            if unit.get("quality") != "noise" and unit.get("type") in {"section", "page_focus", "numbered_item"}
        ]
        if len(broad_units) < 4:
            return terms
        counts = {term: 0 for term in terms}
        for unit in broad_units:
            unit_terms = cls._term_set(" ".join(str(part) for part in [
                unit.get("title", ""),
                unit.get("outline_label", ""),
                unit.get("summary", ""),
            ] if part))
            for term in terms & unit_terms:
                counts[term] += 1
        filtered = {
            term for term in terms
            if counts.get(term, 0) / max(1, len(broad_units)) <= 0.18
        }
        return filtered or terms

    @staticmethod
    def _normalize_heading(text: str) -> str:
        clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(text or "")).strip()
        clean = re.sub(r"[*_`#]+", "", clean)
        clean = re.sub(r"\s+", " ", clean)
        clean = re.sub(r"\s*[:;,-]\s*$", "", clean)
        if len(clean) > 120:
            clean = clean[:120]
        return clean

    @staticmethod
    def _looks_meta_label(text: str) -> bool:
        lower = text.lower()
        return any(term in lower for term in ["overview", "summary", "tổng quan", "thời gian", "hình thức", "introduction", "background"])

    @staticmethod
    def _looks_generic(text: str) -> bool:
        lower = text.lower()
        return lower in {"overview", "summary", "introduction", "background", "section", "content"} or len(lower.split()) <= 2

    @staticmethod
    def _looks_weak_heading(text: str) -> bool:
        clean = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", str(text or "")).strip().lower()
        terms = PlannerAgent._term_set(clean)
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
            term = PlannerAgent._normalise_term(raw)
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
        if any(lower == heading.lower() for heading in headings[:2]):
            return True
        subject_terms = PlannerAgent._term_set(primary_subject)
        for heading in headings[:2]:
            heading_terms = PlannerAgent._term_set(heading)
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
            
        bad_endings = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "with", "for", "by", "as", "which", "that", "at", "from", "into", "onto", "upon", "is", "are", "was", "were", "be", "been", "being", "am", "it", "this", "these", "those"}
        
        noise_prefixes = (
            "cite this document as", "last retrieved",
            "abstract", "tóm tắt", "摘要", "аннотация", "абстракт", "초록", "บทคัดย่อ",
            "references", "bibliography", "tài liệu tham khảo", "参考文献", "参考", "литература", "список литературы", "библиография", "참고문헌", "เอกสารอ้างอิง",
            "acknowledgement", "acknowledgment", "lời cảm ơn", "cảm ơn", "致谢", "鸣谢", "благодарности", "감사의 글", "กิตติกรรมประกาศ",
            "funding", "tài trợ", "资金", "经费", "финансирование", "грант", "자금", "조달", "การระดมทุน", "กองทุน",
            "conflict of interest", "xung đột lợi ích", "利益冲突", "конфликт интересов", "이해 상충", "ความขัดแย้งทางผลประโยชน์",
            "keywords", "từ khóa", "关键词", "关键字", "ключевые слова", "주제어", "키워드", "คำสำคัญ",
            "author", "tác giả", "作者", "автор", "저자", "ผู้แต่ง", "ผู้เขียน"
        )
        
        noise_exacts = {
            "doi", 
            "additional information", "other information", "more information", "miscellaneous", "general information", 
            "thông tin thêm", "thông tin khác", "khác", "ghi chú",
            "附加信息", "其他信息", "更多信息", "杂项", "一般信息", "备注",
            "дополнительная информация", "другая информация", "прочая информация", "общая информация", "заметки",
            "추가 정보", "기타 정보", "자세한 정보", "일반 정보", "참고",
            "ข้อมูลเพิ่มเติม", "ข้อมูลอื่น ๆ", "ข้อมูลทั่วไป", "หมายเหตุ"
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
