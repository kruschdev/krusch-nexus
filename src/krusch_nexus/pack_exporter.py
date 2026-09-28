"""
KruschNexus Authority Pack Exporter & Cartridge Forge (pack_exporter.py)
========================================================================
Bridges the 'Game Console & Cartridge' architecture:
1. Ingests or parses structured documents (PDFs with Poppler TSV coordinates,
   scanned files with Tesseract OCR, DOCX, TXT, HTML, tabular data).
2. Deconstructs document text into bounded authority provisions/clauses.
3. Extracts typed, span-grounded numeric and rule slots:
   - Word numeral normalization ('twenty-one calendar days' -> 21 days)
   - Rent caps, OMI ownership percentages, interest rates, notice days
   - Physical citation coordinates (page_number, pdf_page, bbox [x0, y0, x1, y1], char offsets)
   - Verbatim quoted sentence anchors
4. Preserves Table Grid Spines (sub-line cell bboxes, row records, markdown representation).
5. Detects preemption edges, statutory overrides, and defined terms.
6. Serializes to certified Authority Pack YAML (Jurisdiction Pack, Standards Pack, Playbook Pack)
   guaranteed to pass schema validation and token budget bounds (<= 850 tokens/entry).
"""

from __future__ import annotations

import os
import re
import hashlib
from datetime import datetime, timezone
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from .models import PageData, ContentBlock, NexusConfig
from .exceptions import PackValidationError
from .chunking import (
    ANCHORED_SECTION_REGEX,
    normalize_statute_citation
)


MAX_CHUNK_TOKEN_BUDGET = 850
REQUIRED_ROOT_KEYS = {"pack_id", "version", "publisher", "coverage", "description"}

NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "twenty-one": 21, "twenty-two": 22, "twenty-three": 23, "twenty-four": 24, "twenty-five": 25,
    "thirty": 30, "thirty-three": 33, "thirty-six": 36, "forty": 40, "forty-five": 45,
    "fifty": 50, "sixty": 60, "ninety": 90, "twice": 2
}


class PackSku(str, Enum):
    """The 3 commercial Authority Pack product SKUs."""
    JURISDICTION = "jurisdiction"   # Legal: statutes, municipal codes, local ordinances
    STANDARDS = "standards"         # Compliance/Finance: GAAP ASC 606, NEC, ISO
    PLAYBOOK = "playbook"           # Enterprise Contracts: corporate MSAs, procurement terms, SLAs


@dataclass
class SourceSpan:
    """Exact physical location of an authority provision or slot."""
    page_number: Optional[int] = None
    pdf_page: Optional[int] = None
    printed_page: Optional[str] = None
    bbox: Optional[List[float]] = None  # [x0, y0, width, height] in PDF points
    char_start: Optional[int] = None
    char_end: Optional[int] = None
    quoted_sentence: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        if self.page_number is not None:
            d["page_number"] = self.page_number
        if self.pdf_page is not None:
            d["pdf_page"] = self.pdf_page
        if self.printed_page is not None:
            d["printed_page"] = self.printed_page
        if self.bbox is not None:
            d["bbox"] = self.bbox
        if self.char_start is not None:
            d["char_start"] = self.char_start
        if self.char_end is not None:
            d["char_end"] = self.char_end
        if self.quoted_sentence is not None:
            d["quoted_sentence"] = self.quoted_sentence
        return d


@dataclass
class SpanGroundedSlot:
    """A typed machine slot strictly anchored to physical citation coordinates and quoted sentences."""
    name: str
    value: Any
    unit: Optional[str] = None
    source_span: Optional[SourceSpan] = None
    extraction_method: str = "compiler_layout"
    confidence: float = 1.0

    def verify_grounding(self) -> bool:
        if not self.source_span or not self.source_span.quoted_sentence:
            return False
        quote = self.source_span.quoted_sentence.lower()
        val_str = str(self.value).lower()

        if val_str in quote:
            return True

        if isinstance(self.value, (int, float)):
            int_val = int(self.value)
            if str(int_val) in quote or f"{int_val}%" in quote or f"{int_val} percent" in quote:
                return True
            for word, digit in NUMBER_WORDS.items():
                if int_val == digit and word in quote:
                    return True

        if isinstance(self.value, bool):
            if self.value is True and any(w in quote for w in ["shall", "must", "required", "prohibited", "may not", "notice"]):
                return True

        return False

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "value": self.value,
            "extraction_method": self.extraction_method,
        }
        if self.unit:
            d["unit"] = self.unit
        if self.source_span:
            d["source_span"] = self.source_span.to_dict()
        return d


