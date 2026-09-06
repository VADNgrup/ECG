import asyncio
from src.utils.llm import chat, achat
from typing import List, Dict, Any
import json
from dataclasses import asdict
from src.models.context import DocumentContext
from src.models.slide import SlideContent, Slide
from src.models.feedback import Issue, CriterionResult, SlideReview, WriterReview, Severity

class ReviewerAgent:

    def __init__(self, model: str):
        self.model = model

    async def evaluate(self, slides: List[SlideContent], context: DocumentContext, lecture_plan: Dict[str, Any], slide_specs: List[Slide]=None) -> WriterReview:
        (faith_reviews, cov_reviews, pres_reviews, coh_reviews) = await asyncio.gather(self._evaluate_faithfulness(slides, context), self._evaluate_coverage_and_clarity(slides, lecture_plan, slide_specs or []), self._evaluate_presentation_quality(slides, lecture_plan), self._evaluate_coherence(slides))
        merged: Dict[int, SlideReview] = {}
        for (idx, slide) in enumerate(slides, 1):
            merged[idx] = SlideReview(slide_index=idx, slide_title=slide.slide.slide_title, criteria={})
        for (criterion_name, reviews) in [('faithfulness', faith_reviews), ('coverage_and_clarity', cov_reviews), ('presentation_quality', pres_reviews), ('coherence', coh_reviews)]:
            for sr in reviews:
                if sr.slide_index in merged:
                    merged[sr.slide_index].criteria[criterion_name] = sr.criteria.get(criterion_name, CriterionResult(criterion=criterion_name))
            for slide_idx in merged:
                if criterion_name not in merged[slide_idx].criteria:
                    merged[slide_idx].criteria[criterion_name] = CriterionResult(criterion=criterion_name, issues=[])
        return WriterReview(slide_reviews=list(merged.values()))

    async def _evaluate_faithfulness(self, slides: List[SlideContent], context: DocumentContext) -> List[SlideReview]:
        slides_json = json.dumps([{'slide_number': i, 'slide_title': s.slide.slide_title, 'content': s.content} for (i, s) in enumerate(slides, 1)], indent=2)
        prompt = f'You are a fact-checker. Compare the slides against the source document and detect any inaccuracies or hallucinations.\n\nSOURCE DOCUMENT:\n{context.text_content.markdown[:10000]}\n\nSLIDES:\n{slides_json}\n\nFor each slide that has issues, return a JSON entry. Slides with no issues should NOT appear in the list.\n\nReturn ONLY valid JSON:\n{{\n  "slide_reviews": [\n    {{\n      "slide_number": 3,\n      "issues": [\n        {{\n          "severity": "critical",\n          "location": "Slide 3, bullet 2",\n          "description": "Year 2023 changed to 2024 — source says 2023",\n          "suggestion": "Revert to 2023 as stated in the source",\n          "confidence_score": 0.95\n        }}\n      ]\n    }}\n  ]\n}}\nUse severity as follows:\n- "critical": factual errors, hallucinations, years/numbers changed — set confidence > 0.90 only if you are near-certain\n- "major": important phrasing issue, significant omission of a supporting fact — set confidence > 0.85 only if clearly evident\n- "minor": style, tone, or phrasing suggestion — set confidence > 0.80 only if genuinely useful\nIf you are not confident enough to meet the bar for a severity level, omit the issue entirely.'
        content = await achat(self.model, [{'role': 'user', 'content': prompt}], temperature=0.2)
        return self._parse_slide_reviews('faithfulness', content, slides)

    async def _evaluate_coverage_and_clarity(self, slides: List[SlideContent], lecture_plan: Dict[str, Any], slide_specs: List[Slide]) -> List[SlideReview]:
        slides_json = json.dumps([{'slide_number': i, 'slide_title': s.slide.slide_title, 'content': s.content} for (i, s) in enumerate(slides, 1)], indent=2)
        specs_json = json.dumps([{'slide_number': i, 'slide_title': spec.slide_title, 'goal': spec.goal} for (i, spec) in enumerate(slide_specs, 1)], indent=2, ensure_ascii=False) if slide_specs else '(no specs provided)'
        prompt = f'You are a curriculum reviewer. Your primary job is to verify that each slide strictly achieves its stated goal.\n\nSLIDE SPECIFICATIONS (the intended goal each slide MUST fulfil):\n{specs_json}\n\nSLIDES (actual generated content to evaluate):\n{slides_json}\n\n# CORE RULE\nEach slide has an explicit goal defined in the spec above.\nA slide PASSES only if its content directly and fully addresses that goal.\nA slide FAILS if its content is off-topic, too vague, or covers only a superficial part of the goal.\n\nEvaluate per slide on TWO dimensions:\n\nCOVERAGE — Goal adherence (most important):\n1. Does EVERY key idea stated in the goal appear in the slide content?\n2. Is there any part of the goal that is completely missing from the content?\n3. Does the slide drift away from its intended focus and cover something not in the goal?\n\nCLARITY — Student comprehension:\n1. Are key concepts named and explained without assuming prior knowledge?\n2. Are any undefined terms or jargon used without explanation?\n\nReturn ONLY valid JSON listing only slides WITH issues:\n{{\n  "slide_reviews": [\n    {{\n      "slide_number": 5,\n      "issues": [\n        {{\n          "severity": "critical",\n          "location": "Slide 5",\n          "description": "Goal required explaining X method in detail, but slide only names it without any elaboration",\n          "suggestion": "Add 2-3 bullets explaining how X method works as specified in the goal",\n          "confidence_score": 0.93\n        }},\n      ]\n    }}\n  ]\n}}\nUse severity as follows:\n- "critical": slide completely fails its goal, or major part of the goal is absent — confidence > 0.90\n- "major": goal only partially met, an important concept from the goal is missing or too shallow — confidence > 0.85\n- "minor": minor gap in clarity or a small suggestion to better fulfil the goal — confidence > 0.80\nOmit any issue where you are not confident enough to meet the threshold for its severity.'
        content = await achat(self.model, [{'role': 'user', 'content': prompt}], temperature=0.2)
        return self._parse_slide_reviews('coverage_and_clarity', content, slides)

    async def _evaluate_presentation_quality(self, slides: List[SlideContent], lecture_plan: Dict[str, Any]) -> List[SlideReview]:
        slides_json = json.dumps([{'slide_number': i, 'slide_title': s.slide.slide_title, 'content': s.content} for (i, s) in enumerate(slides, 1)], indent=2)
        prompt = f'You are an academic examiner evaluating the presentation format and quality of lecture slides.\n\nPLANNED OUTLINE:\n{json.dumps(lecture_plan, indent=2)}\n\nSLIDES:\n{slides_json}\n\nEvaluate per slide on TWO dimensions:\n\nFORMAT & PEDAGOGY:\n1. Content density: ideally 4-6 main bullets. Each bullet up to 25 words to maintain complex concepts. Total <= 95 words per slide. Sub-bullets are allowed if they help logical grouping and coherence.\n2. Tone: friendly, conversational, not overly academic?\n3. Structure: 3-6 bullets per slide?\n4. Flow: logical progression between adjacent slides?\n\nPRESENTATION PROFESSIONALISM:\n1. Clear, informative titles (not vague like "More Details")?\n2. Consistent terminology used throughout the deck?\n3. Balanced and well-organized content?\n4. Would an academic examiner approve the overall quality?\n\nReturn ONLY valid JSON listing only slides WITH issues:\n{{\n  "slide_reviews": [\n    {{\n      "slide_number": 2,\n      "issues": [\n        {{\n          "severity": "critical",\n          "location": "Slide 2",\n          "description": "7 bullets — exceeds maximum of 6",\n          "suggestion": "Split into 2 slides or reduce to 4-5 key points",\n          "confidence_score": 0.9\n        }},\n      ]\n    }}\n  ]\n}}\nUse severity as follows:\n- "critical": slide has > 6 bullets or > 95 words — hard structural violation (unless explicitly conveying a complex mathematical/logical theorem that demands it) — confidence > 0.90\n- "major": logical flow broken between adjacent slides, or title is genuinely uninformative — confidence > 0.85\n- "minor": tone slightly too formal, or a single bullet could be shorter — confidence > 0.80\nOmit any issue where you are not confident enough to meet the threshold for its severity.'
        content = await achat(self.model, [{'role': 'user', 'content': prompt}], temperature=0.2)
        return self._parse_slide_reviews('presentation_quality', content, slides)

    async def _evaluate_coherence(self, slides: List[SlideContent]) -> List[SlideReview]:
        slides_json = json.dumps([{'slide_number': i, 'slide_title': s.slide.slide_title, 'content': s.content} for (i, s) in enumerate(slides, 1)], indent=2)
        prompt = f'You are an expert editor evaluating the narrative coherence of lecture slides.\n\nSLIDES:\n{slides_json}\n\nEvaluate per slide based on logical flow from the PREVIOUS slide and to the NEXT slide:\n1. Is there an abrupt jump in topic without any transitional context?\n2. Are terms and concepts introduced smoothly?\n\nReturn ONLY valid JSON listing only slides WITH issues:\n{{\n  "slide_reviews": [\n    {{\n      "slide_number": 4,\n      "issues": [\n        {{\n          "severity": "major",\n          "location": "Slide 4",\n          "description": "Topic jumps suddenly from X to Y without transition",\n          "suggestion": "Add a transitional point explaining how X leads to Y",\n          "confidence_score": 0.9\n        }}\n      ]\n    }}\n  ]\n}}\nUse severity as follows:\n- "major": extreme logical leap rendering the flow broken — confidence > 0.85\n- "minor": slight thematic shift that could be smoothed — confidence > 0.80\nOmit any issue where you are not confident enough to meet the threshold for its severity.'
        content = await achat(self.model, [{'role': 'user', 'content': prompt}], temperature=0.2)
        return self._parse_slide_reviews('coherence', content, slides)

    def _parse_slide_reviews(self, criterion_name: str, content: str, slides: List[SlideContent]) -> List[SlideReview]:
        try:
            data = json.loads(content)
        except Exception:
            clean = content.strip()
            if clean.startswith('```json'):
                clean = clean.split('```json')[1].split('```')[0]
            elif clean.startswith('```'):
                clean = clean.split('```')[1].split('```')[0]
            try:
                data = json.loads(clean.strip())
            except Exception:
                return []
        valid_indices = set(range(1, len(slides) + 1))
        result: List[SlideReview] = []
        for entry in data.get('slide_reviews', []):
            if not isinstance(entry, dict):
                continue
            slide_number = int(entry.get('slide_number', -1))
            if slide_number not in valid_indices:
                continue
            slide = slides[slide_number - 1]
            issues: List[Issue] = []
            for item in entry.get('issues', []):
                if not isinstance(item, dict):
                    continue
                try:
                    severity = Severity(item.get('severity', 'minor').lower())
                except ValueError:
                    severity = Severity.MINOR
                issues.append(Issue(severity=severity, location=str(item.get('location', f'Slide {slide_number}')), description=str(item.get('description', '')), suggestion=str(item.get('suggestion', '')), confidence_score=float(item.get('confidence_score', 0.0))))
            result.append(SlideReview(slide_index=slide_number, slide_title=slide.slide.slide_title, criteria={criterion_name: CriterionResult(criterion=criterion_name, issues=issues)}))
        return result