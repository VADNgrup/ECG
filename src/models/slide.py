from typing import Optional, List, Dict, Union
from dataclasses import dataclass, field
from enum import Enum

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

@dataclass
class Slide:
    slide_title: str
    slide_type: SlideType
    goal: str
    slide_number: int = -99
    table: Optional[Table] = None
    latex_block_formula: Optional[str] = None

@dataclass
class SlideContent:
    slide: Slide
    content: Union[str, List[str], Dict[str, List[str]]] = field(default_factory=list)
    layout_hint: Optional[str] = None