"""Authoritative native-PPTX layout contract.

The names intentionally mirror the public layout methods from the former
``deck_html_layout_manager.py``.  The HTML/Fabric renderer is retired; this registry only
preserves its stable layout vocabulary and validates how those layouts may be used by the
native PPTX exporter.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LayoutRule:
    content: bool = True
    image_count: tuple[int, int] = (0, 0)
    special_only: bool = False


# Public layout names from the legacy catalog.  Cover/TOC/end layouts are kept so the
# manifest has one vocabulary, but content generation is restricted to CONTENT_LAYOUTS.
LAYOUT_RULES: dict[str, LayoutRule] = {
    "only_content": LayoutRule(),
    "comparison_layout": LayoutRule(),
    "table_above_layout": LayoutRule(),
    "two_contents_in_a_slide_layout": LayoutRule(),
    "formula_top_layout": LayoutRule(),
    "code_beside_layout": LayoutRule(),
    "code_above_layout": LayoutRule(),
    "formula_below_layout": LayoutRule(),
    "formula_annotated_layout": LayoutRule(),
    "image_left_layout": LayoutRule(image_count=(1, 1)),
    "image_right_layout": LayoutRule(image_count=(1, 1)),
    "image_above_layout": LayoutRule(image_count=(1, 1)),
    "image_below_layout": LayoutRule(image_count=(1, 1)),
    "two_image_right_layout": LayoutRule(image_count=(2, 2)),
    "two_image_left_layout": LayoutRule(image_count=(2, 2)),
    "two_image_above_layout": LayoutRule(image_count=(2, 2)),
    "two_image_below_layout": LayoutRule(image_count=(2, 2)),
    "two_cols_content_layout": LayoutRule(),
    "definition_callout_layout": LayoutRule(),
    "big_statement_layout": LayoutRule(),
    "steps_horizontal_layout": LayoutRule(),
    "key_points_layout": LayoutRule(),
    "three_cols_content_layout": LayoutRule(),
    "split_contrast_layout": LayoutRule(),
    "conclusion_cards_layout": LayoutRule(),
    "numbered_conclusions_layout": LayoutRule(),
    "grid_2x2_layout": LayoutRule(),
    "research_question_layout": LayoutRule(),
    "agenda_layout": LayoutRule(special_only=True),
    "stats_cards_layout": LayoutRule(),
    "quote_layout": LayoutRule(),
    "section_divider_layout": LayoutRule(content=False, special_only=True),
    # Fullscreen image is never legal for an ordinary content slide.
    "image_fullscreen_overlay_layout": LayoutRule(content=False, image_count=(1, 1), special_only=True),
    "editorial_layout": LayoutRule(),
    "nested_bullets_layout": LayoutRule(),
    "comparison_columns_layout": LayoutRule(),
    "comparison_matrix_layout": LayoutRule(),
    "cycle_layout": LayoutRule(),
    "hub_spoke_layout": LayoutRule(),
    "pyramid_layout": LayoutRule(),
    "funnel_layout": LayoutRule(),
    "kpi_cards_layout": LayoutRule(),
    "big_stat_layout": LayoutRule(),
    "chart_stat_layout": LayoutRule(),
    "table_highlight_layout": LayoutRule(),
    "image_comparison_layout": LayoutRule(image_count=(2, 2)),
    "image_stat_hero_layout": LayoutRule(image_count=(1, 1)),
    "image_quote_hero_layout": LayoutRule(image_count=(1, 1)),
    "concept_breakdown_layout": LayoutRule(),
    "timeline_vertical_layout": LayoutRule(),
    "checklist_layout": LayoutRule(),
    "takeaway_panel_layout": LayoutRule(),
    "spotlight_layout": LayoutRule(),
    "highlight_list_layout": LayoutRule(),
    "icon_matrix_layout": LayoutRule(),
    "data_table_layout": LayoutRule(),
    "pricing_cards_layout": LayoutRule(),
    "cover_split_layout": LayoutRule(content=False, special_only=True),
    "toc_layout": LayoutRule(content=False, special_only=True),
    "toc_vertical_layout": LayoutRule(content=False, special_only=True),
    "toc_described_layout": LayoutRule(content=False, special_only=True),
    "toc_cards_layout": LayoutRule(content=False, special_only=True),
    "end_layout": LayoutRule(content=False, special_only=True),
    "end_with_image_layout": LayoutRule(content=False, image_count=(1, 1), special_only=True),
    "end_image_hero_layout": LayoutRule(content=False, image_count=(1, 1), special_only=True),
}

CONTENT_LAYOUTS = frozenset(name for name, rule in LAYOUT_RULES.items()
                            if rule.content and not rule.special_only)
IMAGE_CONTENT_LAYOUTS = frozenset(name for name, rule in LAYOUT_RULES.items()
                                  if rule.content and rule.image_count[1] > 0)

FAMILY_TO_LAYOUT = {
    "narrative": "only_content",
    "cards": "conclusion_cards_layout",
    "columns": "two_cols_content_layout",
    "comparison": "comparison_layout",
    "steps": "steps_horizontal_layout",
    "timeline": "numbered_conclusions_layout",
    "numbered": "numbered_conclusions_layout",
    "cycle": "steps_horizontal_layout",
    "hierarchy": "nested_bullets_layout",
    "hub": "concept_breakdown_layout",
    "hub_spoke": "concept_breakdown_layout",
    "matrix": "grid_2x2_layout",
    # legacy names produced by the pre-grounding writer
    "only_content": "only_content",
    "two_cols": "two_cols_content_layout",
}


def normalize_layout(layout_id: str | None, *, special: bool = False) -> str:
    """Return a registered legacy layout name or raise; never silently invent a fallback."""
    raw = str(layout_id or "only_content").strip()
    name = FAMILY_TO_LAYOUT.get(raw, raw)
    rule = LAYOUT_RULES.get(name)
    if rule is None:
        raise ValueError(f"Unknown layout: {raw!r}")
    if special:
        if not rule.special_only:
            raise ValueError(f"Layout {name!r} is not a special-slide layout")
    elif rule.special_only or not rule.content:
        raise ValueError(f"Layout {name!r} is not permitted for a content slide")
    return name


def layout_for_content(layout_id: str | None, image_count: int = 0) -> str:
    """Validate content layout and integrate verified images on the same page."""
    name = normalize_layout(layout_id)
    if image_count >= 2:                     # the Writer's two-figure layout with text stands; any other is the default
        return name if name.startswith("two_image_") else "two_image_right_layout"
    if image_count == 1:
        return "image_right_layout"
    if name in IMAGE_CONTENT_LAYOUTS:
        # An image layout without a verified image is invalid, not a placeholder request.
        raise ValueError(f"Layout {name!r} requires a verified document image")
    return name

