"""Every content layout declares the RELATION it expresses, the data it needs and its capacity. The Bullet Writer is
offered the layouts whose contract the slide's content can satisfy, picks one (with the source sentences that prove the
relation) and writes to its text budget; `validate` re-checks the pick deterministically. No layout is excluded by name:
one whose data the slide cannot supply is simply not offered."""
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class LayoutSpec:
    name: str
    relation: str
    purpose: str
    min_blocks: int = 1
    max_blocks: int = 99
    unit: str = "points"            # what min/max count: "points" (one block per idea) or "facts" (one cell per sentence)
    title_chars: int = 70           # budget of a block title
    body_chars: int = 170           # budget of one bullet
    max_facts_per_block: int = 99   # layouts that show one label per block cannot carry details
    long_text: bool = False         # can show a verbatim definition of any length
    typed: str = ""                 # structure kind the Writer must supply (comparison, stats, quote, ...)
    ordered: bool = False           # blocks must follow the order of the source
    needs: str = ""                 # asset the layout is about: formula | table | images | code
    contract: str = ""              # what the Writer must return in `structure`


def _s(name, relation, purpose, lo=1, hi=99, **kw):
    return LayoutSpec(name, relation, purpose, lo, hi, **kw)


SPECS: Tuple[LayoutSpec, ...] = (
    _s("definition_callout_layout", "definition", "Formal definition of ONE concept, shown verbatim.", 1, 1, long_text=True),
    _s("key_points_layout", "quantitative_summary",
       "Numbered cards reserved for sourced quantitative summaries; each card carries an explicit value.",
       2, 6, unit="facts", typed="stats", long_text=True,
       contract='structure={"items":[{"fact_id":id,"value":exact sourced number,"label":meaning}]}'),
    _s("concept_breakdown_layout", "kinds", "2-6 concepts or kinds, each a term with a SHORT definition.", 2, 6,
       title_chars=40, body_chars=110),
    _s("comparison_matrix_layout", "comparison",
       "2-3 objects compared on the SAME criteria: columns are the objects, rows the criteria, each cell one sentence.",
       4, 15, unit="facts", body_chars=90, typed="comparison",
       contract='structure={"entities":[names],"rows":[{"criterion":label,"cells":[fact_id per entity]}]}; every fact in exactly one cell'),
    _s("comparison_columns_layout", "contrast", "Two labelled views side by side, each with a few short points.", 2, 2,
       body_chars=110, max_facts_per_block=4),
    _s("split_contrast_layout", "contrast", "Two independent views without shared criteria (pros/cons, theory/practice).",
       2, 2, body_chars=120, max_facts_per_block=5),
    _s("two_contents_in_a_slide_layout", "two_parts",
       "Two complementary parts, each with its own heading (problem/solution, definition/example).", 2, 2,
       body_chars=120, max_facts_per_block=5),
    _s("three_cols_content_layout", "three_parallel", "Exactly three parallel groups, each with its bullets.", 3, 3,
       body_chars=110),
    _s("grid_2x2_layout", "quadrants", "Exactly four parallel categories, or two dimensions crossed (a 2x2).", 4, 4,
       title_chars=40, body_chars=110, max_facts_per_block=2),
    _s("steps_horizontal_layout", "procedure", "An ORDERED procedure: each step an action and what happens.", 3, 8,
       title_chars=55, body_chars=90, ordered=True),
    _s("timeline_vertical_layout", "sequence", "A longer ordered sequence or chronology, each node a lead and a sentence.",
       3, 8, body_chars=120, ordered=True),
    _s("cycle_layout", "loop", "A repeating loop: the last stage leads back to the first. One short label per stage.", 3, 6,
       title_chars=45, body_chars=60, max_facts_per_block=1, ordered=True),
    _s("hub_spoke_layout", "hub", "One central concept with 3-6 components or kinds around it, one short label each.", 3, 6,
       title_chars=45, body_chars=60, max_facts_per_block=1,
       contract='structure={"center":short label of the central concept}'),
    _s("pyramid_layout", "levels", "Levels of a hierarchy from base to apex, one short line per level.", 3, 5,
       title_chars=22, body_chars=30, max_facts_per_block=1, ordered=True),
    _s("funnel_layout", "narrowing", "Stages that narrow step by step, one short line per stage.", 3, 5,
       title_chars=22, body_chars=30, max_facts_per_block=1, ordered=True),
    _s("checklist_layout", "criteria", "Conditions or criteria that must ALL hold.", 3, 8, body_chars=110),
    _s("spotlight_layout", "dominant", "One dominant idea plus at most three short supporting points.", 2, 4, unit="facts",
       body_chars=120),
    _s("takeaway_panel_layout", "lead_support", "One standout lead sentence with 2-5 supporting sentences under it.", 3, 6,
       unit="facts", body_chars=140, long_text=True),
    _s("big_statement_layout", "thesis",
       "A standalone principle or thesis: one large statement with up to three small supports.", 1, 4, unit="facts",
       body_chars=140),
    _s("highlight_list_layout", "catalog", "A dense catalogue of 6-10 items, each a label with a one-line detail.", 6, 10,
       title_chars=40, body_chars=100, max_facts_per_block=2),
    _s("icon_matrix_layout", "names", "An enumeration of up to 6 short names without explanation.", 3, 6,
       title_chars=40, body_chars=45, max_facts_per_block=1),
    _s("two_cols_content_layout", "long_list", "A long ungrouped list of short items split over two columns.", 7, 12,
       unit="facts", body_chars=90),
    _s("numbered_conclusions_layout", "summary", "Numbered conclusions or takeaways of a section (heading and body).", 3, 5,
       body_chars=130),
    _s("conclusion_cards_layout", "milestones", "3-4 major results, stages or milestones joined by a timeline.", 3, 4,
       title_chars=40, body_chars=100, ordered=True),
    _s("nested_bullets_layout", "hierarchy", "A two-level hierarchy: one or more parent ideas and their child points.", 1, 5,
       body_chars=130, long_text=True),
    _s("quote_layout", "quotation", "A verbatim quotation with who said it.", 1, 1, unit="facts", body_chars=110,
       typed="quote", contract='structure={"fact_id":id,"quote":exact words from that sentence,"attribution":who/where}'),
    _s("big_stat_layout", "key_figure", "1-3 dominant figures, each a number with a label.", 1, 3, unit="facts",
       body_chars=40, typed="stats", contract='structure={"items":[{"fact_id":id,"value":number as written in the sentence,"label":short}]} one item per fact'),
    _s("kpi_cards_layout", "key_figures", "2-4 headline figures side by side, each a number with a label.", 2, 4, unit="facts",
       body_chars=40, typed="stats", contract='structure={"items":[{"fact_id":id,"value":number as written in the sentence,"label":short}]} one item per fact'),
    _s("stats_cards_layout", "key_figures", "2-4 statistic cards, each a big number with a label.", 2, 4, unit="facts",
       body_chars=40, typed="stats", contract='structure={"items":[{"fact_id":id,"value":number as written in the sentence,"label":short}]} one item per fact'),
    _s("pricing_cards_layout", "options",
       "2-4 options or variants, each with ONE headline figure and a list of properties.", 2, 4, body_chars=90,
       typed="options", contract='structure={"cards":[{"name":label,"value":figure written in one of its sentences}]} one card per point'),
    _s("editorial_layout", "profile",
       "Profile of one object: a description plus key-value attributes (dataset size, classes, complexity, authors).", 4, 9,
       unit="facts", body_chars=90, typed="profile",
       contract='structure={"lede":fact_id,"pairs":[{"key":label,"value":text written in the sentence,"fact_id":id}]} lede + one pair per other fact'),
    _s("research_question_layout", "questions", "A central question with its sub-questions.", 3, 7, unit="facts",
       body_chars=140, typed="questions", contract='structure={"main":fact_id,"subs":[fact_ids]} all facts are questions'),
    # asset layouts: offered only when the slide carries that asset
    _s("only_content", "list", "Flat bullet list that shrinks to fit: the fallback for anything.", 1, 99, long_text=True),
    _s("table_above_layout", "table", "A table is the main evidence, with a few bullets under it.", needs="table",
       long_text=True),
    _s("formula_top_layout", "formula", "An equation is the centre of the slide and the points explain it.", needs="formula",
       long_text=True),
    _s("formula_below_layout", "formula_result", "An equation placed below the explanation that leads to it.", needs="formula",
       long_text=True),
    _s("data_table_layout", "table_only", "A table alone as the evidence (no bullets).", needs="table_data", long_text=True),
    _s("formula_annotated_layout", "formula_terms", "An equation whose points each explain one symbol or term.", 2, 99,
       needs="formula", body_chars=110),
    # layouts about pictures / source tables: chosen when those assets exist (image distribution / table extraction)
    _s("comparison_layout", "source_table", "A table of the document shown as it is (no bullets).", needs="table_only", long_text=True),
    _s("table_highlight_layout", "table_takeaway", "A table plus one takeaway callout.", needs="table_callout", long_text=True),
    _s("image_left_layout", "figure", "One figure with the explanation beside it.", needs="images", long_text=True),
    _s("image_right_layout", "figure", "One figure with the explanation beside it.", needs="images", long_text=True),
    _s("image_above_layout", "figure_wide", "One wide figure above its explanation.", needs="images", long_text=True),
    _s("image_below_layout", "figure_wide", "One wide figure below its explanation.", needs="images", long_text=True),
    _s("two_image_left_layout", "figures", "Two figures with the explanation beside them.", needs="images", long_text=True),
    _s("two_image_right_layout", "figures", "Two figures with the explanation beside them.", needs="images", long_text=True),
    _s("two_image_above_layout", "figures", "Two figures above their explanation.", needs="images", long_text=True),
    _s("two_image_below_layout", "figures", "Two figures below their explanation.", needs="images", long_text=True),
    _s("image_comparison_layout", "visual_comparison", "Two figures compared side by side (A/B).", needs="images"),
    # layouts about a code listing of the book: chosen when the slide shows no other asset
    _s("code_beside_layout", "code_walkthrough", "A code listing with its explanation beside it.", needs="code",
       long_text=True),
    _s("code_above_layout", "code_walkthrough_wide", "A wide code listing above its explanation.", needs="code",
       long_text=True),
    _s("chart_stat_layout", "distribution", "A chart of shares between categories plus one remark.", needs="images"),
    _s("image_stat_hero_layout", "visual_emphasis", "A full-bleed picture with one big figure over it.", needs="images"),
    _s("image_quote_hero_layout", "visual_emphasis", "A full-bleed picture with one quotation over it.", needs="images"),
)
LAYOUT_PURPOSE: Dict[str, LayoutSpec] = {spec.name: spec for spec in SPECS}
FALLBACKS = ("only_content",)  # ordinary prose never falls back to numbered cards


