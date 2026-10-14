# Vendored MiniRAG — local edits

MiniRAG (HKUDS, https://github.com/HKUDS/MiniRAG) is vendored into the repo and installed editable
(`pip install -e ./MiniRAG` into the `slide` conda env) so the slide pipeline can drive it as its KG
backend (replacing RAGAnything/LightRAG) and edit its source directly.

Upstream: `HKUDS/MiniRAG` @ `e204d23` (branch `main`), vendored into this repository as plain files on 2026-09-30 — a
clone of the repo carries it with the edits below, and `pip install -r requirements.txt` installs it (`-e ./MiniRAG`).
Only the package and its install files are kept (`minirag/`, `pyproject.toml`, `setup.py`, `requirements.txt`,
`README.md`, `LICENSE` — MIT); the upstream datasets, reproduction scripts and docs are left out. PyPI
`minirag-hku==0.0.2` is broken (won't import), so the source is the only way to install it.

## Local edits vs upstream

- `minirag/prompt.py` — `PROMPTS["DEFAULT_ENTITY_TYPES"]`
  - **was:** `["organization", "person", "location", "event"]` (LiHua-World chat benchmark)
  - **now:** `["concept", "code", "person", "organization", "location", "event", "work",
    "metric", "time", "group", "product", "dataset"]` (the pipeline's document-agnostic entity set, so the KG is
    built of CONCEPTS for a technical document, not only orgs/people/events). `"term"` was merged into
    `concept`; `"code"` added; **`"method"` removed (2026-09-08)** — algorithms/models are now `concept`
    too (the method/concept split was inconsistent: the LLM typed the same entity both ways, e.g.
    `Semi-supervised Learning`=method vs `Semisupervised Learning`=concept). `_norm_type` auto-clamps any
    legacy/out-of-vocab `method` → `concept`; `entity_extraction` prompt updated to say "concept = idea,
    technique, algorithm, model, or defined term (no separate method type)" + a canonical-naming rule
    ("do NOT append filler like 'Algorithm'/'Method'": `Supervised Learning`, not
    `Supervised Learning Algorithm`). Existing KBs are back-filled method→concept by
    `src/rag/kg_dedup.py::clean_kb`.
  - MiniRAG reads entity types from `addon_params["entity_types"]` when provided by the pipeline and
    falls back to this constant for standalone use. Parsing and prompt rendering share the same list.

- `minirag/prompt.py` — `PROMPTS["entity_extraction"]` replaced with a **concept-oriented** version.
  - Goal now prioritises CONCEPTS/methods/metrics/terms over incidental people/orgs, and the two worked
    examples are technical (Gradient Descent / Precision-Recall) instead of the stock fiction
    (Alex/Taylor). Format is IDENTICAL (record structure, `<|>`/`##`/`<|COMPLETE|>` delimiters, the five
    `{...}` placeholders) so `operate.extract_entities`'s parser is unaffected.
  - **2026-09-22: split-attribution fix.** Audit of a real KB (`Hands_On_Machine_Learning_..._p23-100`)
    found the LLM merging several DISTINCT, separately-introduced definitions of one term (e.g. "Machine
    Learning" per the source, plus definitions attributed to "Arthur Samuel, 1959" and "Tom Mitchell,
    1997") into a SINGLE concept node's description, and never extracting the two named people at all —
    the Goal line's "incidental named people" phrasing was suppressing them even though they are the
    credited authors of a definition, not incidental. Fixed: Goal line now carves out named
    authors/sources of a quoted definition/finding/method/result as never incidental; added a SPLITTING
    RULE (each separately-introduced definition of a term -> its own `concept` entity, named
    `"<Term> (<Name>'s definition)"` when attributed) and an ATTRIBUTION RULE (the named author is always
    a `person`/`organization` entity, linked by a "defines" relationship) under Step 1; added Example 3
    (Overfitting / Priya Rao, invented, not copied from any source) demonstrating the pattern. No format
    change, no `DEFAULT_ENTITY_TYPES` change (`concept`/`person` already cover it).

- `minirag/utils.py` — `clean_str()` now (a) strips surrounding quotes (`result.strip('"')`), and (b)
  strips a leaked `"<|...|>"` token and everything after it (`re.sub(r"\s*<\|.*$", "", result)`). The LLM
  sometimes emits a spurious `<|diff_marker|>` inside a field, mashing two entities into one name
  ("Adam Optimizer<|diff_marker|>Learning Rate"); stripping recovers the valid leading name. The record
  delimiters (`<|>`, `<|COMPLETE|>`) are consumed by the splitter before clean_str, so a residual `<|` is
  always garbage.
- `minirag/operate.py` — `_merge_edges_then_upsert` no longer creates relation-endpoint stubs. When either
  endpoint was not extracted as a typed entity, the relationship is dropped and counted. This prevents
  all `unknown` nodes, including garbage such as
  "The Adam optimizer employs the learning rate…" (a sentence used as an entity name). Real entities (incl.
  long `work`/citation titles) come through `_handle_single_entity_extraction` with their own type and are
  unaffected. `extract_entities` then filters the `None` these skips return before building the rel vdb.
  - The GraphRAG-style extraction prompt wraps every value in double quotes, so entity_name/entity_type/
    description all arrive quoted. Upstream MiniRAG keeps the quotes (LightRAG strips them), which leaked
    `'"CONCEPT"'` / `'"Feature Engineering"'` onto slides and broke entity_type matching.

- `minirag/llm/openai.py` — **retry-decorator placement bug fixed (2026-09-09)**: upstream had
  `@retry(...)` sitting on `class GPTKeywordExtractionFormat(BaseModel)` (never called) instead of the
  actual `async def openai_complete_if_cache(...)` right below it — the real LLM-call function had NO
  retry at all on transient connection/timeout errors, so any hiccup against a local Ollama server (or
  a flaky cloud endpoint) failed hard instead of retrying. Moved the decorator onto the function. Also
  added an explicit `timeout=120.0` to both `AsyncOpenAI(...)` client constructions in this file (the
  SDK default allows a call to hang up to 600s with zero output, which on a queued local Ollama server
  looked identical to a silent freeze).
- `minirag/llm/openai.py` — two further edits:
  - accepts an optional `token_tracker` kwarg (popped; after a non-stream response calls
    `token_tracker.add_usage({prompt_tokens, completion_tokens, total_tokens})`) so `build_kg._make_llm_func`
    logs real per-call tokens into the pipeline's shared token log.
  - **LLM RESPONSE CACHE**: upstream `openai_complete_if_cache` did `kwargs.pop("hashing_kv", None)` — i.e.
    it *discarded* the `llm_response_cache` MiniRAG passes, so entity extraction was NEVER cached and every
    rebuild re-paid the LLM. Now it KEEPS `hashing_kv` and, for non-stream/non-structured calls, looks up
    `compute_args_hash(model, messages)` and returns the stored completion on a hit, else stores it after
    the call. Persisted by `MiniRAG._insert_done` -> `kv_store_llm_response_cache.json`. Verified: a 2nd
    build reusing the cache = **0 LLM calls** (only free local embeddings recompute). Lets a rebuild after
    changing operate.py parsing/merge be free.

- `minirag/operate.py` — KG-quality fixes (audit found: 27% of node descriptions had `<SEP>` leaking in,
  ALL-CAPS names/types, `"UNKNOWN"` stubs, delimiter-garbage + out-of-vocab types, strength-number leaking
  into edge keywords). Added 3 helpers (`_norm_type`, `_merge_descriptions`, `_clean_keywords`) and:
  - **Removed `.upper()`** on entity name + relationship source/target → Title Case names (like LightRAG;
    the extraction prompt already says "capitalized the name"). Applied consistently so relations still link.
  - **`entity_type = _norm_type(...)`** — lowercase, strip delimiter/quote junk, CLAMP to
    `DEFAULT_ENTITY_TYPES` (out-of-vocab / `"UNKNOWN"` / empty → `concept`).
  - **`description = _merge_descriptions(...)`** for both entity + edge merge (was
    `GRAPH_FIELD_SEP.join(sorted(set(...)))` → a hub like TensorFlow had 73 `<SEP>`-joined copies, 10k+
    chars). Now: dedup sentences, keep ≤6 distinct, join with space. Deterministic (no LLM) so a rebuild
    reusing the response cache stays free. (MiniRAG's own `_handle_entity_relation_summary` LLM path is
    still commented out; enable it for true summarization at extra LLM cost.)
  - **`keywords = _clean_keywords(...)`** on edge merge — drop the relationship-strength number that leaks
    into the keywords field on malformed records, and any `<SEP>`.
  - Relation endpoints missing a real entity record are now dropped rather than persisted as
    `unknown`; the finished graph schema therefore contains no placeholder type.

- `minirag/operate.py` — `path2chunk` (mini-mode query) guard: skip a reasoning-path node whose
  `get_node` returned `None` or whose `description`/`source_id` is empty, before
  `descriptionlist_node[0]` (empty split → IndexError). Surfaced when querying a repo-built KB in
  `mini` mode after `kg_dedup` pruned nodes — some kept nodes have empty descriptions. `light` mode
  was separately broken by the vdb being out of sync with the graph after dedup; the fix there is
  repo-side (`src/rag/kg_dedup.py::_prune_vdb` deletes vdb entries for dropped nodes/edges), NOT a
  MiniRAG edit.

- `minirag/prompt.py` — `PROMPTS["entity_extraction"]`, 2026-09-23. Three rules added to step 1, after a
  KG audit of the HOML ch.3-4 build found 45 of 45 `work` nodes were cross-reference labels
  ("Figure 3-3", "Equation 4-2", "Table 4-1") and 7 `unknown` nodes were example-local variables:
  - `entity_type` line tightened: `code` is a PUBLIC identifier only (never an example-local variable);
    `work` is the title of a cited publication only.
  - **CROSS-REFERENCE RULE** — a pointer to one of the document's own exhibits is never an entity; from a
    `[Figure]`/`[Table]` line extract what the exhibit SHOWS, never its label. The exhibits themselves
    already get correctly-typed nodes from the repo's structural pass, which the LLM cannot anchor.
  - **CODE BLOCK RULE** — a fenced block is an example OF the surrounding concepts; extract those and the
    public APIs called, not every token.
  Changes the prompt hash ⇒ invalidates `kv_store_llm_response_cache.json`; a rebuild re-pays extraction
  (measured 80 calls / 344,618 tokens / 77 s for an 88-page book on gpt-4o-mini).

- `minirag/llm/openai.py` — 2026-10-04: the `token_tracker` usage also carries `cached_tokens` (from
  `usage.prompt_tokens_details`), so the repo's run summary shows how much of the constant extraction template
  OpenAI served from its prompt cache. No effect on extraction or on the response cache key.
- Gleaning (`entity_extract_max_gleaning`, upstream default 1) is **kept on purpose**. Measured on the HOML
  build (210 chunks, read from the response cache, 2026-10-04): gleaning added 2,656 new entities to the first
  pass's 2,626 (+101%) and 1,052 relations (+62%); 1,944 of the added names occur in the chunk's own text, and 274
  concepts the deck's slides taught were extracted by gleaning only (no chunk's first pass had them). It costs
  about half of the extraction tokens, but turning it off would drop those concepts from the KG.

## Re-applying after an upstream update
Re-clone upstream, then re-apply the edits listed above. Structural KG / heading nodes / claims are added
by the repo in a **post-build networkx pass** (see `src/rag/build_kg.py`), NOT by editing MiniRAG internals,
so those survive an upstream refresh untouched.
