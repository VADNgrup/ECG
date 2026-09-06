import base64
from typing import List, Optional, Dict, Any
from tqdm import tqdm
from src.utils.config import config
from src.utils.llm import vision_chat, b64_image

class VisionCaptionGenerator:

    def __init__(self, model: str=None):
        self.model = model or config.VLM_MODEL_NAME
        self.max_tokens = config.VISION_MAX_TOKENS
        self.temperature = config.VISION_TEMPERATURE

    def generate_caption(self, image_bytes: bytes, context: str) -> str:
        prompt = f'You are analyzing an image from an educational document. \n\nContext from the document:\n{context}\n\nBased on the context and the image content, generate a clear, concise, and descriptive caption for this image. The caption should:\n1. Accurately describe what the image shows\n2. Connect the image to the surrounding context\n3. Be suitable for educational purposes\n4. Be 2-3 sentences long\n\nReturn ONLY the caption text, without any additional formatting or explanation.'
        try:
            return vision_chat(messages=[{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, b64_image(image_bytes)]}], model=self.model, temperature=self.temperature, max_tokens=self.max_tokens)
        except Exception as e:
            print(f'Error generating caption: {e}')
            return f'Image related to: {context[:100]}...' if context else 'Educational image'

    def generate_table_caption(self, table_markdown: str, context: str) -> str:
        prompt = f'You are analyzing a table from an educational document.\n\nContext from the document:\n{context}\n\nTable content:\n{table_markdown}\n\nBased on the table content and surrounding context, generate a clear, concise, and descriptive caption for this table. The caption should:\n1. Accurately describe what data the table presents\n2. Connect the table to the surrounding context\n3. Be suitable for educational purposes\n4. Start with "Table:" or "Table X:" format\n5. Be 1-2 sentences long\n\nReturn ONLY the caption text, without any additional formatting or explanation.'
        try:
            return vision_chat(messages=[{'role': 'user', 'content': prompt}], model=self.model, temperature=self.temperature, max_tokens=self.max_tokens)
        except Exception as e:
            print(f'Error generating table caption: {e}')
            return f'Table: {context[:100]}...' if context else 'Data table'