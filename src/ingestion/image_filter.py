import io
from pathlib import Path
from typing import Tuple, Optional
from PIL import Image, ImageStat
import numpy as np
from src.utils.config import config

class ImageFilter:
    QUALITY_THRESHOLD = 0.5

    def __init__(self):
        pass

    def should_use_image(self, image_bytes: bytes) -> Tuple[bool, str]:
        (passed, reason) = self.pre_filter(image_bytes)
        if not passed:
            return (False, reason)
        quality = self.assess_quality(image_bytes)
        if quality < self.QUALITY_THRESHOLD:
            return (False, f'low_quality (score={quality:.2f})')
        return (True, f'accepted (score={quality:.2f})')

    def pre_filter(self, image_bytes: bytes) -> Tuple[bool, str]:
        try:
            img = Image.open(io.BytesIO(image_bytes))
            if img.mode != 'RGB':
                img = img.convert('RGB')
            (width, height) = img.size
            ratio = width / height
            r_ratio = height / width
            if width < 300 or height < 300:
                return (False, 'too_small')
            if ratio > 5 or r_ratio > 5:
                return (False, 'too_thin')
            img_array = np.array(img)
            if self._is_pure_single_color(img_array):
                return (False, 'pure_single_color')
            return (True, 'passed_pre_filter')
        except Exception:
            return (True, 'error_assume_valid')

    def assess_quality(self, image_bytes: bytes) -> float:
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert('RGB')
            (width, height) = img.size
            resolution_score = self._score_resolution(width, height)
            sharpness_score = self._score_sharpness(img)
            contrast_score = self._score_contrast(img)
            quality = resolution_score * 0.4 + sharpness_score * 0.4 + contrast_score * 0.2
            return min(max(quality, 0.0), 1.0)
        except Exception as e:
            print(f'Image quality assessment error: {e}')
            return 0.5

    @staticmethod
    def _score_resolution(width: int, height: int) -> float:
        pixel_count = width * height
        if pixel_count >= 500 * 500:
            return 1.0
        elif pixel_count >= 300 * 300:
            return 0.9
        elif pixel_count >= 200 * 200:
            return 0.75
        elif pixel_count >= 150 * 150:
            return 0.6
        elif pixel_count >= 100 * 100:
            return 0.5
        else:
            return 0.3

    @staticmethod
    def _score_sharpness(img: Image.Image) -> float:
        try:
            img_array = np.array(img.convert('L'))
            laplacian = np.array([[0, -1, 0], [-1, 4, -1], [0, -1, 0]])
            laplacian_result = np.abs(np.convolve(img_array.flatten(), laplacian.flatten(), mode='valid'))
            variance = np.var(laplacian_result)
            if variance > 500:
                return 1.0
            elif variance > 300:
                return 0.8
            elif variance > 150:
                return 0.6
            elif variance > 50:
                return 0.4
            else:
                return 0.3
        except Exception:
            return 0.6

    @staticmethod
    def _score_contrast(img: Image.Image) -> float:
        try:
            stat = ImageStat.Stat(img)
            std_dev = np.mean(stat.stddev)
            if std_dev > 60:
                return 1.0
            elif std_dev > 45:
                return 0.8
            elif std_dev > 30:
                return 0.6
            elif std_dev > 15:
                return 0.4
            else:
                return 0.3
        except Exception:
            return 0.6

    def _is_pure_single_color(self, img_array: np.ndarray) -> bool:
        if len(img_array.shape) != 3:
            return False
        unique_colors = len(np.unique(img_array.reshape(-1, img_array.shape[2]), axis=0))
        if unique_colors <= 2:
            return True
        if unique_colors <= 5:
            flat = img_array.reshape(-1, img_array.shape[2])
            (unique, counts) = np.unique(flat, axis=0, return_counts=True)
            most_common_count = counts.max()
            total_pixels = img_array.shape[0] * img_array.shape[1]
            if most_common_count / total_pixels > 0.99:
                return True
        return False