_NUMBER = re.compile(r"\d")
_QUOTED = re.compile(r"[\"“”]([^\"“”]{12,})[\"“”]")


def _facts_per_point(points: Sequence[dict]) -> int:
    return max((len(p["fact_ids"]) for p in points), default=0)


def _typed_possible(spec: LayoutSpec, points: Sequence[dict], facts: Sequence[dict]) -> bool:
    """Can the slide's own sentences supply the values this layout must show? (values must occur in the source)"""
    numeric = sum(1 for f in facts if _NUMBER.search(f["text"]))
    if spec.typed == "comparison":
        return len(points) >= 2
    if spec.typed == "quote":
        return any(_QUOTED.search(f["text"]) for f in facts)
    if spec.typed == "stats":
        return numeric >= len(facts) and len(facts) >= 1        # one figure per sentence
    if spec.typed == "options":
        return all(any(_NUMBER.search(facts_by_id(facts)[i]["text"]) for i in p["fact_ids"]) for p in points)
    if spec.typed == "profile":
        return numeric >= 3
    if spec.typed == "questions":
        return all(f["text"].rstrip().endswith("?") for f in facts)
    return True


def facts_by_id(facts: Sequence[dict]) -> Dict[str, dict]:
    return {f["fact_id"]: f for f in facts}


