from typing import TypedDict, List, Dict, Any, Optional, Set

class MultimodalState(TypedDict):
    lecture_id: str
    lecture_dict: Dict[str, Any]
    aggregated_media: Dict[str, Any]
    used_tables: Set[str]
    used_images: Set[str]
    image_distributions: List[Dict[str, Any]]
    table_distributions: List[Dict[str, Any]]