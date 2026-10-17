"""Text in product images (spec 4.4). PaddleOCR-VL reads an image on the local GPU and gives its text and tables as
markdown. The text goes into the chunks of the listing (source "image"), so search_text finds it and no image is
read twice. An image without usable text (a photo, or an advertisement with a heavy design) goes to the worker,
which answers the question of the chat model from it.

Prices in images are often old, so a price in image text is never used."""
import io
import re
import time
from dataclasses import dataclass

import httpx

MODEL = "PaddlePaddle/PaddleOCR-VL"
IMAGE_HOSTS = ("https://m.media-amazon.com/",)  # the image server of amazon.se, not its pages
MAX_ASPECT = 2.0  # an image higher than 2 times its width is cut into segments
MIN_CHARACTERS = 6  # letters and digits; less than this is not usable text
MAX_NEW_TOKENS = 1024


@dataclass
class ImageText:
    url: str
    text: str
    seconds: float


def usable(text: str) -> bool:
    """Enough letters and digits, and not one short piece many times (a model that loops)."""
    if len(re.findall(r"\w", text)) < MIN_CHARACTERS:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return not (len(lines) > 10 and len(set(lines)) <= len(lines) / 5)


def segments(image, max_aspect: float = MAX_ASPECT) -> list:
    """A long image (for example a long A+ image) cut into parts of about 1.5 times its width, with a small overlap,
    so the text stays large enough to read."""
    width, height = image.size
    if height <= max_aspect * width:
        return [image]
    step, overlap = int(1.5 * width), int(0.1 * width)
    tops = list(range(0, height, step))
    if len(tops) > 1 and height - tops[-1] < width // 2:
        tops.pop()  # a thin last strip goes into the part before it
    ends = [top + step + overlap for top in tops[:-1]] + [height]
    return [image.crop((0, top, width, min(end, height))) for top, end in zip(tops, ends)]


def fetch_image_bytes(url: str, timeout: float = 20, max_redirects: int = 3) -> bytes:
    """An image of the image server. A redirect is followed only when it stays on the image server."""
    for _ in range(max_redirects + 1):
        if not url.startswith(IMAGE_HOSTS):
            raise ValueError(f"not an image of the image server: {url}")
        response = httpx.get(url, timeout=timeout, follow_redirects=False)
        if not response.is_redirect:
            response.raise_for_status()
            return response.content
        url = str(response.next_request.url) if response.next_request else ""
    raise ValueError("too many redirects")


class PaddleReader:
    """PaddleOCR-VL through transformers. The model loads at the first image (about 2 GB on the GPU)."""

    def __init__(self, device: str | None = None):
        self.device = device
        self.model = None
        self.processor = None

    def _load(self) -> None:
        import torch
        from transformers import AutoProcessor, PaddleOCRVLForConditionalGeneration
        self.device = self.device or ("cuda" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        self.model = PaddleOCRVLForConditionalGeneration.from_pretrained(MODEL, dtype=dtype).to(self.device).eval()
        self.processor = AutoProcessor.from_pretrained(MODEL)

    def read(self, data: bytes) -> str:
        import torch
        from PIL import Image
        if self.model is None:
            self._load()
        parts = []
        for part in segments(Image.open(io.BytesIO(data)).convert("RGB")):
            messages = [{"role": "user", "content": [{"type": "image", "image": part}, {"type": "text", "text": "OCR:"}]}]
            inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                        return_dict=True, return_tensors="pt").to(self.device)
            with torch.no_grad():
                out = self.model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS)
            parts.append(self.processor.batch_decode(out[:, inputs.input_ids.shape[1]:], skip_special_tokens=True)[0])
        return "\n".join(p.strip() for p in parts if p.strip())


def read_image_texts(urls: list[str], reader, fetch=fetch_image_bytes) -> list[ImageText]:
    """The text of each image. An image that does not load gets no entry; an image without usable text gets ""."""
    found = []
    for url in urls:
        try:
            data = fetch(url)
        except (httpx.HTTPError, ValueError):
            continue
        start = time.perf_counter()
        try:
            text = reader.read(data)
        except Exception as exc:  # an image that the reader cannot open, or no GPU memory: the next image still runs
            if "out of memory" in str(exc).lower():
                raise
            text = ""
        found.append(ImageText(url, text if usable(text) else "", time.perf_counter() - start))
    return found
