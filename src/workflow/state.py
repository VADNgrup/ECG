from typing import Any, Dict, List, Optional, TypedDict

from src.models.context import DocumentContext
from src.models.slide import Slide, SlideContent

class WorkflowState(TypedDict):
    document_context: DocumentContext
    lecture_title: str
    slides: List[SlideContent]
    planned_specs: Optional[List[Slide]]
    slide_specs: Optional[List[Slide]]
    slide_packets: Optional[List[Dict[str, Any]]]
    qa_report: Optional[Dict[str, Any]]
    root_heading: Optional[str]
    scope_manifest: Optional[Dict[str, Any]]
    unit_plans: Optional[List[Dict[str, Any]]]
    deck_plan: Optional[Dict[str, Any]]
