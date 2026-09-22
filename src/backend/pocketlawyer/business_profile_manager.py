"""
Business Profile Manager
Saves/loads business profile data as local JSON files.
Profiles persist in /srv/pocketlawyer/business_profiles/{user_id}.json

Tier 1 Context Engineering (March 2026):
  - get_structured_context(): XML-style sectioned output for AI injection
  - get_profile_completeness(): 0-100% completeness scoring
  - get_relevant_context(): Query-aware section filtering
"""

import os
import re
import json
import threading
from datetime import datetime, timezone
from typing import Dict, Optional, Any

# Profile storage directory - Docker volume mount persists across restarts
PROFILE_DIR = os.environ.get(
    'BUSINESS_PROFILE_DIR',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'profiles')
)


# ── In-Memory Profile Cache (ALA Optimization #1) ────────────────────────
# Avoids disk reads on every chat turn. TTL = 300s (5 min).
# Invalidated on save/update/delete.
_profile_cache: Dict[int, Dict] = {}     # user_id -> profile dict
_cache_ts: Dict[int, float] = {}         # user_id -> timestamp (epoch)
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # seconds


def _cache_get(user_id: int) -> Optional[Dict]:
    """Get profile from cache if fresh enough."""
    import time
    with _cache_lock:
        if user_id in _profile_cache:
            age = time.time() - _cache_ts.get(user_id, 0)
            if age < _CACHE_TTL:
                return _profile_cache[user_id]
            else:
                # Expired — evict
                _profile_cache.pop(user_id, None)
                _cache_ts.pop(user_id, None)
    return None


def _cache_set(user_id: int, profile: Dict):
    """Store profile in cache."""
    import time
    with _cache_lock:
        _profile_cache[user_id] = profile
        _cache_ts[user_id] = time.time()


def _cache_invalidate(user_id: int):
    """Invalidate cache entry for a user (called on write operations)."""
    with _cache_lock:
        _profile_cache.pop(user_id, None)
        _cache_ts.pop(user_id, None)


def _ensure_dir():
    """Create profile directory if it doesn't exist."""
    os.makedirs(PROFILE_DIR, exist_ok=True)


def _profile_path(user_id: int) -> str:
    """Get the file path for a user's profile."""
    return os.path.join(PROFILE_DIR, f'{user_id}.json')


def save_profile(user_id: int, profile_data: Dict[str, Any]) -> Dict:
    """
    Save a business profile to disk.
    
    Args:
        user_id: The business user's ID
        profile_data: Dict with profile fields
        
    Returns:
        Dict with success status
    """
    _ensure_dir()
    
    # Add metadata
    profile_data['user_id'] = user_id
    profile_data['updated_at'] = datetime.now(timezone.utc).isoformat()
    
    # If no created_at, this is a new profile
    existing = load_profile(user_id)
    if existing and existing.get('created_at'):
        profile_data['created_at'] = existing['created_at']
    else:
        profile_data['created_at'] = profile_data['updated_at']
    
    path = _profile_path(user_id)
    with open(path, 'w') as f:
        json.dump(profile_data, f, indent=2, default=str)
    
    # Invalidate cache on write
    _cache_invalidate(user_id)
    
    return {
        'success': True,
        'message': 'Business profile saved',
        'path': path
    }


def load_profile(user_id: int) -> Optional[Dict]:
    """
    Load a business profile (cache-first, disk fallback).
    
    Args:
        user_id: The business user's ID
        
    Returns:
        Profile dict or None if not found
    """
    # Check cache first
    cached = _cache_get(user_id)
    if cached is not None:
        return cached
    
    # Cache miss — read from disk
    path = _profile_path(user_id)
    if not os.path.exists(path):
        return None
    
    try:
        with open(path, 'r') as f:
            profile = json.load(f)
        # Populate cache
        _cache_set(user_id, profile)
        return profile
    except (json.JSONDecodeError, IOError):
        return None


