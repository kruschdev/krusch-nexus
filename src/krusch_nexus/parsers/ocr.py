"""
src/krusch_nexus/parsers/ocr.py
===============================
Deterministic OCR execution and unified policy dataclass.
"""

import os
import shutil
import tempfile
import subprocess
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple

from ..models import ContentBlock

logger = logging.getLogger("krusch_nexus.parsers.ocr")


@dataclass
class OCRPolicy:
    """
    Unified, non-drifting configuration object for optical character recognition.
    """
    min_printable_chars: int = 40
    dpi: int = 300
    psm_prose: int = 6
    psm_form: int = 4
    confidence_floor: float = 0.50
    language: str = "eng"
    timeout_seconds: float = 30.0
    max_pixels: int = 100_000_000  # Cap on decompressed image pixels (100 MP)


class OCRResult(tuple):
    """3-tuple return value for OCR with optional page image pointer for audit."""
    def __new__(cls, text: Optional[str], conf: Optional[float], blocks: List[ContentBlock], image_path: Optional[str] = None):
        return super().__new__(cls, (text, conf, blocks))

    def __init__(self, text: Optional[str], conf: Optional[float], blocks: List[ContentBlock], image_path: Optional[str] = None):
        self.text = text
        self.conf = conf
        self.blocks = blocks
        self.image_path = image_path



import functools
import re


@functools.lru_cache(maxsize=1)
def get_system_tool_versions() -> dict:
    """
    Extract system binary tool versions (pdftotext/poppler, tesseract) for audit provenance.
    Cached for process lifetime.
    """
    versions = {}
    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            res = subprocess.run([pdftotext, "-v"], capture_output=True, text=True, timeout=5)
            out = res.stderr or res.stdout
            m = re.search(r"pdftotext version\s+([\d\.]+)", out)
            if m:
                versions["poppler"] = m.group(1)
        except Exception:
            pass
    tesseract = shutil.which("tesseract")
    if tesseract:
        try:
            res = subprocess.run([tesseract, "--version"], capture_output=True, text=True, timeout=5)
            out = res.stdout or res.stderr
            m = re.search(r"tesseract\s+([\d\.]+)", out)
            if m:
                versions["tesseract"] = m.group(1)
        except Exception:
            pass
    return versions


