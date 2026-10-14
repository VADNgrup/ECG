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
    DECISION_CACHE_DIR = CACHE_DIR / 'decisions'      # cached agent LLM decisions

    # ── Lecture Planner ────────────────────────────────────────────────────────
    LECTURE_PART_CHARS = 10000           # source per Lecture Planner call (a chapter part)
    LECTURE_PART_CONCURRENCY = 8         # parts of a chapter planned in parallel
    LECTURE_THIN_CHARS = 1200            # a planned slide with less source is shown back to the planner
    LEAVE_INCIDENTAL_SPAN = 2            # a node named in at most this many paragraphs may be left as named in passing
    PLANNER_MAX_CONCURRENCY = 4          # chapters planned in parallel
    # ── Packet Builder ─────────────────────────────────────────────────────────
    PACKET_PASSAGES = 3                  # whole paragraphs an anchor packet carries
    PACKET_IMAGE_MIN_COS = 0.4           # an uncited figure must be this close to the slide
    MAX_SLIDE_IMAGES = 2                 # figures one slide shows (two side by side)
    # ── Slide Writer ───────────────────────────────────────────────────────────
    SLIDE_WRITER_BATCH = 1               # slides per Writer call (asked three at once, gpt-5-nano writes only the first)
    MAX_SLIDE_FORMULAS = 3               # formulas one slide draws, stacked in its formula box
    WRITER_RETRIES = 2                   # a slide with no usable answer or pasted bullets is asked again this often
    LLM_REASONING_EFFORT = 'low'         # sent to reasoning models; dropped when the endpoint refuses it
    LLM_REASONING_HEADROOM = 4000        # tokens a reasoning model may think on top of each call's answer budget
    # ── Content QA ─────────────────────────────────────────────────────────────
    QA_THIN_BULLETS = 3                  # a slide with fewer bullets gets unused facts or is dropped
    QA_MAX_ROUNDS = 2                    # QA <-> Writer rounds; a bullet still wrong after them is removed
    QA_BATCH = 6                         # slides per QA call

    # ── retrieval and embeddings ───────────────────────────────────────────────
    RETRIEVE_CHUNKS = 6                  # MiniRAG chunk hits per lookup
    RETRIEVE_ENTITIES = 4                # MiniRAG entity hits per lookup
    EMBED_BATCH = 128
    EMBED_AUTOSAVE_EVERY = 128           # new vectors between cache flushes
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
    # ── evaluation (src/evaluation/deck_metrics.py) ────────────────────────────
    SUPPORT_MIN = 0.6                    # bullet<->source cosine below this: drift
    KEYTERM_MIN = 0.5                    # share of the source's salient terms a bullet must keep
    COVERAGE_MIN = 0.5                   # cosine at which a checklist item counts as present on the deck
    KG_IDEA_WEIGHTS = (0.2, 0.3, 0.2, 0.2, 0.1)   # concept rank: frequency, owner focus, degree, titled, early
    KG_GENERIC_SPREAD = 0.25             # a concept in more of the scope's sentences is subject-wide
    KG_IDEA_LOCAL_WEIGHT = 0.15
    KG_IDEA_SALIENCE_WEIGHT = 0.1
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
        for path in (cls.RAW_DIR, cls.ASSETS_DIR, cls.CONTEXT_DIR, cls.LECTURES_DIR):
            path.mkdir(parents=True, exist_ok=True)


config = Config()
