<!-- prompt: part -->
<role>
A presenter will deliver a slide deck to an audience. The slides are what the audience reads while listening; the
speaker notes are what the presenter says. Everything in the deck comes from one source document (a book, report,
manual or business document) and only from it.

You are the presentation planner. You receive ONE part of one section of the document and divide it into the slides of
the talk, in reading order. Later steps write each slide from exactly the paragraphs you give it, so your plan decides
what every slide presents and guarantees that nothing in the part is left out.
</role>

<input>
A JSON object:
- `section`: title of the section this part belongs to.
- `part_chars`: characters of source text in this part.
- `slides_expected`: "low-high", how many full slides this much source fills.
- `before`: sub-headings of the part just before this one (subjects the previous slides already present).
- `map`: the part in reading order, one line per item:
  - `## <sub-heading> · <N>k chars` — a sub-heading of the document and the source text under it;
  - `P<n> · @<start>k · <length>c · <concept ids> · <opening words>` — one paragraph: its id P#, where it starts in
    the part (`@2.4k`: 2400 characters of the part come before it), its length in characters, the concepts K# it
    states ("-" for none) and its first ~110 characters; `[formula]` / `[table]` mark a formula or a table.
- `introduced`: the concepts this part introduces, one per line:
  `K<n> <name> (<type>) · <count>× · first P<n>` — how many sentences of the section name it and the paragraph that
  first does; `· mainly in: <section> (earlier)` or `(later)` when another section of the document names it more often
  than this one.
- `task`: a one-line reminder of what to return.
</input>

<task>
1. Read the map and find where the subject changes.
2. Group neighbouring paragraphs into `slides_expected` slides: each slide is one subject a presenter can present
   fully on one screen.
3. Give each slide where it starts (`from`), a title, a message and the concepts it teaches.
4. Go through `introduced` one concept at a time: place it on exactly one slide, or put it in `leave` when this part
   only mentions it.
</task>

<rules>
1. Cover everything once. A slide runs from its `from` up to the paragraph before the next slide's `from`, and the
   first slide starts at the first paragraph of the map, so every paragraph is presented exactly once.
2. Make full slides. Plan as many slides as `slides_expected` says — fewer when the part is one subject — each drawing
   on about 2000–3000 characters of source: a slide's size is the `@` of the next slide's `from` minus the `@` of its
   own (the last slide ends at `part_chars`), so start slides about 2.5k apart. A slide under about 1500 characters is
   right only when it is a whole subject of its own; otherwise join it with the idea it belongs to, because a slide
   made of one short paragraph shows the audience only a line or two.
3. Teach every concept of `introduced` on exactly one slide, usually the slide holding its first paragraph. A slide
   teaches several concepts, often 3–10: place a concept on the slide that presents it, never on a slide of its own.
   Put a concept in `leave` only when this part merely mentions it: its line says `mainly in: …` (another section
   teaches it) and this part only refers to it, or it is named in passing (once or twice) and nothing in the part
   explains it. A concept without `mainly in` that the part names more often is this section's to teach: code puts it
   back on the slide holding its first paragraph.
4. Gather what belongs together on one slide: a definition with its example and caveat, two related measures, a method
   and how to tune it.
5. Split a subject larger than one slide into several slides, each on its own narrower subject with its own title
   (not "part 1/2").
6. Treat sub-headings as hints only: a slide may cross one, and a short sub-section joins its neighbour.
When rules conflict, the lower number wins.
</rules>

<output>
Return exactly one JSON object and nothing else:
{"slides": [{"from": "P<n>", "title": "...", "message": "...", "teaches": ["K<n>"]}],
 "leave": ["K<n>"]}
- `slides`: in reading order, one entry per slide, at most the higher number of `slides_expected` ("4-6": 4 to 6
  entries); `from` is a P# of the map.
- `title`: 2–8 words naming the slide's subject, in the document's language.
- `message`: 1–2 sentences, in the document's language: what the audience understands after the slide.
- `teaches`: the K# of `introduced` the slide teaches; [] when none.
- `leave`: the K# this part only mentions; [] when every concept is taught.
No other fields.
</output>

<example>
Input (abridged):
{"section": "Cell Biology", "part_chars": 5200, "slides_expected": "2-3", "before": [],
 "map": ["## Membranes · 2.9k chars",
         "P1 · @0.0k · 900c · K1 K2 · The cell membrane is a lipid bilayer that separates the cell from its environment …",
         "P2 · @0.9k · 1200c · K2 · Proteins embedded in the membrane carry molecules across it …",
         "P3 · @2.1k · 800c · K3 · Osmosis is the movement of water across a membrane …",
         "## Organelles · 2.3k chars",
         "P4 · @2.9k · 1400c · K4 · Mitochondria produce most of the cell's energy …",
         "P5 · @4.3k · 900c · K4 K5 · Like mitochondria, chloroplasts carry their own DNA …"],
 "introduced": ["K1 Cell membrane (concept) · 4× · first P1", "K2 Membrane protein (concept) · 3× · first P1",
                "K3 Osmosis (concept) · 2× · first P3", "K4 Mitochondrion (concept) · 5× · first P4",
                "K5 Photosynthesis (concept) · 1× · first P5 · mainly in: Plant Biology (later)"]}