def update_profile(user_id: int, updates: Dict[str, Any]) -> Dict:
    """
    Partially update a profile (merge with existing).
    
    Args:
        user_id: The business user's ID
        updates: Dict of fields to update (supports nested keys)
        
    Returns:
        Dict with success status
    """
    existing = load_profile(user_id) or {}
    
    # Deep merge
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(existing.get(key), dict):
            existing[key].update(value)
        else:
            existing[key] = value
    
    return save_profile(user_id, existing)


def delete_profile(user_id: int) -> Dict:
    """Delete a user's profile."""
    path = _profile_path(user_id)
    _cache_invalidate(user_id)
    if os.path.exists(path):
        os.remove(path)
        return {'success': True, 'message': 'Profile deleted'}
    return {'success': True, 'message': 'No profile to delete'}



# =========================================================================
# SECTION DEFINITIONS — used by completeness + structured context
# =========================================================================

# Each section: (key, label, required_fields, priority)
# required_fields are the minimum fields needed to consider a section "filled"
PROFILE_SECTIONS = [
    ('owner',      'Contact Information', ['name'],                    'critical'),
    ('company',    'Company Details',     ['name', 'entity_type'],     'critical'),
    ('address',    'Business Location',   ['city', 'state'],           'high'),
    ('industry',   'Industry',            ['primary'],                 'high'),
    ('employees',  'Team Size',           ['count'],                   'medium'),
    ('compliance', 'Compliance & Insurance', ['insurance_types'],      'medium'),
    ('preferences','AI Preferences',      ['ai_formality'],            'low'),
]

# Query → relevant section keys (keyword-based matching)
_RELEVANCE_RULES = [
    # (regex pattern, set of section keys to include)
    (r'employee|hire|fire|terminat|worker|team|staff|contractor|payroll|wage|salary|overtime|break|meal',
     {'owner', 'company', 'employees'}),
    (r'comply|compliance|insurance|license|permit|regulat|posting|osha|safety',
     {'owner', 'company', 'compliance', 'industry'}),
    (r'contract|agreement|nda|lease|vendor|supplier|indemnif',
     {'owner', 'company', 'compliance'}),
    (r'tax|formation|entity|llc|corp|inc|franchise|annual|filing|ein|fein',
     {'owner', 'company', 'address'}),
    (r'location|address|city|county|local|ordinance|zone|zoning',
     {'owner', 'company', 'address'}),
    (r'industry|sector|naics|market|competitor',
     {'owner', 'company', 'industry'}),
    (r'invoice|expense|mileage|receipt|deduction|cost|billing|payment',
     {'owner', 'company', 'industry'}),
    (r'draft|document|letter|memo|generate|create|write',
     {'owner', 'company', 'industry', 'address'}),
]


# =========================================================================
# COMPLETENESS SCORING
# =========================================================================

def get_profile_completeness(user_id: int) -> Dict:
    """
    Calculate how complete a business profile is.

    Returns:
        Dict with:
          - score: 0-100 (weighted by section priority)
          - sections: {section_key: bool} — is each section filled?
          - missing: list of missing section labels
          - filled: count of filled sections
          - total: total sections
    """
    profile = load_profile(user_id)
    if not profile:
        return {
            'score': 0,
            'sections': {s[0]: False for s in PROFILE_SECTIONS},
            'missing': [s[1] for s in PROFILE_SECTIONS],
            'filled': 0,
            'total': len(PROFILE_SECTIONS),
        }

    # Priority weights for scoring
    weights = {'critical': 25, 'high': 18, 'medium': 12, 'low': 5}
    total_weight = sum(weights[s[3]] for s in PROFILE_SECTIONS)

    filled_weight = 0
    sections_status = {}
    missing = []

    for key, label, required, priority in PROFILE_SECTIONS:
        section_data = profile.get(key, {})
        is_filled = _section_is_filled(section_data, required)
        sections_status[key] = is_filled
        if is_filled:
            filled_weight += weights[priority]
        else:
            missing.append(label)

    score = round((filled_weight / total_weight) * 100) if total_weight else 0
    filled_count = sum(1 for v in sections_status.values() if v)

    return {
        'score': score,
        'sections': sections_status,
        'missing': missing,
        'filled': filled_count,
        'total': len(PROFILE_SECTIONS),
    }