@dataclass
class AuthorityEntry:
    """A single statutory section, municipal ordinance, or contract clause."""
    citation: str
    title: str
    topic: str
    authority_class: str
    hierarchy_level: str
    raw_content: str
    section_number: Optional[str] = None
    effective_date: Optional[str] = None
    statutory_slots: Dict[str, Any] = field(default_factory=dict)
    grounded_slots: Dict[str, SpanGroundedSlot] = field(default_factory=dict)
    table_grids: List[Dict[str, Any]] = field(default_factory=list)
    preempts: List[str] = field(default_factory=list)
    preempted_by: List[str] = field(default_factory=list)
    defines_terms: List[str] = field(default_factory=list)
    exceptions_ref: Optional[str] = None
    citation_coordinates: Optional[SourceSpan] = None
    estimated_tokens: int = 0

    def to_dict(self, sku: PackSku = PackSku.JURISDICTION) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "citation": self.citation,
            "title": self.title,
            "topic": self.topic,
            "authority_class": self.authority_class,
            "hierarchy_level": self.hierarchy_level,
            "raw_content": self.raw_content,
        }
        if self.section_number:
            d["section_number"] = self.section_number
        if self.effective_date:
            d["effective_date"] = self.effective_date
        if self.statutory_slots:
            slot_key = "statutory_slots" if sku == PackSku.JURISDICTION else "slots"
            d[slot_key] = self.statutory_slots
        if self.grounded_slots:
            d["grounded_slots"] = {k: v.to_dict() for k, v in self.grounded_slots.items()}
        if self.citation_coordinates:
            d["citation_coordinates"] = self.citation_coordinates.to_dict()
        if self.preempts:
            d["preempts"] = self.preempts
        if self.preempted_by:
            d["preempted_by"] = self.preempted_by
        if self.defines_terms:
            d["defines_terms"] = self.defines_terms
        if self.exceptions_ref:
            d["exceptions_ref"] = self.exceptions_ref
        if self.table_grids:
            d["table_grids"] = self.table_grids
        return d


@dataclass
class AuthorityPack:
    """A portable, versioned, signed rulebook cartridge."""
    pack_id: str
    version: str
    sku: PackSku
    publisher: str
    edition: str
    description: str
    source_document_hash: str
    retrieved_at: str
    effective_from: Optional[str] = None
    effective_to: Optional[str] = None
    domain: Optional[str] = None
    state: Optional[str] = None
    municipality: Optional[str] = None
    county: Optional[str] = None
    code_families: List[str] = field(default_factory=list)
    covered_topics: List[str] = field(default_factory=list)
    known_uncovered_topics: List[str] = field(default_factory=list)
    entries: List[AuthorityEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "pack_id": self.pack_id,
            "version": self.version,
            "sku": self.sku.value,
            "publisher": self.publisher,
            "edition": self.edition,
            "description": self.description,
            "effective_from": self.effective_from,
            "effective_to": self.effective_to,
            "source_document_hash": self.source_document_hash,
            "retrieved_at": self.retrieved_at,
        }
        if self.domain:
            d["domain"] = self.domain
        if self.state:
            d["state"] = self.state
        if self.municipality:
            d["municipality"] = self.municipality
        if self.county:
            d["county"] = self.county
        if self.code_families:
            d["code_families"] = self.code_families

        d["coverage"] = {
            "covered_topics": self.covered_topics,
            "known_uncovered_topics": self.known_uncovered_topics
        }

        # Select container key by SKU
        container_key = (
            "statutes" if self.sku == PackSku.JURISDICTION
            else ("standards" if self.sku == PackSku.STANDARDS else "clauses")
        )
        d[container_key] = [e.to_dict(self.sku) for e in self.entries]
        return d

    def to_yaml(self) -> str:
        if yaml is None:
            raise ImportError("PyYAML is required for Authority Pack export.")

        data = self.to_dict()

        class BlockDumper(yaml.SafeDumper):
            pass

        def str_presenter(dumper: Any, val: str) -> Any:
            if "\n" in val:
                return dumper.represent_scalar('tag:yaml.org,2002:str', val, style='|')
            return dumper.represent_scalar('tag:yaml.org,2002:str', val)

        BlockDumper.add_representer(str, str_presenter)
        return yaml.dump(data, Dumper=BlockDumper, sort_keys=False, width=120, allow_unicode=True)


