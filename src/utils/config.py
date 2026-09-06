import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

class Config:
    OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
    LLM_MODEL_NAME = os.getenv('LLM_MODEL_NAME')
    LLM_BASE_URL = os.getenv('LLM_BASE_URL')
    VLM_BASE_URL = os.getenv('VLM_BASE_URL')
    VLM_API_KEY = os.getenv('VLM_API_KEY')
    VLM_MODEL_NAME = os.getenv('VLM_MODEL_NAME')
    EVAL_LANGUAGE_MODEL = os.getenv('EVAL_LANGUAGE_MODEL')
    EVAL_VISION_MODEL = os.getenv('EVAL_VISION_MODEL')
    EVAL_BASE_URL = os.getenv('EVAL_BASE_URL')
    EVAL_API_KEY = os.getenv('EVAL_API_KEY')
    BASE_DIR = Path(__file__).parent.parent.parent
    DATA_DIR = BASE_DIR / 'data'
    OUTPUT_DIR = BASE_DIR / 'output'
    RAW_DIR = DATA_DIR / 'raw'
    ASSETS_DIR = DATA_DIR / 'assets'
    CONTEXT_DIR = DATA_DIR / 'context'
    LECTURES_DIR = DATA_DIR / 'lectures'
    RESOURCE_DIR = BASE_DIR / 'src' / 'resources'
    THEME_PATH = DATA_DIR / 'theme' / 'theme.json'
    VISION_MAX_TOKENS = 2000
    VISION_TEMPERATURE = 0.3
    IMAGE_MATCH_THRESHOLD = float(os.getenv('IMAGE_MATCH_THRESHOLD', '0.18'))
    IMAGE_MATCH_MAX_IMAGES_PER_SLIDE = int(os.getenv('IMAGE_MATCH_MAX_IMAGES_PER_SLIDE', '2'))
    # 0 = full pipeline (standard MASG). 1 = skip packet builder. 2 = skip content QA loop. 4 = skip clean_repetition().
    ABLATION_MODE = int(os.getenv('ABLATION_MODE', '0'))
    MAX_QA_LOOPS = int(os.getenv('MAX_QA_LOOPS', '3'))
    IMAGE_MATCH_STOPWORDS_PATH = RESOURCE_DIR / 'multimodal_stopwords.txt'
    MIN_EXTRACT_IMAGE_WIDTH = 80
    MIN_EXTRACT_IMAGE_HEIGHT = 60
    MIN_EXTRACT_IMAGE_AREA_RATIO = 0.01
    MIN_EXTRACT_IMAGE_ASPECT_RATIO = 0.2
    MAX_EXTRACT_IMAGE_ASPECT_RATIO = 6.0
    @classmethod
    def get_log_path(cls):
        sanitized_model = (cls.LLM_MODEL_NAME or 'default').replace('/', '_').replace(':', '_')
        log_dir = cls.BASE_DIR / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f'llm_calls_{sanitized_model}.jsonl'

    @classmethod
    def validate(cls):
        if not cls.OPENAI_API_KEY:
            raise ValueError('OPENAI_API_KEY not found in environment')
        cls.RAW_DIR.mkdir(parents=True, exist_ok=True)
        cls.ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        cls.CONTEXT_DIR.mkdir(parents=True, exist_ok=True)
        cls.LECTURES_DIR.mkdir(parents=True, exist_ok=True)
config = Config()