def _section_is_filled(section_data: Dict, required_fields: list) -> bool:
    """Check if a section has its required fields populated."""
    if not section_data:
        return False
    for field in required_fields:
        val = section_data.get(field)
        if val is None:
            return False
        if isinstance(val, str) and not val.strip():
            return False
        if isinstance(val, list) and len(val) == 0:
            return False
        if isinstance(val, (int, float)) and val == 0:
            return False
    return True


# =========================================================================
# STRUCTURED CONTEXT (for AI injection)
# =========================================================================

def get_structured_context(user_id: int) -> Optional[str]:
    """
    Generate compact business context for AI system prompt injection.

    Uses dense [SECTION] KV format (~60% fewer tokens than the previous XML
    format) while preserving all the same information. Empty sections are
    omitted to save tokens.

    Returns:
        Formatted context block or None if no profile / profile is empty.
    """
    profile = load_profile(user_id)
    if not profile:
        return None

    completeness = get_profile_completeness(user_id)
    sections = []

    for key, _, _, _ in PROFILE_SECTIONS:
        builder = _SECTION_BUILDERS.get(key)
        if builder:
            section = builder(profile)
            if section:
                sections.append(section)

    if not sections:
        return None

    header = "## Business Profile"
    status = f"Profile: {completeness['score']}% complete"
    if completeness['missing']:
        status += f" (missing: {', '.join(completeness['missing'])})"

    return header + "\n" + status + "\n" + "\n".join(sections)


# =========================================================================
# QUERY-RELEVANT FILTERING
# =========================================================================

def get_relevant_context(user_id: int, message: str) -> Optional[str]:
    """
    Generate business context filtered to only sections relevant to the
    user's current message. Saves tokens and focuses the AI's attention.

    Falls back to full structured context for generic queries.

    Args:
        user_id: The business user's ID
        message: The user's current chat message

    Returns:
        Filtered structured context block, or None if no profile.
    """
    profile = load_profile(user_id)
    if not profile:
        return None

    # Determine which sections are relevant to this query
    relevant_keys = _match_relevant_sections(message)

    # If no specific match, return full context
    if relevant_keys is None:
        return get_structured_context(user_id)

    # Always include identity (owner) for addressing context
    relevant_keys.add('owner')

    completeness = get_profile_completeness(user_id)
    sections = []

    for key in relevant_keys:
        builder = _SECTION_BUILDERS.get(key)
        if builder:
            section = builder(profile)
            if section:
                sections.append(section)

    if not sections:
        return None

    header = "## Business Profile (filtered)"
    status = f"Profile: {completeness['score']}% complete"
    if completeness['missing']:
        status += f" (missing: {', '.join(completeness['missing'])})"

    return header + "\n" + status + "\n" + "\n".join(sections)


def _match_relevant_sections(message: str) -> Optional[set]:
    """
    Match a user message against relevance rules to determine which
    profile sections to inject.

    Returns:
        set of section keys, or None if no specific match (→ use full context).
    """
    if not message or not message.strip():
        return None

    msg_lower = message.lower()
    matched_keys = set()

    for pattern, keys in _RELEVANCE_RULES:
        if re.search(pattern, msg_lower):
            matched_keys.update(keys)

    # If nothing matched, return None to signal "use full context"
    return matched_keys if matched_keys else None


# --- Individual section builders (used by get_relevant_context) ---