def try_tesseract_ocr(
    pdf_path: str,
    page_num: int,
    policy: Optional[OCRPolicy] = None
) -> Tuple[Optional[str], Optional[float], List[ContentBlock]]:
    """
    Execute high-resolution OCR on a specific PDF page using pdftoppm + tesseract.
    Policy:
    - pdftoppm -r {policy.dpi} (default 300 DPI)
    - Image preprocessing: convert to grayscale and apply autocontrast stretch
    - tesseract --psm {policy.psm_prose} with fallback to --psm {policy.psm_form}
    - Quality quarantine: pages with mean_conf < policy.confidence_floor are quarantined (returns None, mean_conf, [])
    - Language allowlist (default 'eng')
    - Returns: (extracted_text, mean_confidence_0_to_1, content_blocks)
    """
    if policy is None:
        policy = OCRPolicy()

    tess_path = shutil.which("tesseract")
    ppm_path = shutil.which("pdftoppm")
    if not tess_path or not ppm_path:
        return None, None, []

    env = dict(os.environ)

    with tempfile.TemporaryDirectory() as tmpdir:
        img_prefix = os.path.join(tmpdir, f"page_{page_num}")
        ppm_cmd = [
            ppm_path, "-png", "-r", str(policy.dpi),
            "-f", str(page_num), "-l", str(page_num),
            pdf_path, img_prefix
        ]
        try:
            subprocess.run(ppm_cmd, capture_output=True, text=True, timeout=policy.timeout_seconds, check=True)
        except Exception as e:
            logger.warning(f"pdftoppm failed for page {page_num}: {e}")
            return None, None, []

        files = [f for f in os.listdir(tmpdir) if f.startswith(f"page_{page_num}") and f.endswith(".png")]
        if not files:
            return None, None, []

        img_file = os.path.join(tmpdir, files[0])

        # Image bomb safeguard & quality preprocessing (grayscale + gentle contrast enhancement with DPI preservation)
        try:
            from PIL import Image, ImageOps, ImageEnhance
            with Image.open(img_file) as img:
                w, h = img.size
                if (w * h) > policy.max_pixels:
                    logger.warning(f"Image bomb protection: page {page_num} ({w}x{h}={w*h}px) exceeds cap of {policy.max_pixels}px")
                    return None, None, []
                # Preprocessing: preserve DPI, convert to grayscale, and expand dynamic range
                dpi = img.info.get("dpi", (policy.dpi, policy.dpi))
                gray = img.convert("L")
                gray = ImageOps.autocontrast(gray, cutoff=0)
                enhancer = ImageEnhance.Contrast(gray)
                enhanced = enhancer.enhance(1.2)
                enhanced.save(img_file, dpi=dpi)
        except Exception as im_e:
            logger.debug(f"Image preprocessing check skipped: {im_e}")

        # Try PSM prose first, fallback to PSM form
        psm_candidates = [str(policy.psm_prose), str(policy.psm_form)]
        low_confidence_encountered = False
        lowest_conf: Optional[float] = None

        for psm_val in psm_candidates:
            tsv_cmd = [tess_path, img_file, "stdout", "--oem", "1", "--psm", psm_val, "-l", policy.language, "tsv"]
            try:
                tsv_res = subprocess.run(tsv_cmd, capture_output=True, text=True, timeout=policy.timeout_seconds, env=env, check=False)
                if tsv_res.returncode == 0 and tsv_res.stdout.strip():
                    lines = tsv_res.stdout.splitlines()
                    words = []
                    confs = []
                    blocks: List[ContentBlock] = []
                    current_line_words: List[str] = []
                    current_line_boxes: List[Tuple[float, float, float, float]] = []
                    current_line_confs: List[float] = []
                    current_line_num: Optional[int] = None
                    current_block_num: Optional[int] = None

                    def _flush_line():
                        if not current_line_words:
                            return
                        l_text = " ".join(current_line_words)
                        l_bbox = None
                        if current_line_boxes:
                            min_l = min(b[0] for b in current_line_boxes)
                            min_t = min(b[1] for b in current_line_boxes)
                            max_r = max(b[0] + b[2] for b in current_line_boxes)
                            max_b = max(b[1] + b[3] for b in current_line_boxes)
                            scale = 72.0 / float(policy.dpi)
                            l_bbox = [
                                round(min_l * scale, 2),
                                round(min_t * scale, 2),
                                round((max_r - min_l) * scale, 2),
                                round((max_b - min_t) * scale, 2)
                            ]
                        l_conf = (sum(current_line_confs) / (100.0 * len(current_line_confs))) if current_line_confs else None
                        blocks.append(ContentBlock(
                            text=l_text,
                            block_type="paragraph",
                            bbox=l_bbox,
                            confidence=l_conf
                        ))

                    for row in lines[1:]:
                        parts = row.split('\t')
                        if len(parts) >= 12:
                            try:
                                block_num = int(parts[2])
                                line_num = int(parts[4])
                                left = float(parts[6])
                                top = float(parts[7])
                                width = float(parts[8])
                                height = float(parts[9])
                                conf = float(parts[10])
                                w_text = parts[11].strip()

                                if conf >= 0 and w_text:
                                    confs.append(conf)
                                    words.append(w_text)
                                    if current_line_num is not None and (line_num != current_line_num or (current_block_num is not None and block_num != current_block_num)):
                                        _flush_line()
                                        current_line_words = []
                                        current_line_boxes = []
                                        current_line_confs = []
                                    current_line_num = line_num
                                    current_block_num = block_num
                                    current_line_words.append(w_text)
                                    current_line_boxes.append((left, top, width, height))
                                    current_line_confs.append(conf)
                            except (ValueError, IndexError):
                                continue

                    _flush_line()

                    mean_conf = (sum(confs) / (100.0 * len(confs))) if confs else None
                    if mean_conf is not None and mean_conf < policy.confidence_floor:
                        logger.warning(
                            f"OCR mean confidence {mean_conf:.2f} below floor {policy.confidence_floor:.2f} "
                            f"on page {page_num}; quarantining page from corpus."
                        )
                        low_confidence_encountered = True
                        lowest_conf = mean_conf
                        continue  # Try next candidate or quarantine

                    extracted = "\n\n".join(b.text for b in blocks) if blocks else " ".join(words)
                    printable = "".join(c for c in extracted if c.isalnum() or c in " .,;:!?-\n")
                    if len(printable) >= 10:
                        return OCRResult(extracted, mean_conf, blocks)
            except Exception as e:
                logger.debug(f"Tesseract TSV psm={psm_val} failed for page {page_num}: {e}")

        # If TSV returned low-confidence result below floor, strictly quarantine without running fallback
        if low_confidence_encountered:
            quarantine_dir = os.path.expanduser("~/.cache/krusch_nexus/quarantine")
            os.makedirs(quarantine_dir, exist_ok=True)
            quarantine_img = os.path.join(quarantine_dir, f"quarantine_p{page_num}_{os.path.basename(pdf_path)}.png")
            try:
                shutil.copy2(img_file, quarantine_img)
            except Exception:
                quarantine_img = None
            return OCRResult(None, lowest_conf, [], image_path=quarantine_img)

        # Plain text fallback (only if TSV was completely empty or failed)
        ocr_cmd = [tess_path, img_file, "stdout", "--oem", "1", "--psm", str(policy.psm_prose), "-l", policy.language]
        try:
            ocr_res = subprocess.run(ocr_cmd, capture_output=True, text=True, timeout=policy.timeout_seconds, env=env, check=False)
            if ocr_res.returncode == 0:
                extracted = ocr_res.stdout.strip()
                printable = "".join(c for c in extracted if c.isalnum() or c in " .,;:!?-\n")
                if len(printable) >= 10:
                    return OCRResult(extracted, policy.confidence_floor + 0.1, [ContentBlock(text=extracted, block_type="paragraph")])
        except Exception as e:
            logger.warning(f"Tesseract fallback failed for page {page_num}: {e}")

    return OCRResult(None, None, [])