def candidates(points: Sequence[dict], facts: Sequence[dict], assets: Optional[Dict[str, bool]] = None) -> List[LayoutSpec]:
    """Layouts whose contract this slide's content can satisfy (deterministic; the Writer picks among them).
    `points`: [{claim, role, fact_ids}] in slide order; `facts`: [{fact_id, kind, text}]."""
    assets = assets or {}
    # A slide showing an equation must show it, and so on down: whichever asset the slide carries
    # decides which family of layouts can hold it. Images were missing from this chain, which is
    # why no image layout was ever reachable and they had to be forced on at export time.
    images = int(assets.get("images") or 0)
    # A table can be presented several ways, and which ones are honest depends on how much text
    # sits beside it: with no bullets the table can stand alone, with one it can carry a callout.
    if assets.get("formula"):
        allowed = {"formula"}
    elif assets.get("table"):
        allowed = ({"table_only", "table_data"} if not points
                   else {"table", "table_callout"} if len(points) == 1 else {"table"})
    elif images:
        allowed = {"images"}
    else:
        allowed = {""}
    n_points, n_facts, per_point = len(points), len(facts), _facts_per_point(points)
    longest_definition = max((len(f["text"]) for f in facts if f["kind"] == "definition"), default=0)
    out = []
    for spec in SPECS:
        if spec.needs not in allowed:
            continue                                        # assets decide: a slide with an equation shows the equation
        if spec.needs == "images" and _image_slots(spec) > images:
            continue                                        # never offer a two-picture layout for one picture
        count = n_points if spec.unit == "points" else n_facts
        if spec.needs in ("table_only", "table_data"):
            count = max(count, 1)                           # on these the table itself is the content
        if not spec.min_blocks <= count <= spec.max_blocks or per_point > spec.max_facts_per_block:
            continue
        if longest_definition > 2 * spec.body_chars and not spec.long_text:
            continue                                        # definitions stay verbatim: no room in a short-text layout
        if spec.name == "definition_callout_layout" and not (n_points == 1 and points[0]["role"] == "definition"):
            continue
        if spec.name == "big_statement_layout" and n_points != 1:
            continue
        if spec.typed and not _typed_possible(spec, points, facts):
            continue
        out.append(spec)
    if not any(s.name in FALLBACKS or s.needs for s in out):
        out.append(LAYOUT_PURPOSE["only_content"])
    visual_type = str(assets.get("visual_type") or "")
    native_families = {
        "native_process": {"steps_horizontal_layout", "timeline_vertical_layout", "cycle_layout",
                           "nested_bullets_layout"},
        "native_flow": {"steps_horizontal_layout", "timeline_vertical_layout", "funnel_layout",
                         "nested_bullets_layout"},
        "native_comparison": {"comparison_matrix_layout", "comparison_columns_layout",
                              "split_contrast_layout"},
        "native_concept_map": {"hub_spoke_layout", "concept_breakdown_layout",
                               "nested_bullets_layout"},
    }
    if visual_type in native_families:
        preferred = [s for s in out if s.name in native_families[visual_type]]
        if not preferred and points:
            preferred = [LAYOUT_PURPOSE["nested_bullets_layout"]]
        if preferred:
            # A native visual is the slide's primary asset, so every offered
            # layout must actually render it.  Keeping ``only_content`` in the
            # menu let a valid Writer answer violate the visual contract.
            out = preferred
    return out