def _build_identity_section(profile: Dict) -> Optional[str]:
    owner = profile.get('owner', {})
    if not _section_is_filled(owner, ['name']):
        return None
    parts = [owner['name']]
    if owner.get('title'):
        parts[0] += f", {owner['title']}"
    if owner.get('email'):
        parts.append(owner['email'])
    if owner.get('phone'):
        parts.append(owner['phone'])
    return f"[IDENTITY] {' | '.join(parts)}"


def _build_company_section(profile: Dict) -> Optional[str]:
    company = profile.get('company', {})
    if not _section_is_filled(company, ['name']):
        return None
    parts = [company.get('name', '')]
    if company.get('entity_type'):
        parts.append(company['entity_type'])
    if company.get('state_of_formation'):
        parts.append(company['state_of_formation'])
    if company.get('formation_date'):
        parts.append(f"formed {company['formation_date']}")
    if company.get('dba'):
        parts.append(f"DBA: {company['dba']}")
    if company.get('ein'):
        parts.append(f"EIN: {company['ein']}")
    line = f"[COMPANY] {' | '.join(parts)}"
    if company.get('description'):
        line += f"\n  {company['description']}"
    return line


def _build_location_section(profile: Dict) -> Optional[str]:
    addr = profile.get('address', {})
    if not _section_is_filled(addr, ['city', 'state']):
        return None
    addr_parts = [addr.get('street', ''), addr.get('city', ''),
                  addr.get('state', ''), addr.get('zip', '')]
    addr_str = ', '.join(p for p in addr_parts if p)
    return f"[LOCATION] {addr_str}"


def _build_industry_section(profile: Dict) -> Optional[str]:
    industry = profile.get('industry', {})
    if not _section_is_filled(industry, ['primary']):
        return None
    ind_str = industry['primary']
    if industry.get('naics_code'):
        ind_str += f" (NAICS: {industry['naics_code']})"
    return f"[INDUSTRY] {ind_str}"


def _build_team_section(profile: Dict) -> Optional[str]:
    emp = profile.get('employees', {})
    if not _section_is_filled(emp, ['count']):
        return None
    parts = [f"{emp['count']} employees"]
    if emp.get('has_contractors') and emp.get('contractor_count'):
        parts.append(f"{emp['contractor_count']} contractors")
    return f"[TEAM] {', '.join(parts)}"


def _build_compliance_section(profile: Dict) -> Optional[str]:
    comp = profile.get('compliance', {})
    if not _section_is_filled(comp, ['insurance_types']):
        return None
    parts = []
    if comp.get('insurance_types'):
        parts.append(f"insurance: {', '.join(comp['insurance_types'])}")
    if comp.get('business_license'):
        lic = "license: active"
        if comp.get('license_expiration'):
            lic += f", expires {comp['license_expiration']}"
        parts.append(lic)
    return f"[COMPLIANCE] {' | '.join(parts)}"


def _build_preferences_section(profile: Dict) -> Optional[str]:
    prefs = profile.get('preferences', {})
    if not _section_is_filled(prefs, ['ai_formality']):
        return None
    parts = [prefs.get('ai_formality', 'professional')]
    if prefs.get('focus_areas'):
        parts.append(f"focus: {', '.join(prefs['focus_areas'])}")
    return f"[PREFERENCES] {' | '.join(parts)}"


# Section builder registry (used by get_structured_context and get_relevant_context)
_SECTION_BUILDERS = {
    'owner':      _build_identity_section,
    'company':    _build_company_section,
    'address':    _build_location_section,
    'industry':   _build_industry_section,
    'employees':  _build_team_section,
    'compliance': _build_compliance_section,
    'preferences': _build_preferences_section,
}


# =========================================================================
# BACKWARD COMPATIBILITY — old callers still work
# =========================================================================

def get_profile_summary(user_id: int) -> Optional[str]:
    """
    Generate a human-readable summary of the business profile
    for injection into the AI secretary's directive.

    BACKWARD COMPATIBLE: Now delegates to get_structured_context().
    Legacy callers get the new structured format automatically.

    Returns:
        Formatted text block or None if no profile exists
    """
    return get_structured_context(user_id)


