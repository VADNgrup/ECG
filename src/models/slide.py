from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union

class SlideType(Enum):
    CONTENT = 'content'
    HAVE_TABLE = 'have_table'
    HAVE_FORMULA = 'have_formula'
    COMPARISON = 'comparison'
    TWO_SUB_CONTENTS = 'two_sub_contents'

@dataclass
class Table:
    table_markdown: str
    table_caption: str
    structured_table: Optional[Dict] = None

@dataclass
class Slide:
    slide_title: str
    slide_type: SlideType
    goal: str
    slide_number: int = -99
    table: Optional[Table] = None
    latex_block_formula: Optional[str] = None
    # every formula the slide draws, stacked in one box (the first is latex_block_formula)
    latex_formulas: List[str] = field(default_factory=list)
    # a code listing of the book shown on the slide (no other asset): {"code": text, "language": name}
    code_block: Optional[Dict] = None
    kg_node_id: Optional[int] = None
    # a chapter's opener: rendered as its section divider, not a content slide
    is_section_opener: bool = False
    # the goal the slide serves (the chapter's title for an opener)
    section_heading: str = ""
    # names of the KG nodes the slide's packets teach
    kg_concepts: List[str] = field(default_factory=list)
    grounding: Dict = field(default_factory=dict)
    slide_id: str = ""

@dataclass
class SlideContent:
    slide: Slide
    content: Union[str, List[str], Dict[str, List[str]]] = field(default_factory=list)
    layout_hint: Optional[str] = None
    fact_ids: List[List[str]] = field(default_factory=list)
    groups: List[Dict] = field(default_factory=list)
    bullet_sources: List[List[Dict]] = field(default_factory=list)
    speaker_notes: List[Dict] = field(default_factory=list)
    layout_reason: str = ""
    layout_structure: Dict = field(default_factory=dict)