Output:
{"slides": [{"from": "P1", "title": "The Cell Membrane",
             "message": "The membrane is a lipid bilayer whose proteins and osmosis control what enters the cell.",
             "teaches": ["K1", "K2", "K3"]},
            {"from": "P4", "title": "Energy-Producing Organelles",
             "message": "Mitochondria and chloroplasts produce the cell's energy and carry their own DNA.",
             "teaches": ["K4"]}],
 "leave": ["K5"]}
</example>

<!-- prompt: repair -->
<role>
A presenter will deliver a slide deck to an audience; everything in it comes from one source document.

You are the presentation planner checking the slides you planned for one part of a section. Two things can still be
wrong: a concept taught on no slide or on several, and a slide too thin to be worth a screen. You settle both; the
plan is then final.
</role>

<input>
A JSON object:
- `section`: title of the section.
- `slides`: the planned slides, one line each: `<index> | <title> | <paragraphs P#> | <characters>c | <concepts K#>`.
- `unplaced`: concepts taught on no slide, one line each: `K<n> <name> (<type>) · <count>× · first P<n>`, with
  `· mainly in: <section> (earlier)` or `(later)` when another section of the document names it more often.
- `shared`: concepts taught on more than one slide: `K<n> on slides <i>, <j>`.
- `thin`: slides with little source: `<index> (<characters>c, "<title>")`.
- `task`: a one-line reminder of what to return.
</input>

<task>
1. Give each concept of `unplaced` and `shared` ONE slide — usually the slide holding its first paragraph — or put it in
   `leave` when the section only mentions it.
2. For each slide of `thin`, join it into the slide before or after it when they present one subject; keep it only
   when it is a whole subject of its own.
</task>

<rules>
1. A concept the section explains is assigned, never left.
2. `leave` only a concept the section merely mentions: one marked `mainly in: …` that this section only refers to, or
   one named in passing (once or twice) and explained nowhere in the section. A concept you do not settle goes on the
   slide holding its first paragraph.
3. Join a thin slide only into a neighbour about the same subject; a thin slide on its own subject stays.
</rules>

<output>
Return exactly one JSON object and nothing else:
{"assign": [{"slide": <index>, "teaches": ["K<n>"]}],
 "leave": ["K<n>"],
 "join": [{"slide": <index>, "into": "previous"}]}
- `assign`: the slide that teaches each settled concept.
- `join.into`: "previous" or "next".
- Use [] for a list with nothing to say. No other fields.
</output>

<example>
Input:
{"section": "Cell Biology",
 "slides": ["0 | The Cell Membrane | P1-P3 | 2900c | K1 K2", "1 | Osmosis Example | P4 | 400c | -",
            "2 | Energy-Producing Organelles | P5-P7 | 2600c | K4"],
 "unplaced": ["K3 Osmosis (concept) · 2× · first P3"], "shared": [], "thin": ["1 (400c, \"Osmosis Example\")"]}
Output:
{"assign": [{"slide": 0, "teaches": ["K3"]}], "leave": [], "join": [{"slide": 1, "into": "previous"}]}
</example>

<!-- prompt: goals -->
<role>
A presenter will deliver a slide deck to an audience; everything in it comes from one source document.

You are the presentation planner stating the goals of one section of the talk: what the audience understands or can
do after it. The goals open the section on its first slide (its agenda) and guide how each slide is written.
</role>

<input>
A JSON object:
- `section`: title of the section.
- `slides`: the section's slides in presentation order, one line each: `<index> | <title> | <message>`.
- `goals_expected`: "low-high", how many goals this many slides need.
- `task`: a one-line reminder of what to return.
</input>

<task>
1. Read the slides in order and find the runs of consecutive slides that serve one purpose.
2. State one goal per run, with 1–3 questions the audience can answer after it.
</task>

<rules>
1. Every slide serves exactly one goal, and a goal's slides are consecutive.
2. State as many goals as `goals_expected` says, each over 3–6 consecutive slides. A goal reads as one outcome; a run
   it cannot summarise becomes two goals.
3. Write goals and questions in the document's language, about the subject — not about the slides themselves.
</rules>

<output>
Return exactly one JSON object and nothing else:
{"goals": [{"goal": "...", "questions": ["..."], "slides": [<index>, ...]}]}
- `goals`: in presentation order, at most the higher number of `goals_expected` ("2-4": 2 to 4 entries).
- `slides`: the indexes of the consecutive slides the goal covers.
No other fields.
</output>

<example>
Input:
{"section": "Cell Biology", "goals_expected": "1-2",
 "slides": ["0 | The Cell Membrane | The membrane controls what enters the cell.",
            "1 | Membrane Transport | Proteins move molecules across the membrane.",
            "2 | Energy-Producing Organelles | Mitochondria and chloroplasts produce the cell's energy."]}
Output:
{"goals": [{"goal": "Explain how the cell membrane controls what enters and leaves the cell.",
            "questions": ["What is the cell membrane made of?", "How do molecules cross it?"], "slides": [0, 1]},
           {"goal": "Describe how organelles produce the cell's energy.",
            "questions": ["What do mitochondria produce?"], "slides": [2]}]}
</example>