def get_employee_roster_summary(user_id: int, db_path=None) -> Optional[str]:
    """
    Generate a concise team roster for injection into the AI chat context.
    Queries the employee SQLite DB for active employees belonging to this user.
    
    Args:
        user_id: The business user's ID
        db_path: Override DB path (for testing); default uses EmployeeManager's default
    
    Returns:
        Formatted text block or None if no employees exist
    """
    try:
        from ala_secretary.execution.employee_manager import EmployeeManager
        kwargs = {'business_id': user_id}
        if db_path:
            kwargs['db_path'] = db_path
        mgr = EmployeeManager(**kwargs)
        employees = mgr.list_employees(status='active')
        if not employees:
            return None
        
        lines = [
            "TEAM ROSTER (Your employees — reference this when the user asks about a team member):"
        ]
        for emp in employees:
            name = emp.get('full_name', 'Unknown')
            role = emp.get('role') or 'No role assigned'
            dept = emp.get('department') or 'No department'
            start = emp.get('start_date') or 'Unknown start date'
            parts = [name, role, dept, f"started {start}"]
            
            # Include email if available
            email = emp.get('email')
            if email:
                parts.append(email)
            
            lines.append(f"• {' — '.join(parts)}")
        
        return '\n'.join(lines)
    except Exception:
        return None


def get_empty_profile() -> Dict:
    """Return an empty profile template for the form."""
    return {
        'owner': {
            'name': '',
            'email': '',
            'title': '',
            'phone': ''
        },
        'company': {
            'name': '',
            'dba': '',
            'ein': '',
            'entity_type': '',
            'formation_date': '',
            'state_of_formation': '',
            'description': ''
        },
        'address': {
            'street': '',
            'city': '',
            'state': '',
            'zip': ''
        },
        'industry': {
            'primary': '',
            'naics_code': ''
        },
        'employees': {
            'count': 0,
            'has_contractors': False,
            'contractor_count': 0
        },
        'compliance': {
            'business_license': False,
            'license_expiration': '',
            'insurance_types': []
        },
        'preferences': {
            'reminder_days_before': 30,
            'email_notifications': True,
            'ai_formality': 'professional'
        }
    }


# =========================================================================
# TIER 2A: CONVERSATION-DERIVED FACT EXTRACTION (March 2026)
#
# Analyzes user messages for business facts and auto-updates the profile.
# Uses Gemini Flash (cheap/fast) for extraction. Runs async after response.
# =========================================================================

# Fields that should NEVER be overwritten by extraction (user must set manually)
_PROTECTED_FIELDS = frozenset(['ein', 'formation_date'])

# Maximum number of fields to update per extraction (prevents runaway updates)
_MAX_FIELDS_PER_EXTRACTION = 5

# The extraction prompt template
_EXTRACTION_PROMPT = """You are a business profile fact extractor. Analyze the user's messages 
for concrete business facts that should be stored in their profile.

Current profile:
{current_profile}

Recent user messages:
{messages}

RULES:
- Only extract FACTS explicitly stated by the user (do not infer or assume)
- Only extract info that maps to the profile schema below
- Return null if no new facts found
- Never extract EIN or formation_date (protected fields)
- Maximum 5 field updates per extraction

Profile schema (only use these paths):
- owner.name, owner.email, owner.title, owner.phone
- company.name, company.dba, company.entity_type, company.state_of_formation, company.description
- address.street, address.city, address.state, address.zip
- industry.primary, industry.naics_code
- employees.count (integer), employees.has_contractors (boolean), employees.contractor_count (integer)
- compliance.business_license (boolean), compliance.insurance_types (list of strings)
- preferences.ai_formality (string: "professional", "casual", "technical")

Return ONLY a JSON object with updates or null. Example:
{{"employees": {{"count": 15}}, "company": {{"description": "SaaS platform for HR"}}}}
"""


