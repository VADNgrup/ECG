import os
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()


class Config:
    """Only endpoints, keys and model names come from .env; everything else is a fixed setting of the pipeline."""
    # ── .env: models and endpoints ─────────────────────────────────────────────
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    LLM_MODEL_NAME = os.getenv('LLM_MODEL_NAME')
    LLM_BASE_URL = os.getenv('LLM_BASE_URL')
    VLM_BASE_URL = os.getenv('VLM_BASE_URL')
    VLM_API_KEY = os.getenv('VLM_API_KEY')
    VLM_MODEL_NAME = os.getenv('VLM_MODEL_NAME')
    OCR_MODE = os.getenv('OCR_MODE', '').strip().lower() or 'cloud'   # who reads the pages: cloud (default) | mineru
    OCR_MODES = {'cloud': 'cloud', 'mineru': 'none'}                   # OCR_MODE -> its `--ocr` value
    WRITER_MODEL = os.getenv('WRITER_MODEL', '')                  # Slide Writer model ('' = LLM_MODEL_NAME)
    IMAGE_GEN_MODEL = os.getenv('IMAGE_GEN_MODEL', 'gpt-image-1')
    EVAL_LANGUAGE_MODEL = os.getenv('EVAL_LANGUAGE_MODEL')
    EVAL_BASE_URL = os.getenv('EVAL_BASE_URL')
    EVAL_API_KEY = os.getenv('EVAL_API_KEY')
    EMBED_MODEL = os.getenv('EMBED_MODEL', 'text-embedding-3-small')
    EMBED_BASE_URL = os.getenv('EMBED_BASE_URL') or os.getenv('LLM_BASE_URL', 'https://api.openai.com/v1')
    EMBED_API_KEY = os.getenv('EMBED_API_KEY') or os.getenv('OPENAI_API_KEY', '')
    EMBED_BACKEND = 'openai'                                       # tests switch it to 'hash' (tests/conftest.py)

    # ── paths ──────────────────────────────────────────────────────────────────
    BASE_DIR = Path(__file__).parent.parent.parent
    DATA_DIR = BASE_DIR / 'data'
    OUTPUT_DIR = BASE_DIR / 'output'
    RAW_DIR = DATA_DIR / 'raw'
    ASSETS_DIR = DATA_DIR / 'assets'
    CONTEXT_DIR = DATA_DIR / 'context'
    LECTURES_DIR = DATA_DIR / 'lectures'
    LOGS_DIR = BASE_DIR / 'logs'
    # every rebuildable cache lives under one directory, one subfolder per kind
    CACHE_DIR = DATA_DIR / 'cache'
    PARSE_CACHE_DIR = CACHE_DIR / 'parse'            # MinerU / cloud-OCR page parse output
    KB_CACHE_DIR = CACHE_DIR / 'kb'                   # knowledge-base graphs (was data/rag_kb)
    DECISION_CACHE_DIR = CACHE_DIR / 'decisions'      # cached LLM decisions (the KG step's are kept across runs)
    RUN_DECISIONS = '_run'               # the agents' decisions of the current run, under DECISION_CACHE_DIR; removed
                                         # when the run has made all its decks
    DECISION_ARCHIVE_DIR = CACHE_DIR / 'decisions_archive'   # a copy of every agent decision, for measuring a rule
                                                             # against them; the pipeline never reads it

    # ── fields of study: each has its own prompts and layouts (main.py --domain; detected when not given) ──
    DOMAINS = ("general", "cs", "engineering", "math", "science", "economics", "social")
    DOMAIN_MIN_CONFIDENCE = 0.6          # a detected field below this confidence takes the general prompts
    DOMAIN_SAMPLE_CHARS = 1500           # opening text of the document the field detector reads
    DOMAIN_SAMPLE_CONCEPTS = 20          # most mentioned KG concepts it reads
    SCOPE_MENU_DEPTH = 2                 # levels of sections listed for the user to choose the deck's scope
    # relations (layout_purpose) a field's slides take first, as its prompt overlay says; the Writer is offered every
    # usable layout, these first in this order (a field not listed keeps the catalogue's order)
    DOMAIN_RELATIONS = {
        "cs": ("procedure", "code_walkthrough", "code_walkthrough_wide", "contrast", "kinds", "hierarchy"),
        "engineering": ("procedure", "figure", "figure_wide", "comparison", "kinds", "table"),
        "math": ("formula_terms", "definition", "procedure", "formula", "hierarchy"),
        "science": ("contrast", "procedure", "loop", "figure", "figure_wide", "kinds"),
        "economics": ("key_figures", "contrast", "sequence", "table", "quantitative_summary", "lead_support"),
        "social": ("contrast", "sequence", "hub", "lead_support", "summary"),
    }
    # ── the document read ──────────────────────────────────────────────────────
    BOOK_TITLE_PAGES = 3                 # a PDF's metadata title is the deck's only when its opening pages print it:
    BOOK_TITLE_MIN_SHARED = 0.8          # this share of the title's words, at least
    MIN_SOURCE_CHARS = 512               # a reading shorter than this is an empty or broken extraction: no deck
    LANGUAGE_SAMPLE_CHARS = 4000         # opening of the document its language is detected from
    # ── Outline agent ──────────────────────────────────────────────────────────
    OUTLINE_UNIT_MAX_CHARS = 150_000     # a scope root bigger than this (a Part of chapters) is planned chapter by
                                         # chapter: chapters measure 0.9k-95k chars (HOML, Spark), Parts 305k-364k
    OUTLINE_BLOCK_MIN_CHARS = 1000       # sections join into blocks of at least this: 2000 put 2.95 sections in a block of the 48-document collection, 1000 puts 1.96 (HOML 1.63 -> 1.29)
    OUTLINE_GIST_SENTENCES = 2           # sentences retrieval gives per block to say what it states, at least...
    OUTLINE_GIST_MAX = 10                # ...one per slide the block may get, at most this many
    OUTLINE_IDEA_CHARS = 160             # the KG description shown with each idea of a heading stating no edge, up to this
    OUTLINE_GIST_CHARS = 220            # each shown up to this many characters
    OUTLINE_QUERY_TOP_K = 10             # entities MiniRAG's mini query matches per keyword (its QueryParam.top_k)
    OUTLINE_QUERY_NODE_TOKENS = 4000     # its entity table's token cap (MiniRAG: 500); the table reaches no LLM, and a
                                         # KG of several documents needs room left after other documents' entities
    OUTLINE_ASSET_CHARS = 90             # a table's or figure's caption line in a block's dossier
    OUTLINE_RELATION_CHARS = 90          # the sentence stating a relation, up to this many characters
    LEAVE_INCIDENTAL_SPAN = 2            # a node named in at most this many paragraphs may be left as named in passing
    PLANNER_MAX_CONCURRENCY = 4          # chapters planned in parallel
    OUTLINE_GOALS_MAX_TOKENS = 2000      # answer room of the Outline's second call: goals of the headings it left without one
    OUTLINE_MAX_TOKENS = 12000          # answer room of one Outline call: a chapter's goal tree and slides (estimated ~7k for 40 headings)
    # ── Packet Builder ─────────────────────────────────────────────────────────
    PACKET_PASSAGES = 3                  # whole paragraphs an anchor packet carries
    PACKET_IMAGE_MIN_COS = 0.4           # an uncited figure must be this close to the slide
    TABLE_MATCH_CHARS = 4000             # text of a table, and of a slide's passages, embedded to place an uncited table
    NO_SPACE_CHAR_SIZE = 5               # text one character of a no-space script holds, as characters of a spaced one: one text measures 3000 (German) and 599 (Chinese)
    PACKET_HINT_CHARS = 140              # the anchored sentence shown for a node its passages do not name
    MAX_SLIDE_IMAGES = 2                 # figures one slide shows (two side by side)
    # ── Slide Writer ───────────────────────────────────────────────────────────
    SLIDE_WRITER_BATCH = 3               # slides per Writer call: one system prompt for a goal's run (at 6, gpt-4o-mini wrote 29% fewer bullets per slide)
    WRITER_MAX_TOKENS = 6000             # answer room of a one-slide Writer call
    WRITER_SLIDE_TOKENS = 2000           # answer room each further slide of a batch adds
    MAX_SLIDE_FORMULAS = 3               # formulas one slide draws, stacked in its formula box
    SHOWN_VISUALS = 4                    # formula candidates the Writer sees, listings the Packet Builder chooses from
    SHOWN_CODE_LINES = 40                # lines of a listing an agent reads (the speaker notes keep all of it)
    SHOWN_FORMULA_CHARS = 400            # characters of a formula an agent reads
    WRITER_RETRIES = 2                   # a slide with no usable answer is asked again this often
    WRITER_REPAIR_BATCH = 6              # slides per call when the Writer writes again the bullets QA sent back
    WRITER_REPAIR_MAX_TOKENS = 3000      # answer room of one such call
    LLM_REASONING_EFFORT = 'low'         # the effort asked of a model seen thinking, where its endpoint takes `reasoning_effort`
    LLM_THINK_KEEP_SHARE = 0.5           # a way to think less is kept when the model then thinks at most this share of before
    LLM_THINK_SAMPLES = 3                # replies a way to think less is judged on (and plain replies it is judged against)
    LLM_REASONING_HEADROOM = 4000        # tokens a reasoning model may think on top of each call's answer budget
    LLM_MAX_RETRIES = 8                  # attempts of one chat/vision call (rate limits, timeouts, network)
    LLM_RETRY_MAX_WAIT_S = 60.0          # longest wait before a retry (a rate limit window is one minute)
    LLM_RETRY_JITTER = 0.25              # share of a wait added at random, so parallel calls do not retry together
    VLM_MAX_CONCURRENCY = 4              # vision calls in flight at once across every thread (page images are large)
    OCR_CONCURRENCY = 4                  # pages handed to the vision model at once (VLM_MAX_CONCURRENCY still applies)
    # ── Content QA ─────────────────────────────────────────────────────────────
    BULLET_SOURCE_CHARS = 200            # a slide's source supports one bullet per this many characters at most...
    BULLET_SOURCE_CHARS_LOOSE = 400      # ...and is expected to give one per this many
    WRITER_SLIDE_BULLETS = 6             # bullets the Writer writes on a slide: median of 845 text slides of 52 decks (2026-11-15, quartiles 4-7)
    LANGUAGE_DRIFT_SUPPORT = 0.3         # deck_report: a bullet sharing less than this of its words with its sources is unlike them
    DECK_LABEL_SAMPLE_CHARS = 1200       # how much of a chapter's text tells its language...
    DECK_LABEL_SAMPLE_PIECES = 8         # ...taken from this many paragraphs across it, so one foreign abstract does not decide
    DECK_LABEL_MAX_CHARS = 20            # a fixed label longer than this is not a label: the renderer's default stays
    DECK_LABEL_MAX_TOKENS = 160          # the answer: nine short labels
    END_SUMMARY_MAX_ITEMS = 6            # the closing slide's recap: at most this many lines (a chapter's goals, or the
                                         # deck's structure at the deepest level that fits)
    TOC_GLANCE_COLS = 2                  # Contents adds a deeper heading level only while the page stays within this many
                                         # of the renderer's columns (fit_model.TOC_ROWS_MULTI rows each)
    QA_THIN_BULLETS = 3                  # a slide with fewer bullets gets unused facts or is dropped
    QA_MAX_ROUNDS = 2                    # rounds the QA Router may send slides back to the Writer (the paper's k_max);
                                         # a bullet still wrong after them is removed
    GRAPH_RECURSION_LIMIT = 32           # LangGraph steps per lecture: 4 agents + 2 per Writer repair, with room
    QA_BATCH = 15                        # slides per QA call: the system prompt once for many slides; every bullet is still read
    QA_DRAWN_LINES = 20                  # lines of a drawn listing QA reads to judge it
    CITE_SUPPORT_MIN = 0.8               # share of a bullet's content words its cited sentences must hold
    CITE_ANCHOR_MIN = 0.5                # share an uncited bullet needs in a slide sentence to be anchored to it
    CITE_EXTRA_MAX = 3                   # slide sentences code may add to a bullet's cites to reach CITE_SUPPORT_MIN
    SECTION_SLIDE_MIN_CHARS = QA_THIN_BULLETS * BULLET_SOURCE_CHARS   # a section with this much text can fill a slide alone

    # ── retrieval and embeddings ───────────────────────────────────────────────
    RETRIEVE_CHUNKS = 6                  # MiniRAG chunk hits per lookup
    RETRIEVE_ENTITIES = 4                # MiniRAG entity hits per lookup
    EMBED_BATCH = 128
    EMBED_AUTOSAVE_EVERY = 128           # new vectors between cache flushes
    EMBED_TIMEOUT_S = 120.0              # one embedding request, before the client tries again
    EMBED_MAX_RETRIES = 5                # the client's own retries (growing waits) before a failure stops the run
    # ── KG build ───────────────────────────────────────────────────────────────
    KG_DEDUP_THRESHOLD = 0.8             # node-dedup similarity (only with KG_DEDUP_ENABLED)
    KG_DEDUP_ENABLED = False             # the similarity merge over-merges distinct entities on HOML
    IMG_ICON_MAX_AREA = 0.02             # a figure covering less of the page is page furniture
    IMG_REPEAT_MAX_AREA = 0.06           # the repeated-geometry rule applies to figures this small
    IMG_REPEAT_PAGES = 3                 # same small figure on this many pages = margin icon / logo
    MIN_EXTRACT_IMAGE_WIDTH = 80
    MIN_EXTRACT_IMAGE_HEIGHT = 60
    MIN_EXTRACT_IMAGE_AREA_RATIO = 0.01
    MIN_EXTRACT_IMAGE_ASPECT_RATIO = 0.2
    MAX_EXTRACT_IMAGE_ASPECT_RATIO = 6.0
    TABLE_MIN_COLS = 2                   # a table asset has at least this many columns
    TABLE_MAX_COLS = 12                  # and at most this many
    TABLE_HEADER_FILLED_MIN = 0.75       # share of its header cells that must hold text
    # ── evaluation (src/evaluation/deck_metrics.py) ────────────────────────────
    SUPPORT_MIN = 0.6                    # bullet<->source cosine below this: drift
    KEYTERM_MIN = 0.5                    # share of the source's salient terms a bullet must keep
    COVERAGE_MIN = 0.5                   # cosine at which a checklist item counts as present on the deck
    SLIDE_TITLE_MIN_WORDS, SLIDE_TITLE_MAX_WORDS = 2, 8   # a slide title the Writer gives (its prompt says 2-8 words)
    # a bullet sharing this share of its content words with an earlier bullet of the deck says it again and is removed:
    # on the 48-document collection (2026-10-28) every cross-slide pair at >= 0.7 was the same fact, at 0.6 about half
    REPEAT_BULLET_OVERLAP = 0.7
    REPORT_REPEAT_OVERLAP = 0.5     # deck_report: a bullet sharing this share of its content words with another slide's repeats it
    REPEAT_ASK_OVERLAP = REPORT_REPEAT_OVERLAP   # below REPEAT_BULLET_OVERLAP, from here the LLM says whether it adds anything
    REPEAT_MAX_TOKENS = 20               # room of that yes-or-no answer
    # captions and numbered headings, learned from the document (src/rag/ocr.py, structural.py, packet_assembly.py)
    CAPTION_LABEL_MIN_CHARS = 2     # a caption label ("Table", "Hình", "图表") is a word of this many letters...
    FENCED_SAMPLE_CHARS = 300            # how much of a fenced block the LLM reads to tell a listing from prose
    FENCED_MAX_TOKENS = 30               # room of its yes-or-no answer
    FENCED_CONCURRENCY = 8               # blocks asked about at the same time
    EVAL_SLIDE_WIDTH_PX = 1280           # width of the slide pictures the evaluation reads
    EVAL_RENDER_TIMEOUT_S = 300          # longest a LibreOffice conversion of one deck may take (non-Windows)
    HEADING_CHECK_CONCURRENCY = 8        # marked lines asked about at the same time
    # A text-layer block the page's OCR skipped (bench10: missing paragraphs hold <= 0.21 of their word pairs and run
    # 35-122 words; partly misread ones >= 0.4; repeated headers and URL notes <= 26 words) is read again.
    OCR_MISSING_PAIR_SHARE = 0.25        # a block is missing when fewer of its word pairs than this are in the OCR
    OCR_MISSING_BLOCK_MIN_WORDS = 30     # and it holds at least this many words
    OCR_OVER_PICTURE_SHARE = 0.5         # a block mostly over a picture is the picture's label, never asked about
    IMAGE_ROLE_DETAIL = "low"            # how sharp the vision model sees an uncaptioned picture: enough for content or decoration
    IMAGE_ROLE_MAX_TOKENS = 20           # room of its one-word answer
    IMAGE_ROLE_CONCURRENCY = 8           # pictures asked about at the same time
    HEADING_CHECK_NEIGHBOURS = 2         # blocks shown on each side of a marked line
    HEADING_CHECK_CONTEXT_CHARS = 120    # how much of each of those blocks
    CAPTION_LABEL_MAX_CHARS = 24    # ...up to this many (longer is a heading's first word, not a label)
    CAPTION_NUMBER_MAX_DIGITS = 3   # its number has at most this many digits (more is a year or a count)
    CAPTION_LEAD_MARKS = 3          # marks before a caption's label ("**", "—") it may open with
    CAPTION_LINES_BEFORE = 2        # lines above a table where its caption may stand ("Table 1" / its title)...
    CAPTION_LINES_AFTER = 1         # ...and below it
    ROMAN_MAX_CHARS = 7             # a section numbered in Roman numerals (I..LXXXVIII) has at most this many
    HEADING_SIBLING_MAX_WORDS = 8   # a line this short, written like a heading's siblings, is a heading left unstyled
    FIG_FULL_PAGE_SHARE = 0.9       # a graphic covering this share of the page is the page's frame, not a figure
    FIG_EDGE_TOLERANCE_PT = 2       # points: a caption or graphic this close to an edge still touches it
    KG_IDEA_WEIGHTS = (0.2, 0.3, 0.2, 0.2, 0.1)   # concept rank: frequency, owner focus, degree, titled, early
    KG_GENERIC_SPREAD = 0.25             # a concept in more of the scope's sentences is subject-wide
    KG_IDEA_LOCAL_WEIGHT = 0.15
    KG_TREAT_INTRO_MAX = 2
    KG_TREAT_DEEP_MIN = 6

    @classmethod
    def get_log_path(cls):
        sanitized_model = (cls.LLM_MODEL_NAME or 'default').replace('/', '_').replace(':', '_')
        cls.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        return cls.LOGS_DIR / f'llm_calls_{sanitized_model}.jsonl'

    @classmethod
    def validate(cls):
        if not cls.OPENAI_API_KEY:
            raise ValueError('OPENAI_API_KEY not found in environment')
        cls.ensure_dirs()

    @classmethod
    def ensure_dirs(cls):
        """The working folders, made when missing (a fresh clone, or one removed by hand)."""
        for path in (cls.RAW_DIR, cls.ASSETS_DIR, cls.CONTEXT_DIR, cls.LECTURES_DIR, cls.OUTPUT_DIR, cls.LOGS_DIR):
            path.mkdir(parents=True, exist_ok=True)


config = Config()
