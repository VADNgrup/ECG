from typing import TypedDict, List, Dict, Any, Optional
from src.models.context import DocumentContext
from src.models.slide import SlideContent, Slide

class WorkflowState(TypedDict):
    document_context: DocumentContext
    lecture_title: str
    slides: List[SlideContent]
    slide_specs: Optional[List[Slide]]
    slide_packets: Optional[List[Dict[str, Any]]]
    qa_report: Optional[Dict[str, Any]]
    revision_count: Optional[int]
