"""
Guardrail Router for High-Risk Legal Query Detection (UPL Shield)

Prevents ALA from crossing the line from "legal information" to "legal advice"
in high-stakes situations. Per applicable state UPL statutes,
Unauthorized Practice of Law (UPL) is a strict liability offense.

This module classifies user messages before they reach the main ALA processing
loop and injects mandatory disclaimers for high-risk topics.
"""

import re
import logging
from typing import Tuple, List, Optional

logger = logging.getLogger(__name__)

# ── High-Risk Trigger Keywords ──────────────────────────────────────────────
# These keywords indicate legal scenarios that carry significant liability risk.
# When matched, ALA's system prompt is augmented with mandatory disclaimers.

HIGH_RISK_TRIGGERS = [
    # Employment termination (FEHA, CFRA, FMLA exposure)
    "terminate", "fire", "firing", "layoff", "lay off", "laid off",
    "wrongful termination", "at-will", "constructive dismissal",
    # Discrimination & harassment (FEHA)
    "harassment", "discrimination", "hostile work environment",
    "sexual harassment", "retaliation", "whistleblower",
    "protected class", "disparate impact", "disparate treatment",
    # Protected categories
    "pregnancy", "pregnant", "disability", "disabled",
    "race", "gender", "sexual orientation", "religion", "national origin",
    "age discrimination",
    # Leave & accommodation
    "fmla", "cfra", "reasonable accommodation", "ada",
    "medical leave", "family leave",
    # Government agencies & legal proceedings
    "dlse", "dfeh", "eeoc", "labor board", "labor commissioner",
    "subpoena", "lawsuit", "sue", "suing", "litigation",
    "class action", "arbitration demand",
    # Wage & hour (Labor Code)
    "wage claim", "wage theft", "overtime", "misclassification",
    "independent contractor", "meal break", "rest break",
    "minimum wage violation",
    # Securities & corporate
    "securities", "sec filing", "insider trading",
    "equity split", "stock fraud", "ponzi",
    # Criminal & tax
    "tax evasion", "tax fraud", "criminal", "felony", "misdemeanor",
    "money laundering", "embezzlement",
    # IP disputes
    "patent infringement", "trade secret", "non-compete",
]

# Compile into a regex pattern for efficient matching
_TRIGGER_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(t) for t in HIGH_RISK_TRIGGERS) + r')\b',
    re.IGNORECASE
)

# ── Tiered Injection Payloads ───────────────────────────────────────────────
# Graduated guardrail response (March 2026 optimization):
#   CRITICAL → Full disclaimer + few-shot examples (pregnancy+termination, etc.)
#   HIGH     → Short disclaimer + normal legal analysis (lawsuits, wage claims)
#   ELEVATED → NO injection (overtime, meal breaks, non-compete — routine compliance)

# Legacy alias kept for backward compatibility with any external imports
HIGH_RISK_INJECTION = None  # Replaced by tiered system below

CRITICAL_INJECTION = (
    "\n\n## ⚠️ CRITICAL-RISK LEGAL QUERY DETECTED\n"
    "SYSTEM INSTRUCTION: The user has asked about a CRITICAL legal scenario "
    "(protected class, termination of protected employee, securities, criminal). "
    "You MUST begin your response with this exact disclaimer:\n"
    "'**⚠️ CRITICAL WARNING:** This query involves high-risk legal liability. "
    "I am an AI legal assistant, not an attorney, and this information does not "
    "constitute legal advice. You should consult a licensed attorney in your state "
    "before taking any adverse action.'\n\n"
    "Then, provide objective, educational information about applicable law regarding "
    "the topic, emphasizing:\n"
    "- Maximum penalties and statutory damages under applicable state and federal law\n"
    "- Relevant statutes and regulations (cite specific code sections)\n"
    "- State agency complaint procedures and deadlines if applicable\n"
    "- The severe consequences of non-compliance (personal liability, treble damages, etc.)\n"
    "- Why professional legal counsel is essential for this specific situation\n\n"
    "### ❌ PROHIBITED (Legal Advice — NEVER DO THIS):\n"
    "User: 'Should I fire my pregnant employee?'\n"
    "→ NEVER say 'Yes, you can fire her because...' or 'No, don't fire her because...'\n"
    "→ INSTEAD explain FEHA pregnancy protections, PDL requirements, and STRONGLY recommend "
    "consulting an employment attorney before ANY adverse action.\n\n"
    "User: 'Will I win this lawsuit?'\n"
    "→ NEVER predict legal outcomes or provide case assessments.\n"
    "→ INSTEAD explain the relevant legal framework and recommend consulting litigation counsel.\n\n"
    "User: 'Can I classify this worker as a 1099 contractor?'\n"
    "→ NEVER say 'Yes, they qualify as a contractor' — that is a legal determination.\n"
    "→ INSTEAD explain the ABC Test (Labor Code §2775), risk factors, and recommend consulting "
    "an employment attorney for a formal classification analysis.\n\n"
    "Do NOT provide a specific course of action. Provide INFORMATION ONLY."
)

