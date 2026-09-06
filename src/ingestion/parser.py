import os
import fitz
import base64
import requests
import json
import ast
import logging
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
from tqdm import tqdm
from src.utils.config import config

_log = logging.getLogger(__name__)

class ParsedContent:
    def __init__(self):
        self.full_text: str = ''
        self.tables: List[Dict[str, Any]] = []
        self.images: List[Dict[str, Any]] = []
        self.page_insights: List[Dict[str, Any]] = []
        self.page_count: int = 0

class DocumentParser:
    def __init__(self, pdf_path):
        self.pdf_path = Path(pdf_path) if isinstance(pdf_path, str) else pdf_path
        self.doc = fitz.open(str(self.pdf_path))
        self.model_url = config.VLM_BASE_URL
        if self.model_url and not self.model_url.endswith("/chat/completions"):
            self.model_url = f"{self.model_url.rstrip('/')}/chat/completions"
        
        self.model_key = config.VLM_API_KEY
        self.model_name = config.VLM_MODEL_NAME

    def _encode_image(self, img_bytes):
        return base64.b64encode(img_bytes).decode('utf-8')

    def parse_document(self, document_id: str = '') -> ParsedContent:
        content = ParsedContent()
        content.page_count = len(self.doc)
        
        assets_images_dir = config.ASSETS_DIR / document_id / 'images'
        assets_images_dir.mkdir(parents=True, exist_ok=True)

        full_markdown_parts = []
        all_extracted_images = []

        print(f"\n--- Starting VLM Conversion: {self.pdf_path.name} ---")

        for i in tqdm(range(content.page_count), desc="Processing Pages"):
            page = self.doc.load_page(i)
            
            text_blocks = page.get_text("dict")["blocks"]
            page_map = []
            for b in text_blocks:
                if b["type"] == 0:
                    text_content = ""
                    for line in b["lines"]:
                        for span in line["spans"]:
                            text_content += span["text"]
                        text_content += "\n"
                    if text_content.strip():
                        page_map.append({
                            "type": "text",
                            "content": text_content.strip(),
                            "bbox": b["bbox"]
                        })

            image_info_list = page.get_images(full=True)
            page_rect = page.rect
            page_area = max(page_rect.width * page_rect.height, 1)
            
            raw_rects = []
            for img in image_info_list:
                xref = img[0]
                for r in page.get_image_rects(xref):
                    raw_rects.append(r)
            
            merged_rects = self._merge_intersecting_rects(raw_rects, margin=3.0)

            for img_idx, rect in enumerate(merged_rects):
                if self._is_small_or_decorative_image(rect, page_area):
                    _log.info(
                        'Skipping small/decorative image on page %d: %.1fx%.1f (area %.4f)',
                        i + 1,
                        rect.width,
                        rect.height,
                        rect.width * rect.height / page_area,
                    )
                    continue
                
                # Render the merged region from the page instead of extracting sliced parts
                pix = page.get_pixmap(clip=rect, matrix=fitz.Matrix(2, 2))
                img_ext = "png"
                img_filename = f"page_{i+1}_img_{img_idx+1}.{img_ext}"
                img_path = assets_images_dir / img_filename

                with open(img_path, "wb") as f_img:
                    f_img.write(pix.tobytes("png"))
                
                rel_img_path = f"assets/{document_id}/images/{img_filename}"
                
                page_map.append({
                    "type": "image",
                    "content": rel_img_path,
                    "bbox": rect
                })
                
                all_extracted_images.append({
                    'image_id': f"img_{len(all_extracted_images) + 1:03d}",
                    'file_path': rel_img_path,
                    'caption': '', 
                    'reference_context': '',
                    'metadata': {
                        'width': int(rect.width),
                        'height': int(rect.height),
                        'format': img_ext,
                        'file_size_kb': len(pix.tobytes("png")) / 1024
                    }
                })

            page_map.sort(key=lambda x: x["bbox"][1])

            layout_map_str = ""
            for idx, item in enumerate(page_map):
                if item["type"] == "text":
                    layout_map_str += f"\n[BLOCK {idx} - TEXT]\n{item['content']}\n"
                else:
                    layout_map_str += f"\n[BLOCK {idx} - IMAGE: {item['content']}]\n"

            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            b64_img = self._encode_image(pix.tobytes("png"))

            page_markdown = self._call_vlm(layout_map_str, b64_img)
            full_markdown_parts.append(f"<!-- PAGE {i+1} -->\n\n{page_markdown}")
            page_insight = self._call_page_insight_vlm(layout_map_str, page_markdown, b64_img, i + 1)
            content.page_insights.append(page_insight)

        content.full_text = "\n\n---\n\n".join(full_markdown_parts)
        content.images = all_extracted_images
        content.tables = [] 
        
        self.doc.close()
        return content

    @staticmethod
    def _merge_intersecting_rects(rects: List[fitz.Rect], margin: float = 2.0) -> List[fitz.Rect]:
        merged = []
        for r in rects:
            r_padded = r + (-margin, -margin, margin, margin)
            intersected = False
            for i, m in enumerate(merged):
                m_padded = m + (-margin, -margin, margin, margin)
                if r_padded.intersects(m_padded):
                    merged[i] = m | r
                    intersected = True
                    break
            if not intersected:
                merged.append(r)
        
        # Second pass to ensure transitive merges are caught
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(merged):
                j = i + 1
                while j < len(merged):
                    m1_padded = merged[i] + (-margin, -margin, margin, margin)
                    m2_padded = merged[j] + (-margin, -margin, margin, margin)
                    if m1_padded.intersects(m2_padded):
                        merged[i] = merged[i] | merged[j]
                        merged.pop(j)
                        changed = True
                    else:
                        j += 1
                i += 1
        return merged

    @staticmethod
    def _is_small_or_decorative_image(rect, page_area: float) -> bool:
        width = float(rect.width)
        height = float(rect.height)
        if width < config.MIN_EXTRACT_IMAGE_WIDTH or height < config.MIN_EXTRACT_IMAGE_HEIGHT:
            return True
        image_area = width * height
        if image_area / page_area < config.MIN_EXTRACT_IMAGE_AREA_RATIO:
            return True
        aspect = width / height if height else 0
        if aspect < config.MIN_EXTRACT_IMAGE_ASPECT_RATIO or aspect > config.MAX_EXTRACT_IMAGE_ASPECT_RATIO:
            return True
        return False

    def _call_vlm(self, layout_map, b64_image) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.model_key}"
        }

        prompt = (
            "You are a professional document converter. I have provided a 'Layout Map' of a PDF page with sorted blocks.\n\n"
            "STRICT FORMATTING RULES:\n"
            "1. Output ONLY Markdown content. No conversational filler.\n"
            "2. For [BLOCK X - TEXT]: \n"
            "   - Convert to high-quality Markdown.\n"
            "   - Use LaTeX ($$ ... $$) for ALL mathematical formulas and variables.\n"
            "   - Use proper Markdown headers (#, ##, ###) for titles.\n"
            "   - Reconstruct tables using Markdown table syntax.\n"
            "3. For [BLOCK X - IMAGE: assets/...]: \n"
            "   - STEP 1: Look at the surrounding [TEXT] blocks in the Layout Map to see how the document refers to this image (look for 'Figure X', 'Table Y', or descriptive sentences nearby).\n"
            "   - STEP 2: Analyze the visual content of the image and read any text inside it.\n"
            "   - STEP 3: Generate a HIGHLY CONTEXTUAL caption that aligns with the document's text and the visual evidence.\n"
            "   - Insert it as: ![Contextual Caption](assets/...)\n"
            "   - Also add a visible caption below it: *Figure: Contextual Caption*\n"
            "   - IMPORTANT: USE THE EXACT FILENAME AND EXTENSION PROVIDED. NEVER CHANGE IT.\n"
            "4. Follow the EXACT sequence of blocks from the Layout Map.\n\n"
            f"LAYOUT MAP:\n{layout_map}"
        )

        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                    ]
                }
            ],
            "temperature": 0.1
        }

        try:
            response = requests.post(self.model_url, headers=headers, json=payload, timeout=420)
            response.raise_for_status()
            resp_data = response.json()
            content = resp_data['choices'][0]['message']['content']
            
            if content.startswith("```markdown"): content = content[11:]
            if content.startswith("```"): content = content[3:]
            if content.endswith("```"): content = content[:-3]
            
            return content.strip()
        except Exception as e:
            _log.error(f"Error calling VLM: {e}")
            return self._fallback_markdown_from_layout(layout_map)

    def _call_page_insight_vlm(self, layout_map: str, page_markdown: str, b64_image: str, page_num: int) -> Dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.model_key}"
        }
        prompt = (
            "You are analyzing a PDF page for presentation planning.\n"
            "Look at the page image, the layout map, and the reconstructed markdown.\n"
            "Decide what information on this page is most important to preserve in a slide deck.\n\n"
            "CRITICAL LANGUAGE RULE: Your output MUST use the EXACT SAME LANGUAGE as the body text "
            "in the LAYOUT MAP below. For must_have_points and support_points, extract SHORT VERBATIM "
            "PHRASES (5–15 words) directly from the [BLOCK X - TEXT] content — do NOT paraphrase or "
            "translate into any other language. If the layout text is in English, output in English. "
            "If Vietnamese, output in Vietnamese. Copy the actual words as they appear in the text.\n\n"
            "Return ONLY JSON with this schema:\n"
            "{\n"
            '  "page": <int>,\n'
            '  "page_role": "cover|overview|content|appendix|references|metadata",\n'
            '  "page_title": "<short title of the most important content on the page>",\n'
            '  "must_have_points": ["point 1", "point 2"],\n'
            '  "support_points": ["supporting detail"],\n'
            '  "noise_points": ["metadata or decorative content that should not become slide titles"],\n'
            '  "confidence": <float 0..1>\n'
            "}\n\n"
            "Rules:\n"
            "- Focus on presentation-worthy meaning, not literal OCR only.\n"
            "- For references or citation pages, mark citation lines as noise or support unless they reveal a clear theme.\n"
            "- For table-heavy pages, identify the rows or findings that actually matter, not headers or isolated numbers.\n"
            "- Keep must-have points short and concrete.\n\n"
            f"PAGE NUMBER: {page_num}\n\n"
            f"LAYOUT MAP:\n{layout_map}\n\n"
            f"RECONSTRUCTED MARKDOWN:\n{page_markdown}\n"
        )
        payload = {
            "model": self.model_name,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_image}"}}
                    ]
                }
            ],
            "temperature": 0.1
        }
        try:
            response = requests.post(self.model_url, headers=headers, json=payload, timeout=420)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            return self._parse_page_insight(content, page_num)
        except Exception as e:
            _log.warning(f"Error calling page insight VLM on page {page_num}: {e}")
            return self._fallback_page_insight(page_markdown, page_num)

    @staticmethod
    def _parse_page_insight(content: str, page_num: int) -> Dict[str, Any]:
        text = (content or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        parsed: Dict[str, Any] = {}
        for loader in (json.loads, ast.literal_eval):
            try:
                candidate = loader(text)
                if isinstance(candidate, dict):
                    parsed = candidate
                    break
            except Exception:
                continue
        if not parsed:
            return DocumentParser._fallback_page_insight("", page_num)
        return {
            "page": int(parsed.get("page") or page_num),
            "page_role": str(parsed.get("page_role") or "content").strip() or "content",
            "page_title": str(parsed.get("page_title") or "").strip(),
            "must_have_points": [str(item).strip() for item in parsed.get("must_have_points", []) if str(item).strip()][:8],
            "support_points": [str(item).strip() for item in parsed.get("support_points", []) if str(item).strip()][:8],
            "noise_points": [str(item).strip() for item in parsed.get("noise_points", []) if str(item).strip()][:8],
            "confidence": float(parsed.get("confidence") or 0.0),
        }

    @staticmethod
    def _fallback_page_insight(page_markdown: str, page_num: int) -> Dict[str, Any]:
        headings = re.findall(r"^#{1,4}\s+(.+)$", page_markdown or "", flags=re.MULTILINE)
        bullets = re.findall(r"^\s*[-*]\s+(.+)$", page_markdown or "", flags=re.MULTILINE)
        numbered = re.findall(r"^\s*\d{1,2}[.)]\s+(.+)$", page_markdown or "", flags=re.MULTILINE)
        title = headings[0].strip()[:120] if headings else ""
        must_have = []
        for item in headings[1:4] + bullets[:4] + numbered[:4]:
            text = " ".join(str(item).split())
            if len(text) >= 12:
                must_have.append(text[:180])
        return {
            "page": page_num,
            "page_role": "content",
            "page_title": title,
            "must_have_points": must_have[:6],
            "support_points": [],
            "noise_points": [],
            "confidence": 0.2,
        }

    @staticmethod
    def _fallback_markdown_from_layout(layout_map: str) -> str:
        chunks: List[str] = []
        current_heading = None
        for raw_line in (layout_map or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("[BLOCK"):
                continue
            clean = re.sub(r"\s+", " ", line).strip()
            if not clean:
                continue
            if len(clean) <= 80 and clean.upper() == clean and re.search(r"[A-ZÀ-Ỹ]", clean):
                current_heading = clean.title()
                chunks.append(f"# {current_heading}")
            elif re.match(r"^\d{1,2}[.)]\s+", clean):
                chunks.append(clean)
            elif len(clean) <= 160 and clean.endswith(":"):
                chunks.append(f"## {clean[:-1]}")
            else:
                chunks.append(clean)
        return "\n\n".join(chunks[:120]).strip() or "# Extracted Page\n\nContent could not be reconstructed cleanly."
