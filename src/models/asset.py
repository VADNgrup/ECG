from typing import List, Optional
from pydantic import BaseModel, Field

class AssetMetadata(BaseModel):
    width: int
    height: int
    format: str
    file_size_kb: float

class ImageAsset(BaseModel):
    image_id: str
    file_path: str
    caption: str
    reference_context: Optional[str] = None
    metadata: AssetMetadata

class AssetCollection(BaseModel):
    images: List[ImageAsset] = Field(default_factory=list)

    def add_image(self, image: ImageAsset) -> None:
        self.images.append(image)

    def get_by_id(self, image_id: str) -> Optional[ImageAsset]:
        for image in self.images:
            if image.image_id == image_id:
                return image
        return None