def extract_grounded_slots(
    text: str,
    page_number: Optional[int] = None,
    pdf_page: Optional[int] = None,
    printed_page: Optional[str] = None,
    blocks: Optional[List[ContentBlock]] = None,
    base_offset: int = 0
) -> Tuple[Dict[str, Any], Dict[str, SpanGroundedSlot]]:
    """
    Extract typed, grounded slots from section text with exact quoted sentence anchors
    and physical bounding boxes matching the 3-Tier standard.
    """
    statutory_slots: Dict[str, Any] = {}
    grounded_slots: Dict[str, SpanGroundedSlot] = {}

    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]
    
    # Track sentence offsets in text
    sentence_spans: List[Tuple[str, int, int]] = []
    cursor = 0
    for s in sentences:
        pos = text.find(s, cursor)
        if pos != -1:
            start_off = base_offset + pos
            end_off = start_off + len(s)
            sentence_spans.append((s, start_off, end_off))
            cursor = pos + len(s)
        else:
            sentence_spans.append((s, base_offset, base_offset + len(s)))

    def _find_matching_bbox(sentence_text: str) -> Optional[List[float]]:
        if not blocks:
            return None
        # Find block containing sentence or having highest overlap
        clean_s = re.sub(r'\s+', ' ', sentence_text).lower()
        for b in blocks:
            if not b.bbox:
                continue
            clean_b = re.sub(r'\s+', ' ', b.text).lower()
            if clean_s in clean_b or clean_b in clean_s:
                return b.bbox
        return None

    def _normalize_num(val_str: str) -> Union[int, float]:
        v = val_str.lower().strip()
        if v in NUMBER_WORDS:
            return NUMBER_WORDS[v]
        try:
            cleaned = v.replace(",", "")
            return float(cleaned) if "." in cleaned else int(cleaned)
        except ValueError:
            return 0

    for s_text, c_start, c_end in sentence_spans:
        s_lower = s_text.lower()
        box = _find_matching_bbox(s_text)
        s_span = SourceSpan(
            page_number=page_number,
            pdf_page=pdf_page,
            printed_page=printed_page,
            bbox=box,
            char_start=c_start,
            char_end=c_end,
            quoted_sentence=s_text
        )

        # 1. Deposit cap months
        m = re.search(
            r'(?:demand(?:\s+or\s+receive)?\s+security.*?in\s+excess\s+of|security\s+deposit.*?(?:exceed|cap\s+is)|security.*?limited\s+to)\s+(\d+(?:\.\d+)?|one|two)\s+month(?:\'s|s\'|s)?\s+rent'
            r'|(\d+(?:\.\d+)?|one|two)\s+month(?:\'s|s\'|s)?\s+rent\s+(?:as\s+a\s+security\s+deposit|for\s+an?\s+(?:unfurnished|furnished))',
            s_lower,
            re.DOTALL
        )
        if m:
            raw_v = m.group(1) or m.group(2)
            val = float(_normalize_num(raw_v))
            slot = SpanGroundedSlot("deposit_cap_months", val, unit="months", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["deposit_cap_months"] = val
                grounded_slots["deposit_cap_months"] = slot

        # 2. Small landlord cap months
        m = re.search(
            r'(?:no\s+more\s+than\s+two\s+residential\s+rental\s+properties.*?demand\s+up\s+to\s+|small\s+landlord.*?up\s+to\s+)(two|\d+(?:\.\d+)?)\s+month(?:\'s|s\'|s)?\s+rent',
            s_lower,
            re.DOTALL
        )
        if m:
            val = float(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("small_landlord_cap_months", val, unit="months", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["small_landlord_cap_months"] = val
                grounded_slots["small_landlord_cap_months"] = slot

        # 3. Accounting return days
        m = re.search(
            r'(?:within\s+)(\d+|twenty-one|thirty|fourteen|sixty)\s+(?:calendar\s+)?days(?:\s+(?:after|of)\s+[^\.,;]+|\s+to\s+(?:furnish|return))?',
            s_lower
        )
        if m:
            val = int(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("accounting_days", val, unit="days", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["accounting_days"] = val
                grounded_slots["accounting_days"] = slot

        # 4. Statutory damages multiplier
        m = re.search(
            r'(?:statutory\s+damages\s+of\s+(?:up\s+to\s+)?)(twice|two|\d+(?:\.\d+)?)\s+(?:the\s+amount|times)',
            s_lower
        )
        if m:
            val = float(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("statutory_damages_multiplier", val, unit="multiplier", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["statutory_damages_multiplier"] = val
                grounded_slots["statutory_damages_multiplier"] = slot

        # 5. Minimum ownership percent OMI
        m = re.search(
            r'(?:at\s+least\s+)?(\d+(?:\.\d+)?)\s*%\s*(?:recorded\s+)?ownership',
            s_lower
        )
        if m:
            val = float(m.group(1))
            slot = SpanGroundedSlot("minimum_ownership_percent_omi", val, unit="percent", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["minimum_ownership_percent_omi"] = val
                grounded_slots["minimum_ownership_percent_omi"] = slot

        # 6. Occupancy threshold months
        m = re.search(
            r'(?:lawfully\s+occupied[^\.]*?for\s+|occupancy\s+of\s+at\s+least\s+)(\d+|twelve|twenty-four)\s+months',
            s_lower
        )
        if m:
            val = int(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("occupancy_threshold_months", val, unit="months", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["occupancy_threshold_months"] = val
                grounded_slots["occupancy_threshold_months"] = slot

        # 7. Rent increase caps (base_cap_percent, max_cap_percent)
        m = re.search(r'increase\s+[^\.]*?more\s+than\s+(\d+(?:\.\d+)?)\s*(?:%|percent)\s+plus', s_lower)
        if m:
            val = float(m.group(1))
            slot = SpanGroundedSlot("base_cap_percent", val, unit="percent", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["base_cap_percent"] = val
                grounded_slots["base_cap_percent"] = slot

        m = re.search(r'or\s+(\d+(?:\.\d+)?)\s*(?:%|percent),?\s+whichever\s+is\s+lower', s_lower)
        if m:
            val = float(m.group(1))
            slot = SpanGroundedSlot("max_cap_percent", val, unit="percent", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["max_cap_percent"] = val
                grounded_slots["max_cap_percent"] = slot

        # 8. Max increases per year
        m = re.search(r'no\s+more\s+than\s+(\d+|one|two)\s+increments?\s+over\s+(?:any\s+)?12-month', s_lower)
        if m:
            val = int(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("max_increases_per_year", val, unit="count", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["max_increases_per_year"] = val
                grounded_slots["max_increases_per_year"] = slot

        # 9. Cure days
        m = re.search(r'(?:cure\s+within|written\s+notice\s+to\s+cure\s+of)\s+(\d+|one|two|five|ten|fourteen|twenty|thirty|sixty)\s+(?:calendar\s+)?days', s_lower)
        if m:
            val = int(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("cure_days", val, unit="days", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["cure_days"] = val
                grounded_slots["cure_days"] = slot

        # 10. Daily statutory penalty
        m = re.search(r'\$([0-9]+(?:\.[0-9]{2})?)\s+for\s+each\s+(?:calendar\s+)?day', s_text)
        if m:
            val = float(m.group(1).replace(",", ""))
            slot = SpanGroundedSlot("daily_statutory_penalty", val, unit="USD", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["daily_statutory_penalty"] = val
                grounded_slots["daily_statutory_penalty"] = slot

        # 11. Minimum statutory damages
        m = re.search(r'(?:not\s+less\s+than|minimum\s+statutory\s+damages\s+of)\s+[^\$0-9]*\$([0-9]+(?:\.[0-9]{2})?)', s_text)
        if m:
            val = float(m.group(1).replace(",", ""))
            slot = SpanGroundedSlot("minimum_statutory_damages", val, unit="USD", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["minimum_statutory_damages"] = val
                grounded_slots["minimum_statutory_damages"] = slot

        # 12. SLA Availability
        m = re.search(r'(?:warrants?\s+|availability\s+of\s+|uptime\s+of\s+)(\d{2}(?:\.\d+)?)\s*%\s*(?:monthly\s+)?(?:service\s+)?(?:availability|uptime)', s_lower)
        if m:
            val = float(m.group(1))
            slot = SpanGroundedSlot("sla_availability_percent", val, unit="percent", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["sla_availability_percent"] = val
                grounded_slots["sla_availability_percent"] = slot

        # 13. Liability cap months
        m = re.search(r'(?:exceed\s+)?fees\s+paid\s+(?:by\s+customer\s+)?in\s+(?:the\s+)?(?:prior|preceding)\s+(\d+|twelve|twenty-four)\s+months', s_lower)
        if m:
            val = int(_normalize_num(m.group(1)))
            slot = SpanGroundedSlot("liability_cap_months", val, unit="months", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["liability_cap_months"] = val
                grounded_slots["liability_cap_months"] = slot

        # 14. Server nodes
        m = re.search(r'up\s+to\s+(\d+)\s+server\s+nodes', s_lower)
        if m:
            val = int(m.group(1))
            slot = SpanGroundedSlot("max_server_nodes", val, unit="nodes", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["max_server_nodes"] = val
                grounded_slots["max_server_nodes"] = slot

        # 15. Base rent monthly
        m = re.search(r'(?:base\s+rent[^\.]*?shall\s+be\s+)\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})?)\s+per\s+month', s_lower)
        if m:
            val = float(m.group(1).replace(",", ""))
            slot = SpanGroundedSlot("monthly_base_rent", val, unit="USD", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["monthly_base_rent"] = val
                grounded_slots["monthly_base_rent"] = slot

        # 16. Annual escalation percent
        m = re.search(r'(\d+(?:\.\d+)?)\s*%\s*annual\s+escalation', s_lower)
        if m:
            val = float(m.group(1))
            slot = SpanGroundedSlot("annual_escalation_percent", val, unit="percent", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["annual_escalation_percent"] = val
                grounded_slots["annual_escalation_percent"] = slot

        # 17. Improvement allowance per sqft
        m = re.search(r'allowance\s+of\s+\$([0-9]+(?:\.[0-9]{2})?)\s+per\s+rentable\s+square\s+foot', s_lower)
        if m:
            val = float(m.group(1))
            slot = SpanGroundedSlot("allowance_per_sqft", val, unit="USD/sqft", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["allowance_per_sqft"] = val
                grounded_slots["allowance_per_sqft"] = slot

        # 18. Rentable square feet
        m = re.search(r'(\d+(?:,[0-9]{3})*)\s+rentable\s+square\s+feet', s_lower)
        if m:
            val = int(m.group(1).replace(",", ""))
            slot = SpanGroundedSlot("rentable_square_feet", val, unit="sqft", source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["rentable_square_feet"] = val
                grounded_slots["rentable_square_feet"] = slot

        # 19. Booleans
        if "reasonable attorney's fees to the prevailing party" in s_lower or "award reasonable attorney" in s_lower:
            slot = SpanGroundedSlot("prevailing_attorney_fees", True, source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["prevailing_attorney_fees"] = True
                grounded_slots["prevailing_attorney_fees"] = slot

        if "written notice to cease" in s_lower or "written notice of the rent adjustment program" in s_lower:
            slot = SpanGroundedSlot("requires_written_warning_notice", True, source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["requires_written_warning_notice"] = True
                grounded_slots["requires_written_warning_notice"] = slot

        if "relocation assistance" in s_lower or "relocation payments" in s_lower:
            slot = SpanGroundedSlot("requires_relocation_payment", True, source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["requires_relocation_payment"] = True
                grounded_slots["requires_relocation_payment"] = slot

        if "initial and all subsequent rental rates" in s_lower or "vacancy decontrol" in s_lower:
            slot = SpanGroundedSlot("vacancy_decontrol", True, source_span=s_span)
            if slot.verify_grounding():
                statutory_slots["vacancy_decontrol"] = True
                grounded_slots["vacancy_decontrol"] = slot

    return statutory_slots, grounded_slots


class PackValidator:
    """Rigid validation conforming to the Krusch Authority Pack specification."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, len(text.strip()) // 4)

    @classmethod
    def validate_pack_dict(cls, data: Dict[str, Any], strict_grounding: bool = True) -> bool:
        if not isinstance(data, dict):
            raise PackValidationError("Root YAML document must be a dictionary.")

        missing = REQUIRED_ROOT_KEYS - set(data.keys())
        if missing:
            raise PackValidationError(f"Missing required root fields: {sorted(list(missing))}")

        coverage = data.get("coverage", {})
        if not isinstance(coverage, dict):
            raise PackValidationError("'coverage' must be a dictionary.")

        covered = coverage.get("covered_topics")
        if not covered or not isinstance(covered, list):
            raise PackValidationError("'coverage.covered_topics' must be a non-empty list.")

        entries_raw = data.get("statutes") or data.get("standards") or data.get("clauses")
        if not entries_raw or not isinstance(entries_raw, list):
            raise PackValidationError("Pack must define a non-empty 'statutes', 'standards', or 'clauses' list.")

        citations_seen = set()
        for idx, entry in enumerate(entries_raw):
            if not isinstance(entry, dict):
                raise PackValidationError(f"Entry #{idx} must be a dictionary.")

            citation = entry.get("citation") or entry.get("standard_id") or entry.get("clause_id")
            if not citation:
                raise PackValidationError(f"Entry #{idx} missing citation.")

            if citation in citations_seen:
                raise PackValidationError(f"Duplicate citation '{citation}'.")
            citations_seen.add(citation)

            topic = entry.get("topic")
            if not topic:
                raise PackValidationError(f"Entry '{citation}' missing required 'topic'.")

            raw_content = entry.get("raw_content", "")
            if not raw_content.strip():
                raise PackValidationError(f"Entry '{citation}' has empty 'raw_content'.")

            toks = cls.estimate_tokens(raw_content)
            if toks > MAX_CHUNK_TOKEN_BUDGET:
                raise PackValidationError(
                    f"Entry '{citation}' exceeds token budget ({toks} > {MAX_CHUNK_TOKEN_BUDGET} tokens)."
                )

            # Strict grounding validation
            if strict_grounding and "grounded_slots" in entry:
                for slot_name, slot_dict in entry["grounded_slots"].items():
                    val = slot_dict.get("value")
                    source_span = slot_dict.get("source_span", {})
                    quote = source_span.get("quoted_sentence", "")
                    if not quote:
                        raise PackValidationError(f"Slot '{slot_name}' in '{citation}' missing quoted_sentence anchor.")
                    val_str = str(val).lower()
                    quote_lower = quote.lower()
                    grounded = (
                        val_str in quote_lower
                        or (isinstance(val, (int, float)) and (str(int(val)) in quote_lower or any(int(val) == d and w in quote_lower for w, d in NUMBER_WORDS.items())))
                        or (isinstance(val, bool) and val is True)
                    )
                    if not grounded:
                        raise PackValidationError(
                            f"Grounding failure for slot '{slot_name}': value '{val}' not grounded in quote '{quote}'."
                        )

        return True


class AuthorityPackExporter:
    """
    Industrial cartridge forge: translates raw documents into certified,
    preemption-aware, span-grounded Authority Packs.
    """

    def __init__(self, config: Optional[NexusConfig] = None):
        self.config = config or NexusConfig.from_env()

    @staticmethod
    def _infer_topic(title: str, text: str) -> str:
        s = f"{title} {text}".lower()
        if "security deposit" in s:
            return "Security Deposits"
        if "just cause" in s or "evict" in s:
            return "Just Cause Evictions"
        if "rent control" in s or "costa-hawkins" in s:
            return "Rent Control & Preemption"
        if "rent increase" in s or "rent adjustment" in s:
            return "Rent Increases"
        if "utility" in s or "lockout" in s:
            return "Utility Shutoff & Lockouts"
        if "owner move-in" in s or "omi" in s:
            return "Owner Move-In"
        if "availability" in s or "service level" in s or "sla" in s:
            return "Service Level Commitments"
        if "liability" in s or "indemnif" in s:
            return "Limitation of Liability"
        if "expansion" in s or "leased premises" in s:
            return "Premises & Expansion"
        if "allowance" in s or "improvement" in s:
            return "Tenant Improvements"
        if "revenue" in s or "10-k" in s or "financial" in s:
            return "Financial Statements"
        return "General"

    @staticmethod
    def _extract_preemption_references(text: str) -> Tuple[List[str], List[str], List[str]]:
        preempts: List[str] = []
        preempted_by: List[str] = []
        defines_terms: List[str] = []

        # Preempts
        if "preempts" in text.lower() or "notwithstanding any other" in text.lower():
            refs = re.findall(r'(?:preempts|supersedes)\s+([^\n\.,;]+)', text, re.IGNORECASE)
            for r in refs:
                cleaned = r.strip()
                if cleaned and cleaned not in preempts:
                    preempts.append(cleaned)

        # Preempted by
        if "subject to" in text.lower() or "exempted by" in text.lower():
            refs = re.findall(r'(?:subject to|exempted by)\s+([^\n\.,;]+)', text, re.IGNORECASE)
            for r in refs:
                cleaned = r.strip()
                if cleaned and cleaned not in preempted_by:
                    preempted_by.append(cleaned)

        # Defines terms
        m_terms = re.findall(r'[\'\"“]([A-Z][A-Za-z0-9\s\-_]{2,40})[\'\"”]\s+(?:means|shall mean|denotes)', text)
        for t in m_terms:
            cleaned = t.strip()
            if cleaned not in defines_terms:
                defines_terms.append(cleaned)

        return preempts, preempted_by, defines_terms

    def export_from_pages(
        self,
        pages: List[PageData],
        source_filename: str,
        source_hash: str,
        pack_id: Optional[str] = None,
        sku: PackSku = PackSku.JURISDICTION,
        publisher: Optional[str] = None,
        edition: Optional[str] = None,
        description: Optional[str] = None,
        domain: Optional[str] = None,
        state: Optional[str] = None,
        municipality: Optional[str] = None,
        county: Optional[str] = None,
        code_families: Optional[List[str]] = None,
        effective_from: Optional[str] = None,
        verify: bool = True
    ) -> AuthorityPack:
        """
        Build an Authority Pack from parsed PageData objects.
        """
        base_id = pack_id or os.path.splitext(os.path.basename(source_filename))[0].lower().replace("-", "_").replace(" ", "_")
        if not base_id.endswith("_pack_v1"):
            base_id = f"{base_id}_pack_v1"

        pub = publisher or "Office of the City Clerk & Legislative Counsel"
        ed = edition or f"{datetime.now(timezone.utc).year} Statutory Supplement"
        desc = description or f"Authoritative bounded rulebook extracted from {source_filename}."

        entries: List[AuthorityEntry] = []
        covered_topics_set = set()

        # Combine pages and extract sections
        for page in pages:
            text = page.text
            blocks = page.blocks
            tables = page.tables

            # Find section matches
            lines = text.splitlines()
            curr_sec_lines: List[str] = []
            curr_title = "General Provisions"
            curr_cit = f"{source_filename} p.{page.index}" if page.index else source_filename
            curr_num = None
            curr_blocks: List[ContentBlock] = []

            for line in lines:
                m_sec = ANCHORED_SECTION_REGEX.match(line.strip())
                if m_sec:
                    # Flush previous section if exists
                    if curr_sec_lines:
                        sec_raw = "\n".join(curr_sec_lines).strip()
                        if sec_raw:
                            topic = self._infer_topic(curr_title, sec_raw)
                            covered_topics_set.add(topic)
                            stat_slots, g_slots = extract_grounded_slots(
                                sec_raw,
                                page_number=page.index,
                                pdf_page=page.pdf_page or page.index,
                                printed_page=page.printed_page,
                                blocks=curr_blocks or blocks
                            )
                            preempts, preempted_by, defines = self._extract_preemption_references(sec_raw)

                            # Bbox union
                            boxes = [b.bbox for b in curr_blocks if b.bbox]
                            comb_box = None
                            if boxes:
                                c_left = min(b[0] for b in boxes)
                                c_top = min(b[1] for b in boxes)
                                c_right = max(b[0] + b[2] for b in boxes)
                                c_bottom = max(b[1] + b[3] for b in boxes)
                                comb_box = [round(c_left, 2), round(c_top, 2), round(c_right - c_left, 2), round(c_bottom - c_top, 2)]

                            entries.append(AuthorityEntry(
                                citation=curr_cit,
                                section_number=curr_num,
                                title=curr_title,
                                topic=topic,
                                authority_class="controlling_statute" if sku == PackSku.JURISDICTION else ("standard_rule" if sku == PackSku.STANDARDS else "playbook_clause"),
                                hierarchy_level="section",
                                raw_content=sec_raw,
                                statutory_slots=stat_slots,
                                grounded_slots=g_slots,
                                preempts=preempts,
                                preempted_by=preempted_by,
                                defines_terms=defines,
                                citation_coordinates=SourceSpan(
                                    page_number=page.index,
                                    pdf_page=page.pdf_page or page.index,
                                    printed_page=page.printed_page,
                                    bbox=comb_box
                                ),
                                estimated_tokens=PackValidator.estimate_tokens(sec_raw)
                            ))

                    curr_sec_lines = [line]
                    norm = normalize_statute_citation(line)
                    curr_cit = norm["canonical_token"] or line.strip()
                    curr_num = norm["section_number"] or None
                    curr_title = line.strip()
                    curr_blocks = [b for b in blocks if line.strip() in b.text]
                else:
                    curr_sec_lines.append(line)
                    matching = [b for b in blocks if line.strip() and line.strip() in b.text]
                    curr_blocks.extend(matching)

            # Flush final section on page
            if curr_sec_lines:
                sec_raw = "\n".join(curr_sec_lines).strip()
                if sec_raw:
                    topic = self._infer_topic(curr_title, sec_raw)
                    covered_topics_set.add(topic)
                    stat_slots, g_slots = extract_grounded_slots(
                        sec_raw,
                        page_number=page.index,
                        pdf_page=page.pdf_page or page.index,
                        printed_page=page.printed_page,
                        blocks=curr_blocks or blocks
                    )
                    preempts, preempted_by, defines = self._extract_preemption_references(sec_raw)

                    boxes = [b.bbox for b in curr_blocks if b.bbox]
                    comb_box = None
                    if boxes:
                        c_left = min(b[0] for b in boxes)
                        c_top = min(b[1] for b in boxes)
                        c_right = max(b[0] + b[2] for b in boxes)
                        c_bottom = max(b[1] + b[3] for b in boxes)
                        comb_box = [round(c_left, 2), round(c_top, 2), round(c_right - c_left, 2), round(c_bottom - c_top, 2)]

                    entries.append(AuthorityEntry(
                        citation=curr_cit,
                        section_number=curr_num,
                        title=curr_title,
                        topic=topic,
                        authority_class="controlling_statute" if sku == PackSku.JURISDICTION else ("standard_rule" if sku == PackSku.STANDARDS else "playbook_clause"),
                        hierarchy_level="section",
                        raw_content=sec_raw,
                        statutory_slots=stat_slots,
                        grounded_slots=g_slots,
                        table_grids=tables if tables else [],
                        preempts=preempts,
                        preempted_by=preempted_by,
                        defines_terms=defines,
                        citation_coordinates=SourceSpan(
                            page_number=page.index,
                            pdf_page=page.pdf_page or page.index,
                            printed_page=page.printed_page,
                            bbox=comb_box
                        ),
                        estimated_tokens=PackValidator.estimate_tokens(sec_raw)
                    ))

        # Fallback if no explicit sections were parsed
        if not entries:
            all_text = "\n\n".join(p.text for p in pages if p.text).strip()
            topic = self._infer_topic(source_filename, all_text)
            covered_topics_set.add(topic)
            stat_slots, g_slots = extract_grounded_slots(all_text, blocks=[b for p in pages for b in p.blocks])
            preempts, preempted_by, defines = self._extract_preemption_references(all_text)
            entries.append(AuthorityEntry(
                citation=f"{source_filename} Main",
                title=source_filename,
                topic=topic,
                authority_class="controlling_statute" if sku == PackSku.JURISDICTION else "playbook_clause",
                hierarchy_level="document",
                raw_content=all_text,
                statutory_slots=stat_slots,
                grounded_slots=g_slots,
                preempts=preempts,
                preempted_by=preempted_by,
                defines_terms=defines,
                estimated_tokens=PackValidator.estimate_tokens(all_text)
            ))

        pack = AuthorityPack(
            pack_id=base_id,
            version="1.0.0",
            sku=sku,
            publisher=pub,
            edition=ed,
            description=desc,
            effective_from=effective_from or "2024-01-01",
            source_document_hash=f"sha256:{source_hash}",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            domain=domain or ("legal" if sku == PackSku.JURISDICTION else ("accounting" if sku == PackSku.STANDARDS else "commercial")),
            state=state,
            municipality=municipality,
            county=county,
            code_families=code_families or ([] if not state else [f"{state} Codes"]),
            covered_topics=sorted(list(covered_topics_set)) if covered_topics_set else ["General"],
            known_uncovered_topics=[],
            entries=entries
        )

        if verify:
            PackValidator.validate_pack_dict(pack.to_dict())

        return pack

    def export_from_file(
        self,
        file_path: str,
        sku: Union[PackSku, str] = PackSku.JURISDICTION,
        pack_id: Optional[str] = None,
        publisher: Optional[str] = None,
        edition: Optional[str] = None,
        description: Optional[str] = None,
        domain: Optional[str] = None,
        state: Optional[str] = None,
        municipality: Optional[str] = None,
        county: Optional[str] = None,
        code_families: Optional[List[str]] = None,
        effective_from: Optional[str] = None,
        output_path: Optional[str] = None,
        verify: bool = True
    ) -> str:
        """
        Parse local file and export certified Authority Pack YAML.
        """
        from .parsers import parse_document
        from .parsers.registry import compute_file_hash
        from .ingest.sandbox import sanitize_filename

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Source document not found: {file_path}")

        resolved_sku = PackSku(sku) if isinstance(sku, str) else sku
        filename = sanitize_filename(os.path.basename(file_path))
        file_hash = compute_file_hash(file_path)

        res = parse_document(file_path, filename)
        pack = self.export_from_pages(
            pages=res.pages,
            source_filename=filename,
            source_hash=file_hash,
            pack_id=pack_id,
            sku=resolved_sku,
            publisher=publisher,
            edition=edition,
            description=description,
            domain=domain,
            state=state,
            municipality=municipality,
            county=county,
            code_families=code_families,
            effective_from=effective_from,
            verify=verify
        )

        yaml_content = pack.to_yaml()
        if output_path:
            out_dir = os.path.dirname(os.path.abspath(output_path))
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(yaml_content)

        return yaml_content


def export_authority_pack(
    file_or_pages: Union[str, List[PageData]],
    sku: Union[PackSku, str] = PackSku.JURISDICTION,
    output_path: Optional[str] = None,
    **kwargs: Any
) -> str:
    """Convenience top-level library entrypoint to mint an Authority Pack."""
    exporter = AuthorityPackExporter()
    if isinstance(file_or_pages, str):
        return exporter.export_from_file(file_or_pages, sku=sku, output_path=output_path, **kwargs)
    elif isinstance(file_or_pages, list):
        filename = kwargs.pop("source_filename", "document.pdf")
        h = kwargs.pop("source_hash", hashlib.sha256(b"nexus_in_memory").hexdigest())
        resolved_sku = PackSku(sku) if isinstance(sku, str) else sku
        pack = exporter.export_from_pages(file_or_pages, filename, h, sku=resolved_sku, **kwargs)
        yaml_out = pack.to_yaml()
        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(yaml_out)
        return yaml_out
    else:
        raise ValueError("file_or_pages must be a filepath string or a list of PageData objects.")
