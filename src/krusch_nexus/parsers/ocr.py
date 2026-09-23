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


def try_tesseract_ocr(
    pdf_path: str,
    page_num: int,
    policy: Optional[OCRPolicy] = None
) -> Tuple[Optional[str], Optional[float], List[ContentBlock]]:
    """
    Execute high-resolution OCR on a specific PDF page using pdftoppm + tesseract.
    Policy:
    - pdftoppm -r {policy.dpi} (default 300 DPI)
    - tesseract --psm {policy.psm_prose} with fallback to --psm {policy.psm_form}
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

        # Image bomb safeguard: verify pixel count
        try:
            from PIL import Image
            with Image.open(img_file) as img:
                w, h = img.size
                if (w * h) > policy.max_pixels:
                    logger.warning(f"Image bomb protection: page {page_num} ({w}x{h}={w*h}px) exceeds cap of {policy.max_pixels}px")
                    return None, None, []
        except Exception as im_e:
            logger.debug(f"Image dimension check skipped: {im_e}")

        # Try PSM prose first, fallback to PSM form
        psm_candidates = [str(policy.psm_prose), str(policy.psm_form)]
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
                    current_line_num: Optional[int] = None
                    current_block_num: Optional[int] = None

                    for row in lines[1:]:
                        parts = row.split('\t')
                        if len(parts) >= 12:
                            try:
                                block_num = int(parts[2])
                                line_num = int(parts[4])
                                conf = float(parts[10])
                                w_text = parts[11].strip()

                                if conf >= 0 and w_text:
                                    confs.append(conf)
                                    words.append(w_text)
                                    if current_line_num is not None and (line_num != current_line_num or (current_block_num is not None and block_num != current_block_num)):
                                        if current_line_words:
                                            blocks.append(ContentBlock(
                                                text=" ".join(current_line_words),
                                                block_type="paragraph"
                                            ))
                                            current_line_words = []
                                    current_line_num = line_num
                                    current_block_num = block_num
                                    current_line_words.append(w_text)
                            except (ValueError, IndexError):
                                continue

                    if current_line_words:
                        blocks.append(ContentBlock(
                            text=" ".join(current_line_words),
                            block_type="paragraph"
                        ))

                    mean_conf = (sum(confs) / (100.0 * len(confs))) if confs else None
                    extracted = "\n\n".join(b.text for b in blocks) if blocks else " ".join(words)
                    printable = "".join(c for c in extracted if c.isalnum() or c in " .,;:!?-\n")
                    if len(printable) >= 10:
                        return extracted, mean_conf, blocks
            except Exception as e:
                logger.debug(f"Tesseract TSV psm={psm_val} failed for page {page_num}: {e}")

        # Plain text fallback
        ocr_cmd = [tess_path, img_file, "stdout", "--oem", "1", "--psm", str(policy.psm_prose), "-l", policy.language]
        try:
            ocr_res = subprocess.run(ocr_cmd, capture_output=True, text=True, timeout=policy.timeout_seconds, env=env, check=False)
            if ocr_res.returncode == 0:
                extracted = ocr_res.stdout.strip()
                printable = "".join(c for c in extracted if c.isalnum() or c in " .,;:!?-\n")
                if len(printable) >= 10:
                    return extracted, 0.85, [ContentBlock(text=extracted, block_type="paragraph")]
        except Exception as e:
            logger.warning(f"Tesseract fallback failed for page {page_num}: {e}")

    return None, None, []
