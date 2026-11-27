"""The state the four agents share, as the paper's W = <D, S, R>: D the parsed document (`document_context`), S the slide
deck -- one entry per slide holding its specification s_i, evidence packet e_i and written bullets b_i (`deck`) -- and R
the Content QA's audit (`review`). Each agent reads the deck from the state and writes its own part of every entry; the
other fields are the deck as the lecture file stores it."""
from typing import Any, Dict, List, Optional, TypedDict

from src.models.context import DocumentContext
from src.models.slide import Slide, SlideContent


class SlideEntry(TypedDict):
    """One slide of the deck, (s_i, e_i, b_i): the Outline writes `plan`, the Packet Builder `spec`, `packet` and
    `built`, the Writer `content`, the Content QA corrects `content`."""
    slide_id: Optional[str]
    chapter: Any                         # the chapter (scope root) the slide belongs to
    plan: Optional[Dict[str, Any]]       # the Outline's plan: title, message, paragraphs, main ideas
    spec: Optional[Slide]                # s_i: the slide's specification
    packet: Optional[Dict[str, Any]]     # e_i: its evidence packet, as the lecture file stores it
    built: Optional[Dict[str, Any]]      # the assembled evidence behind the packet (pool, assets, refs); None on openers
    content: Optional[SlideContent]      # b_i: its written bullets


class Review(TypedDict):
    """R: the Content QA's audit of the deck; the QA Router reads `status`."""
    status: str                          # failed (slides go back to the Writer) | passed | passed_with_warnings
    feedback: List[Dict[str, Any]]       # the slides sent back: {slide_id, slide, issues}
    round: int                           # audit rounds run (r); the Writer repairs at most QA_MAX_ROUNDS times
    pending: Dict[str, List[str]]        # what the Writer is to fix, by slide_id
    audit: Dict[str, Any]                # QA's own record across rounds (report, settled bullets, slides to check)


class WorkflowState(TypedDict):
    document_context: DocumentContext    # D
    deck: List[SlideEntry]               # S
    review: Optional[Review]             # R
    lecture_title: str
    slides: List[SlideContent]
    planned_specs: Optional[List[Slide]]
    slide_specs: Optional[List[Slide]]
    slide_packets: Optional[List[Dict[str, Any]]]
    qa_report: Optional[Dict[str, Any]]
    root_heading: Optional[str]
    domain: Optional[str]
    scope_manifest: Optional[Dict[str, Any]]
    unit_plans: Optional[List[Dict[str, Any]]]
    labels: Optional[Dict[str, str]]
    numbered: Optional[Dict[str, List[int]]]   # numbers the scope's text gives figures / tables ("Table 1")