def extract_profile_facts(user_id: int, messages: list, gemini_client=None) -> Optional[Dict]:
    """
    Analyze recent user messages for business facts and auto-update the profile.
    
    Uses Gemini Flash (cheap, fast) to extract structured facts from conversation.
    Only updates fields that are explicitly mentioned by the user.
    
    Args:
        user_id: The business user's ID
        messages: List of recent message dicts [{'role': 'user'|'model', 'content': '...'}]
        gemini_client: Optional pre-built Gemini client (for testing)
    
    Returns:
        Dict of extracted updates applied, or None if no facts found.
    """
    if not messages:
        return None
    
    # Only analyze user messages (not model responses)
    user_msgs = [m.get('content', '') for m in messages if m.get('role') == 'user' and m.get('content')]
    if not user_msgs:
        return None
    
    # Load current profile for context
    profile = load_profile(user_id) or get_empty_profile()
    
    # Build extraction prompt
    prompt = _EXTRACTION_PROMPT.format(
        current_profile=json.dumps(profile, indent=2),
        messages='\n'.join(f'- "{msg}"' for msg in user_msgs[-3:])  # Last 3 user messages
    )
    
    try:
        # Use Gemini Flash for cheap, fast extraction
        if gemini_client is None:
            from google import genai
            gemini_client = genai.Client()
        
        response = gemini_client.models.generate_content(
            model='gemini-3-flash-preview',
            contents=prompt,
            config={
                'response_mime_type': 'application/json',
                'response_schema': {
                    'type': 'object',
                    'properties': {
                        'owner': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'title': {'type': 'string'},
                                'phone': {'type': 'string'},
                                'email': {'type': 'string'},
                            },
                        },
                        'company': {
                            'type': 'object',
                            'properties': {
                                'name': {'type': 'string'},
                                'entity_type': {'type': 'string'},
                                'description': {'type': 'string'},
                            },
                        },
                        'employees': {
                            'type': 'object',
                            'properties': {
                                'count': {'type': 'integer'},
                                'contractors': {'type': 'integer'},
                            },
                        },
                        'industry': {
                            'type': 'object',
                            'properties': {
                                'primary': {'type': 'string'},
                                'naics_code': {'type': 'string'},
                            },
                        },
                        'address': {
                            'type': 'object',
                            'properties': {
                                'state': {'type': 'string'},
                                'city': {'type': 'string'},
                            },
                        },
                        'compliance': {
                            'type': 'object',
                            'properties': {
                                'insurance_types': {
                                    'type': 'array',
                                    'items': {'type': 'string'},
                                },
                            },
                        },
                    },
                },
            }
        )
        
        result_text = response.text.strip()
        if not result_text or result_text.lower() == 'null':
            return None
        
        updates = json.loads(result_text)
        if not isinstance(updates, dict) or not updates:
            return None
        
        # Safety: remove protected fields
        _remove_protected_fields(updates)
        
        # Safety: limit field count
        field_count = sum(len(v) if isinstance(v, dict) else 1 for v in updates.values())
        if field_count > _MAX_FIELDS_PER_EXTRACTION:
            return None  # Refuse suspiciously large extractions
        
        if not updates:
            return None
        
        # Apply updates to profile
        update_profile(user_id, updates)
        
        return updates
    
    except (json.JSONDecodeError, Exception):
        # Extraction failures are non-critical — silently continue
        return None


def _remove_protected_fields(updates: Dict) -> None:
    """Remove protected fields from extraction results (in-place)."""
    for section_key, section_data in list(updates.items()):
        if isinstance(section_data, dict):
            for field in list(section_data.keys()):
                if field in _PROTECTED_FIELDS:
                    del section_data[field]
            # Remove empty sections after cleanup
            if not section_data:
                del updates[section_key]

