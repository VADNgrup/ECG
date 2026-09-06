import json
import random
from src.utils.config import Config


def get_theme(theme_name: str) -> tuple:
    with open(Config.THEME_PATH, 'r', encoding='utf-8') as f:
        themes = json.load(f)
    for theme in themes:
        if theme['theme'] == theme_name:
            return (theme['theme'], theme['font'])
    return ('seriph', 'Roboto')


def select_theme(*_args, **_kwargs) -> tuple:
    with open(Config.THEME_PATH, 'r', encoding='utf-8') as f:
        themes = json.load(f)
    available = [t['theme'] for t in themes if t.get('theme')]
    chosen = random.choice(available)
    print(f'[theme_selection] Random theme: {chosen}')
    return get_theme(chosen)
