from sentence_transformers import SentenceTransformer
from transformers import CLIPProcessor, CLIPModel
from typing import Optional
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
from typing import List, Optional, Tuple
from pathlib import Path
from PIL import Image
import torch

class SemanticMatcher:

    def __init__(self, lm_model_embedding: str='Qwen/Qwen3-Embedding-0.6B', clip_model_embedding: str='openai/clip-vit-base-patch32'):
        self.embedding_model = SentenceTransformer(lm_model_embedding, trust_remote_code=True)
        self.clip_model = CLIPModel.from_pretrained(clip_model_embedding)
        self.clip_processor = CLIPProcessor.from_pretrained(clip_model_embedding)

    def sentence_similarity(self, sentence_1: str, sentence_2: str) -> float:
        embedding_1 = self._get_text_embedding(sentence_1)
        embedding_2 = self._get_text_embedding(sentence_2)
        similarity = cosine_similarity(embedding_1.reshape(1, -1), embedding_2.reshape(1, -1))[0][0]
        return float(similarity)

    def _get_text_embedding(self, text: str):
        text_embedding = self.embedding_model.encode(text, convert_to_numpy=True)
        return text_embedding

    def text_image_similarity(self, text: str, image_path: str) -> float:
        text_clip_embedding = self._get_text_clip_embedding(text)
        image_clip_embedding = self._get_image_clip_embedding(image_path)
        similarity = cosine_similarity(text_clip_embedding.reshape(1, -1), image_clip_embedding.reshape(1, -1))[0][0]
        return float(similarity)

    def _get_text_clip_embedding(self, text: str):
        inputs = self.clip_processor(text=[text], return_tensors='pt', padding=True, truncation=True)
        with torch.no_grad():
            text_clip_embedding = self.clip_model.get_text_features(**inputs)
            text_clip_embedding = text_clip_embedding / text_clip_embedding.norm(dim=-1, keepdim=True)
        return text_clip_embedding.squeeze(0).cpu().numpy()

    def _get_image_clip_embedding(self, image_path: str):
        image = Image.open(image_path).convert('RGB')
        inputs = self.clip_processor(images=image, return_tensors='pt')
        with torch.no_grad():
            image_clip_embedding = self.clip_model.get_image_features(**inputs)
            image_clip_embedding = image_clip_embedding / image_clip_embedding.norm(dim=-1, keepdim=True)
        return image_clip_embedding.squeeze(0).cpu().numpy()