HIGH_INJECTION = (
    "\n\n## ⚖️ ELEVATED LEGAL QUERY\n"
    "SYSTEM INSTRUCTION: The user has asked about a significant legal topic "
    "(lawsuits, government agency complaints, wage claims). "
    "Include a brief note that you are providing legal information, not "
    "legal advice, and recommend consulting an attorney for case-specific guidance. "
    "Then provide thorough, well-cited legal information.\n"
    "Do NOT open with a large warning banner — a single sentence disclaimer is sufficient."
)

# ELEVATED level: NO injection — ALA responds confidently with statute citations.
# Covers: overtime, meal break, rest break, non-compete, independent contractor,
# and other routine compliance questions that are ALA's bread and butter.


def check_guardrails(message: str) -> Tuple[bool, List[str], Optional[str]]:
    """Check a user message against high-risk guardrail triggers.
    
    Graduated response (March 2026):
      CRITICAL → full disclaimer injection
      HIGH     → short disclaimer injection
      ELEVATED → no injection (routine compliance questions)
    
    Args:
        message: The user's input message
        
    Returns:
        Tuple of:
            - triggered: bool, whether any high-risk keywords were found
            - matched_keywords: list of matched keywords
            - injection: the prompt injection payload, or None if ELEVATED/no match
    """
    if not message:
        return False, [], None
    
    # Find all matching trigger keywords
    matches = _TRIGGER_PATTERN.findall(message.lower())
    
    if not matches:
        return False, [], None
    
    # Deduplicate and normalize
    unique_matches = list(set(m.lower() for m in matches))
    
    # Graduated response: classify risk level and select injection
    risk_level = get_risk_level(unique_matches)
    
    if risk_level == "CRITICAL":
        injection = CRITICAL_INJECTION
        logger.warning(f"🛡️ GUARDRAIL CRITICAL: Keywords={unique_matches}")
    elif risk_level == "HIGH":
        injection = HIGH_INJECTION
        logger.warning(f"⚖️ GUARDRAIL HIGH: Keywords={unique_matches}")
    else:
        # ELEVATED — routine compliance, no disclaimer needed
        injection = None
        logger.info(f"📋 GUARDRAIL ELEVATED (no injection): Keywords={unique_matches}")
    
    return True, unique_matches, injection


def get_risk_level(matched_keywords: List[str]) -> str:
    """Classify the risk level based on matched keywords.
    
    Returns 'CRITICAL', 'HIGH', or 'ELEVATED'.
    """
    critical_keywords = {
        "pregnancy", "pregnant", "discrimination", "harassment",
        "sexual harassment", "retaliation", "whistleblower",
        "terminate", "fire", "firing", "securities",
        "criminal", "felony", "subpoena", "class action",
    }
    
    high_keywords = {
        "lawsuit", "sue", "suing", "dlse", "dfeh", "eeoc",
        "wage claim", "wrongful termination", "layoff",
        "misclassification", "tax evasion",
    }
    
    matched_set = set(k.lower() for k in matched_keywords)
    
    if matched_set & critical_keywords:
        return "CRITICAL"
    elif matched_set & high_keywords:
        return "HIGH"
    else:
        return "ELEVATED"
