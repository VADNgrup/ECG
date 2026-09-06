import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from PIL import Image
import io
from src.models.asset import ImageAsset, AssetMetadata
from src.utils.config import config
from src.utils.file_utils import ensure_dir
from src.ingestion.image_filter import ImageFilter
_log = logging.getLogger(__name__)

class AssetManager:

    def __init__(self, document_id: str):
        self.document_id = document_id
        self.asset_dir = config.ASSETS_DIR / document_id / 'images'
        ensure_dir(self.asset_dir)
        self.images: List[ImageAsset] = []
        self._image_filter = ImageFilter()

    def save_image(self, image_bytes: bytes, image_index: int=0, caption: str='', reference_context: str='') -> Optional[ImageAsset]:
        (passed, reason) = self._image_filter.pre_filter(image_bytes)
        if not passed:
            _log.info('[AssetManager] Skipping image %02d — filter: %s', image_index, reason)
            return None
        image_id = f'img_{image_index:02d}'
        img = Image.open(io.BytesIO(image_bytes))
        if img.mode == 'RGBA':
            background = Image.new('RGB', img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        if img.size[0] > config.MAX_IMAGE_SIZE[0] or img.size[1] > config.MAX_IMAGE_SIZE[1]:
            img.thumbnail(config.MAX_IMAGE_SIZE, Image.Resampling.LANCZOS)
        file_name = f'{image_id}.{config.DEFAULT_IMAGE_FORMAT}'
        file_path = self.asset_dir / file_name
        img.save(file_path, format=config.DEFAULT_IMAGE_FORMAT.upper())
        file_size_kb = file_path.stat().st_size / 1024
        metadata = AssetMetadata(width=img.width, height=img.height, format=config.DEFAULT_IMAGE_FORMAT, file_size_kb=round(file_size_kb, 2))
        relative_path = str(file_path.relative_to(config.BASE_DIR))
        image_asset = ImageAsset(image_id=image_id, file_path=relative_path, caption=caption, metadata=metadata)
        self.images.append(image_asset)
        return image_asset

    def get_all_images(self) -> List[ImageAsset]:
        return self.images

    def get_image_path(self, image_id: str) -> Path:
        for img in self.images:
            if img.image_id == image_id:
                return config.BASE_DIR / img.file_path
        raise ValueError(f'Image {image_id} not found')