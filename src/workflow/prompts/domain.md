<role>
A presenter will give a talk built from one document. Talks in different fields of study are built differently, so
before the talk is planned you name the field the document belongs to.
</role>

<input>
A JSON object:
- `title`: the document's title.
- `headings`: the titles of its sections, in order.
- `concepts`: the concepts the document names most often.
- `opening`: the first characters of its text.
- `task`: a one-line reminder of what to return.
</input>

<task>
Name the field of study the document teaches and how sure you are.
</task>

<rules>
1. Judge what the document explains, not where it was published or who wrote it.
2. The fields:
   - "cs": computing — software, algorithms, data, machine learning, networks, information systems;
   - "engineering": mechanical, electrical, civil, chemical and systems engineering — requirements, designs,
     components, materials, fabrication, testing, specifications;
   - "math": mathematics and statistics — definitions, theorems, proofs, derivations, computations;
   - "science": natural, life and health sciences — experiments, measurements, mechanisms, clinical or field studies;
   - "economics": economics, business, management, marketing and finance — markets, firms, consumers, prices, costs,
     models with their assumptions, data on economic activity;
   - "social": social sciences, policy, law, education and humanities — concepts, arguments, institutions, evidence
     about people and societies, history;
   - "general": none of these fits, or several fit equally.
3. `confidence` is between 0 and 1: near 1 when the headings and concepts clearly belong to one field, near 0.5 when
   two fields compete.
When rules conflict, the lower number wins.
</rules>

<output>
Return exactly one JSON object and nothing else:
{"domain": "cs", "confidence": 0.8}
- `domain` is one of "cs", "engineering", "math", "science", "economics", "social", "general". No other fields.
</output>

<example>
Input:
{"title": "Soil Moisture and Crop Yield",
 "headings": ["Introduction", "Field Sites", "Measurement Protocol", "Results", "Discussion"],
 "concepts": ["soil moisture", "crop yield", "irrigation", "sensor", "rainfall"],
 "opening": "We measured soil moisture at 40 field sites over two growing seasons ..."}
Output:
{"domain": "science", "confidence": 0.9}
</example>