def _image_slots(spec: LayoutSpec) -> int:
    """How many pictures a layout has room for, read from its own name."""
    return 2 if "two_image" in spec.name or spec.name.startswith("image_comparison") else 1


def catalog(specs: Sequence[LayoutSpec]) -> List[dict]:
    """The layout menu shown to the Writer (a fixed prefix per candidate set, so the API can cache it)."""
    return [{"layout": s.name, "relation": s.relation, "purpose": s.purpose,
             "text_budget": f"title <= {s.title_chars} chars, each bullet <= {s.body_chars} chars"
                            + ("" if s.long_text else " (no long text)"),
             **({"structure": s.contract} if s.contract else {})} for s in specs]


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\"“”'’]", "", str(text))).strip().casefold()


def _order_key(fact: dict) -> Tuple[int, int]:
    ref = (fact.get("source_refs") or [{}])[0]
    return int(ref.get("page") or 0), int(ref.get("start") or 0)


def validate(spec: LayoutSpec, structure: dict, evidence: Sequence[str], points: Sequence[dict], facts: Sequence[dict]
             ) -> Optional[str]:
    """None when the pick is sound, else the reason (fed back to the Writer once)."""
    by_id = facts_by_id(facts)
    if spec.ordered and len(points) >= 2:
        keys = [_order_key(by_id[p["fact_ids"][0]]) for p in points if p["fact_ids"]]
        if keys != sorted(keys):
            return f"{spec.name} needs points in the order the source states them, but they are not"
    if spec.relation in ("procedure", "sequence", "loop", "levels", "narrowing", "comparison", "contrast", "hub"):
        cited = [e for e in evidence if e in by_id]
        spanned = {p["claim"] for p in points for e in cited if e in p["fact_ids"]}
        if len(spanned) < 2:
            return f"{spec.name} ({spec.relation}) needs `evidence`: source sentences, from at least two points, that state this relation"
    if not spec.typed:
        return None
    if spec.typed == "comparison":
        entities, rows = structure.get("entities"), structure.get("rows")
        if not isinstance(entities, list) or not 2 <= len(entities) <= 3 or not isinstance(rows, list) or len(rows) < 2:
            return "comparison needs 2-3 entities and at least 2 criteria rows"
        placed = []
        for row in rows:
            cells = row.get("cells") if isinstance(row, dict) else None
            if not isinstance(cells, list) or len(cells) != len(entities) or not row.get("criterion"):
                return "every comparison row needs a criterion and one cell per entity"
            placed += cells
        if sorted(map(str, placed)) != sorted(by_id) or len(set(placed)) != len(placed):
            return "every fact must sit in exactly one comparison cell"
        return None
    if spec.typed == "stats":
        items = structure.get("items")
        if not isinstance(items, list) or sorted(str(i.get("fact_id")) for i in items if isinstance(i, dict)) != sorted(by_id):
            return "stats needs one item per fact"
        for item in items:
            if _norm(item.get("value", "")) not in _norm(by_id[item["fact_id"]]["text"]) or not item.get("value"):
                return f"stat value {item.get('value')!r} is not written in its sentence"
        return None
    if spec.typed == "quote":
        fact = by_id.get(str(structure.get("fact_id")))
        if not fact or not structure.get("quote") or _norm(structure["quote"]) not in _norm(fact["text"]):
            return "the quotation must be words written in the cited sentence"
        return None if structure.get("attribution") and _norm(structure["attribution"]) in _norm(fact["text"]) \
            else "the attribution must be written in the quoted sentence"
    if spec.typed == "options":
        cards = structure.get("cards")
        if not isinstance(cards, list) or len(cards) != len(points):
            return "one card per point is required"
        for card, point in zip(cards, points):
            if not card.get("value") or not any(_norm(card["value"]) in _norm(by_id[i]["text"]) for i in point["fact_ids"]):
                return f"card figure {card.get('value')!r} is not written in the point's sentences"
        return None
    if spec.typed == "profile":
        pairs, lede = structure.get("pairs"), by_id.get(str(structure.get("lede")))
        if not lede or not isinstance(pairs, list) or len(pairs) != len(by_id) - 1:
            return "profile needs a lede and one key-value pair per other sentence"
        for pair in pairs:
            fact = by_id.get(str(pair.get("fact_id")))
            if not fact or not pair.get("key") or _norm(pair.get("value", "")) not in _norm(fact["text"]):
                return f"profile value {pair.get('value')!r} is not written in its sentence"
        return None
    if spec.typed == "questions":
        subs = structure.get("subs")
        if str(structure.get("main")) not in by_id or not isinstance(subs, list) or len(subs) != len(by_id) - 1:
            return "questions needs a main question and every other sentence as a sub-question"
        return None
    return None
