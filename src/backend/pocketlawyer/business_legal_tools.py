"""
Small Business Pro - Business Legal Tools
Deterministic scripts for ALA's business capabilities

Each tool provides structured analysis with consistent output formats.
"""

import os
import json
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGAL_DB = os.path.join(BASE_DIR, 'legal_data.db')


# =============================================================================
# 1. CONTRACT REVIEW
# =============================================================================

class ContractReviewer:
    """
    Analyze contracts, flag risks, suggest modifications.
    
    Usage:
        reviewer = ContractReviewer()
        analysis = reviewer.analyze(contract_text, contract_type='service_agreement')
    """
    
    # Risk patterns to flag
    RISK_PATTERNS = {
        'indemnification': {
            'patterns': ['indemnify', 'hold harmless', 'defend and indemnify'],
            'severity': 'high',
            'guidance': 'Review scope of indemnification. Consider limiting to third-party claims arising from your acts/omissions.'
        },
        'unlimited_liability': {
            'patterns': ['unlimited liability', 'all damages', 'any and all losses'],
            'severity': 'high',
            'guidance': 'Negotiate liability cap (typically contract value or insurance limits).'
        },
        'auto_renewal': {
            'patterns': ['automatically renew', 'auto-renew', 'shall renew'],
            'severity': 'medium',
            'guidance': 'Note renewal terms and cancellation notice period. Add calendar reminder.'
        },
        'non_compete': {
            'patterns': ['non-compete', 'noncompete', 'shall not compete', 'covenant not to compete'],
            'severity': 'high',
            'guidance': 'CA generally voids non-competes (B&P Code § 16600). May still apply to business sale.'
        },
        'arbitration': {
            'patterns': ['binding arbitration', 'arbitrate', 'waive jury'],
            'severity': 'medium',
            'guidance': 'Consider if arbitration serves your interests. Review arbitrator selection, venue, cost allocation.'
        },
        'termination_for_convenience': {
            'patterns': ['terminate for convenience', 'terminate without cause', 'at any time for any reason'],
            'severity': 'medium',
            'guidance': 'Ensure mutual termination rights. Review wind-down provisions.'
        },
        'assignment': {
            'patterns': ['may not assign', 'cannot assign', 'assignment prohibited'],
            'severity': 'low',
            'guidance': 'May limit exit options. Consider carve-out for affiliates or acquisition.'
        },
        'intellectual_property': {
            'patterns': ['work for hire', 'assigns all', 'ownership of', 'all intellectual property'],
            'severity': 'high',
            'guidance': 'Clarify who owns what. Ensure you retain rights to your pre-existing IP and tools.'
        },
        'confidentiality_term': {
            'patterns': ['perpetual confidentiality', 'indefinitely', 'survive termination indefinitely'],
            'severity': 'medium',
            'guidance': 'Consider reasonable time limit (3-5 years) except for trade secrets.'
        },
        'governing_law': {
            'patterns': ['governed by the laws of', 'jurisdiction of', 'venue shall be'],
            'severity': 'low',
            'guidance': 'California venue preferred. Review if out-of-state litigation is practical.'
        }
    }
    
    CONTRACT_TYPES = {
        'service_agreement': ['scope', 'payment', 'term', 'termination', 'liability'],
        'nda': ['definition_confidential', 'exceptions', 'term', 'return_of_materials'],
        'employment': ['duties', 'compensation', 'benefits', 'termination', 'restrictive_covenants'],
        'lease': ['rent', 'term', 'use', 'maintenance', 'default'],
        'vendor': ['deliverables', 'pricing', 'warranties', 'acceptance', 'liability'],
        'partnership': ['contributions', 'distributions', 'management', 'dissolution', 'buyout']
    }
    
    def analyze(self, contract_text: str, contract_type: str = 'general') -> Dict:
        """
        Analyze contract and return structured risk assessment.
        """
        text_lower = contract_text.lower()
        
        # Find risks
        risks_found = []
        for risk_name, risk_info in self.RISK_PATTERNS.items():
            for pattern in risk_info['patterns']:
                if pattern.lower() in text_lower:
                    risks_found.append({
                        'risk': risk_name.replace('_', ' ').title(),
                        'severity': risk_info['severity'],
                        'guidance': risk_info['guidance'],
                        'pattern_found': pattern
                    })
                    break
        
        # Check for missing sections
        missing_sections = []
        expected_sections = self.CONTRACT_TYPES.get(contract_type, [])
        for section in expected_sections:
            if section.replace('_', ' ') not in text_lower and section not in text_lower:
                missing_sections.append(section.replace('_', ' ').title())
        
        # Calculate risk score
        risk_score = sum(
            3 if r['severity'] == 'high' else 2 if r['severity'] == 'medium' else 1
            for r in risks_found
        )
        
        risk_level = 'LOW' if risk_score < 3 else 'MEDIUM' if risk_score < 7 else 'HIGH'
        
        return {
            'contract_type': contract_type,
            'risk_level': risk_level,
            'risk_score': risk_score,
            'risks': sorted(risks_found, key=lambda x: {'high': 0, 'medium': 1, 'low': 2}[x['severity']]),
            'missing_sections': missing_sections,
            'word_count': len(contract_text.split()),
            'analysis_date': datetime.now().isoformat(),
            'recommendations': self._generate_recommendations(risks_found, missing_sections)
        }
    
    def deep_analyze(self, contract_text: str,
                     contract_type: str = 'general') -> Dict:
        """
        AI-powered deep contract analysis using ContractAnalyzer.

        Falls back to rule-based analyze() if ContractAnalyzer is unavailable.

        Args:
            contract_text: Full contract text
            contract_type: Contract type (service_agreement, nda, etc.)

        Returns:
            Merged analysis dict with both rule-based and AI-powered results.
        """
        # Always run rule-based analysis
        rule_based = self.analyze(contract_text, contract_type)

        # Attempt AI-powered analysis
        try:
            from execution.integrations.contract_analyzer import ContractAnalyzer
            analyzer = ContractAnalyzer()
            ai_result = analyzer.analyze(contract_text, contract_type)

            # Merge: AI analysis enriches rule-based results
            return {
                'contract_type': contract_type,
                'risk_level': ai_result['risk_summary']['risk_level'],
                'risk_score': ai_result['risk_summary']['overall_score'],
                'risks': rule_based['risks'],
                'missing_sections': rule_based['missing_sections'],
                'word_count': rule_based['word_count'],
                'analysis_date': rule_based['analysis_date'],
                'recommendations': rule_based['recommendations'],
                # AI-powered additions
                'ai_clauses': ai_result['clauses'],
                'ca_flags': ai_result['ca_flags'],
                'ai_risk_summary': ai_result['risk_summary'],
                'ai_recommendations': ai_result['recommendations'],
                'analysis_method': ai_result['analysis_method'],
                'source': 'contract_reviewer_deep'
            }
        except ImportError:
            rule_based['analysis_method'] = 'rule_based_only'
            rule_based['source'] = 'contract_reviewer'
            return rule_based
        except Exception:
            rule_based['analysis_method'] = 'rule_based_fallback'
            rule_based['source'] = 'contract_reviewer'
            return rule_based

    def _generate_recommendations(self, risks: List, missing: List) -> List[str]:
        """Generate actionable recommendations."""
        recs = []
        
        high_risks = [r for r in risks if r['severity'] == 'high']
        if high_risks:
            recs.append(f"⚠️ Address {len(high_risks)} high-severity issues before signing")
        
        if missing:
            recs.append(f"📋 Request clauses for: {', '.join(missing)}")
        
        if any(r['risk'] == 'Indemnification' for r in risks):
            recs.append("🛡️ Negotiate mutual indemnification or cap your exposure")
        
        if any(r['risk'] == 'Arbitration' for r in risks):
            recs.append("⚖️ Consider if arbitration clause serves your business interests")
        
        if not recs:
            recs.append("✅ Contract appears standard. Review specific terms before signing.")
        
        return recs


# =============================================================================
# 2. EMPLOYMENT GUIDANCE
# =============================================================================

class EmploymentAdvisor:
    """
    Hiring, firing, policies, compliance with CA labor law.
    """
    
    # California employment law requirements
    CA_REQUIREMENTS = {
        'new_hire': {
            'required_forms': [
                ('I-9', 'Within 3 business days of start', 'federal'),
                ('W-4', 'First day', 'federal'),
                ('DE 4', 'First day', 'california'),
                ('Notice to Employee (DLSE)', 'First day', 'california'),
            ],
            'required_notices': [
                'Sexual Harassment Prevention Policy',
                'Workers Compensation Rights',
                'Paid Family Leave',
                'State Disability Insurance',
                'Unemployment Insurance',
            ],
            'required_posters': [
                'California Minimum Wage',
                'Payday Notice',
                'Safety and Health Protection',
                'Workers Compensation',
                'Discrimination and Harassment',
            ]
        },
        'termination': {
            'final_pay': {
                'involuntary': 'Same day (Lab. Code § 201)',
                'voluntary_no_notice': 'Within 72 hours (Lab. Code § 202)',
                'voluntary_72hr_notice': 'Last day of work (Lab. Code § 202)',
            },
            'required_items': [
                'Final paycheck including all earned wages',
                'Accrued unused vacation/PTO (required in CA)',
                'COBRA/Cal-COBRA notice (if applicable)',
                'WARN Act notice (60 days for mass layoffs)',
                'EDD pamphlet (DE 2320)',
            ],
            'documentation': [
                'Written termination letter',
                'Return of company property acknowledgment',
                'Final pay acknowledgment',
                'Separation agreement (if applicable)',
            ]
        },
        'exempt_criteria': {
            'salary_minimum': 66560,  # 2024: 2x minimum wage for 40 hrs
            'duties_tests': [
                'Executive: Manages enterprise/department, supervises 2+ employees',
                'Administrative: Office work, exercises discretion on significant matters',
                'Professional: Licensed profession or learned/creative field',
            ]
        }
    }
    
    def new_hire_checklist(self, employee_type: str = 'non_exempt') -> Dict:
        """Generate new hire compliance checklist."""
        checklist = {
            'employee_type': employee_type,
            'day_one': [],
            'within_first_week': [],
            'within_30_days': [],
            'ongoing': []
        }
        
        # Day one requirements
        checklist['day_one'] = [
            {'item': 'Complete I-9 Section 1', 'required': True, 'deadline': 'First day'},
            {'item': 'Collect W-4 and DE 4', 'required': True, 'deadline': 'First day'},
            {'item': 'Provide Notice to Employee (wage notice)', 'required': True, 'deadline': 'First day'},
            {'item': 'Provide required workplace posters or digital access', 'required': True, 'deadline': 'First day'},
            {'item': 'Set up time tracking system', 'required': employee_type == 'non_exempt', 'deadline': 'First day'},
        ]
        
        # First week
        checklist['within_first_week'] = [
            {'item': 'Complete I-9 Section 2 (verify documents)', 'required': True, 'deadline': '3 business days'},
            {'item': 'Enroll in payroll', 'required': True, 'deadline': 'Before first pay period'},
            {'item': 'Provide employee handbook acknowledgment', 'required': True, 'deadline': '7 days'},
            {'item': 'Sexual harassment prevention training (1 hour)', 'required': True, 'deadline': '6 months, but start early'},
        ]
        
        # Within 30 days
        checklist['within_30_days'] = [
            {'item': 'Benefits enrollment (if eligible)', 'required': False, 'deadline': '30 days'},
            {'item': 'Workers comp information provided', 'required': True, 'deadline': '30 days'},
        ]
        
        # Ongoing
        checklist['ongoing'] = [
            {'item': 'Maintain accurate time records (non-exempt)', 'required': employee_type == 'non_exempt', 'frequency': 'Each pay period'},
            {'item': 'Provide itemized wage statements', 'required': True, 'frequency': 'Each pay period'},
            {'item': 'Track meal and rest breaks', 'required': employee_type == 'non_exempt', 'frequency': 'Daily'},
        ]
        
        return checklist
    
    def termination_checklist(self, termination_type: str = 'involuntary') -> Dict:
        """Generate termination compliance checklist."""
        final_pay_rule = self.CA_REQUIREMENTS['termination']['final_pay'].get(
            termination_type, 'Same day'
        )
        
        return {
            'termination_type': termination_type,
            'final_pay_deadline': final_pay_rule,
            'checklist': [
                {'item': 'Prepare final paycheck with all wages owed', 'critical': True},
                {'item': 'Include accrued, unused PTO/vacation', 'critical': True},
                {'item': 'Prepare written termination letter', 'critical': True},
                {'item': 'Calculate and pay any earned bonuses/commissions', 'critical': True},
                {'item': 'Prepare COBRA/Cal-COBRA notice', 'critical': False},
                {'item': 'Provide EDD pamphlet (DE 2320)', 'critical': True},
                {'item': 'Collect company property', 'critical': False},
                {'item': 'Disable system access', 'critical': True},
                {'item': 'Document reason for termination (internal)', 'critical': True},
            ],
            'warnings': [
                '⚠️ Late final pay: $100/day penalty up to 30 days (Lab. Code § 203)',
                '⚠️ Do not withhold final pay for unreturned property',
                '⚠️ Include ALL compensation: wages, commissions, bonuses earned',
            ]
        }
    
    def exempt_analysis(self, salary: float, job_duties: str) -> Dict:
        """Analyze if position qualifies as exempt."""
        salary_threshold = self.CA_REQUIREMENTS['exempt_criteria']['salary_minimum']
        
        meets_salary = salary >= salary_threshold
        
        # Basic duties analysis
        duties_lower = job_duties.lower()
        likely_executive = any(kw in duties_lower for kw in ['manage', 'supervise', 'direct', 'hire', 'fire'])
        likely_admin = any(kw in duties_lower for kw in ['discretion', 'independent judgment', 'policy', 'strategy'])
        likely_professional = any(kw in duties_lower for kw in ['licensed', 'degree required', 'specialized', 'creative'])
        
        return {
            'salary_test': {
                'threshold': salary_threshold,
                'actual': salary,
                'passes': meets_salary
            },
            'duties_indicators': {
                'executive': likely_executive,
                'administrative': likely_admin,
                'professional': likely_professional,
            },
            'recommendation': self._exempt_recommendation(meets_salary, likely_executive, likely_admin, likely_professional),
            'disclaimer': 'Full exemption analysis requires detailed review of actual job duties. Misclassification carries significant penalties.'
        }
    
    def _exempt_recommendation(self, salary_ok: bool, exec: bool, admin: bool, prof: bool) -> str:
        if not salary_ok:
            return "❌ Does NOT meet salary threshold. Classify as non-exempt."
        
        if exec or admin or prof:
            return "⚠️ May qualify as exempt. Conduct detailed duties analysis before classifying."
        
        return "❌ Likely non-exempt. Salary alone doesn't establish exemption."


# =============================================================================
# 3. ENTITY MANAGEMENT
# =============================================================================

class EntityManager:
    """
    LLC/Corp maintenance, governance, compliance calendars.
    
    Enhanced with CA Secretary of State API integration for real-time
    entity status monitoring and Good Standing verification.
    """
    
    CA_REQUIREMENTS = {
        'llc': {
            'annual_filings': [
                {'filing': 'Statement of Information', 'due': 'Every 2 years (anniversary month)', 'fee': 20, 'agency': 'Secretary of State'},
                {'filing': 'Franchise Tax', 'due': 'April 15 ($800 minimum)', 'fee': 800, 'agency': 'Franchise Tax Board'},
            ],
            'governance': [
                'Operating Agreement (recommended)',
                'Meeting minutes (if desired)',
                'Capital account records',
            ]
        },
        'corporation': {
            'annual_filings': [
                {'filing': 'Statement of Information', 'due': 'Annually (anniversary month)', 'fee': 25, 'agency': 'Secretary of State'},
                {'filing': 'Franchise Tax', 'due': 'April 15 ($800 minimum)', 'fee': 800, 'agency': 'Franchise Tax Board'},
            ],
            'governance': [
                'Annual shareholder meeting',
                'Annual board meeting',
                'Meeting minutes',
                'Stock ledger maintenance',
                'Corporate resolutions for major decisions',
            ]
        }
    }
    
    def compliance_calendar(self, entity_type: str, formation_date: str,
                            entity_number: str = None) -> List[Dict]:
        """
        Generate compliance calendar for entity.
        
        If entity_number is provided, enriches the calendar with live
        CA SOS status data (Good Standing, last SI filing date).
        """
        from dateutil.relativedelta import relativedelta
        from dateutil.parser import parse
        
        try:
            formed = parse(formation_date)
        except Exception:
            formed = datetime.now()
        
        calendar = []
        today = datetime.now()
        
        requirements = self.CA_REQUIREMENTS.get(entity_type.lower(), {})
        
        # Generate next 12 months of deadlines
        for filing in requirements.get('annual_filings', []):
            # Calculate next due date
            if 'anniversary' in filing['due'].lower():
                next_due = formed.replace(year=today.year)
                if next_due < today:
                    next_due = next_due.replace(year=today.year + 1)
            elif 'April 15' in filing['due']:
                next_due = datetime(today.year, 4, 15)
                if next_due < today:
                    next_due = datetime(today.year + 1, 4, 15)
            else:
                next_due = today + timedelta(days=30)
            
            calendar.append({
                'deadline': next_due.strftime('%Y-%m-%d'),
                'filing': filing['filing'],
                'fee': filing['fee'],
                'agency': filing['agency'],
                'days_until': (next_due - today).days
            })
        
        # Sort by date
        calendar.sort(key=lambda x: x['deadline'])
        
        # Enrich with live SOS data if entity number provided
        if entity_number:
            try:
                live_status = self.check_live_status(entity_number)
                if live_status and 'error' not in live_status:
                    for item in calendar:
                        item['sos_live_status'] = live_status.get('status', 'UNKNOWN')
                        item['in_good_standing'] = live_status.get('in_good_standing')
                    if not live_status.get('in_good_standing'):
                        # Inject urgent compliance alert at the top
                        calendar.insert(0, {
                            'deadline': today.strftime('%Y-%m-%d'),
                            'filing': '🚨 ENTITY NOT IN GOOD STANDING — Immediate Action Required',
                            'fee': None,
                            'agency': 'Secretary of State / Franchise Tax Board',
                            'days_until': 0,
                            'sos_live_status': live_status.get('status'),
                            'action_required': live_status.get('action_required'),
                            'in_good_standing': False
                        })
            except Exception:
                pass  # Graceful degradation — calendar still works without live data
        
        return calendar
    
    def check_live_status(self, entity_number: str) -> Optional[Dict]:
        """
        Check real-time entity status via CA Secretary of State API.
        
        Args:
            entity_number: CA SOS entity number (e.g., "202012345678")
            
        Returns:
            Good Standing result from CASosClient, or None if unavailable.
        """
        try:
            from execution.integrations.ca_sos_api import CASosClient
            client = CASosClient()
            return client.check_good_standing(entity_number)
        except ImportError:
            return None
        except Exception as e:
            return {"error": str(e), "entity_number": entity_number}
    
    def search_entity(self, name: str) -> Optional[Dict]:
        """
        Search for a CA business entity by name via SOS API.
        
        Args:
            name: Business name to search for
            
        Returns:
            Search results from CASosClient, or None if unavailable.
        """
        try:
            from execution.integrations.ca_sos_api import CASosClient
            client = CASosClient()
            return client.search_entity(name)
        except ImportError:
            return None
        except Exception as e:
            return {"error": str(e), "search_term": name}
    
    def governance_checklist(self, entity_type: str) -> Dict:
        """Generate governance checklist."""
        requirements = self.CA_REQUIREMENTS.get(entity_type.lower(), {})
        
        return {
            'entity_type': entity_type,
            'required_documents': requirements.get('governance', []),
            'best_practices': [
                'Document all major business decisions in writing',
                'Maintain separation between personal and business finances',
                'Hold annual meetings (even if informal for single-member)',
                'Keep formation documents and amendments accessible',
                'Review operating agreement/bylaws annually',
            ],
            'common_mistakes': [
                '❌ Commingling personal and business funds',
                '❌ Missing annual filings (leads to suspension)',
                '❌ No written records of major decisions',
                '❌ Operating agreement never updated since formation',
            ]
        }


# =============================================================================
# 4. RISK ASSESSMENT
# =============================================================================

class RiskAssessor:
    """
    Evaluate business decisions for legal exposure.
    """
    
    RISK_CATEGORIES = {
        'employment': {
            'weight': 0.25,
            'factors': ['employee_count', 'contractors', 'remote_workers', 'turnover_rate']
        },
        'contracts': {
            'weight': 0.20,
            'factors': ['customer_contracts', 'vendor_contracts', 'contractor_agreements']
        },
        'regulatory': {
            'weight': 0.20,
            'factors': ['industry_type', 'licenses_required', 'data_handling']
        },
        'liability': {
            'weight': 0.20,
            'factors': ['customer_facing', 'professional_services', 'product_liability']
        },
        'ip': {
            'weight': 0.15,
            'factors': ['trademarks', 'trade_secrets', 'software', 'content']
        }
    }
    
    def assess_business(self, business_profile: Dict) -> Dict:
        """
        Assess overall business legal risk.
        
        Args:
            business_profile: Dict with business characteristics
                - employee_count: int
                - industry: str
                - annual_revenue: float
                - years_in_business: int
                - has_contracts: bool
                - handles_data: bool
        """
        risks = []
        
        # Employment risks
        employees = business_profile.get('employee_count', 0)
        if employees >= 5:
            risks.append({
                'category': 'Employment',
                'risk': 'FEHA compliance required',
                'severity': 'medium',
                'action': 'Ensure anti-discrimination policies in place'
            })
        if employees >= 50:
            risks.append({
                'category': 'Employment', 
                'risk': 'CFRA/FMLA applies',
                'severity': 'high',
                'action': 'Implement family leave policies and tracking'
            })
        if employees >= 100:
            risks.append({
                'category': 'Employment',
                'risk': 'WARN Act applies',
                'severity': 'medium',
                'action': '60-day notice required for mass layoffs'
            })
        
        # Data/Privacy risks
        if business_profile.get('handles_data', False):
            risks.append({
                'category': 'Privacy',
                'risk': 'CCPA/CPRA compliance',
                'severity': 'high',
                'action': 'Implement privacy policy, data handling procedures'
            })
        
        # Contract risks
        if business_profile.get('annual_revenue', 0) > 100000:
            risks.append({
                'category': 'Contracts',
                'risk': 'Significant contract exposure',
                'severity': 'medium',
                'action': 'Review all customer/vendor contracts for liability caps'
            })
        
        # Calculate overall score
        severity_scores = {'high': 3, 'medium': 2, 'low': 1}
        total_score = sum(severity_scores[r['severity']] for r in risks)
        
        risk_level = 'LOW' if total_score < 5 else 'MEDIUM' if total_score < 10 else 'HIGH'
        
        return {
            'overall_risk_level': risk_level,
            'risk_score': total_score,
            'risk_count': len(risks),
            'risks': sorted(risks, key=lambda x: severity_scores[x['severity']], reverse=True),
            'priority_actions': [r['action'] for r in risks if r['severity'] == 'high'][:3],
            'assessment_date': datetime.now().isoformat()
        }


# =============================================================================
# 5. REGULATORY COMPLIANCE
# =============================================================================

class RegulatoryChecker:
    """
    Business licenses, permits, industry regulations.
    """
    
    CA_LICENSE_REQUIREMENTS = {
        'general': [
            {'license': 'Business License', 'issuer': 'City/County', 'renewal': 'Annual'},
            {'license': 'Seller\'s Permit', 'issuer': 'CDTFA', 'renewal': 'N/A', 'condition': 'If selling tangible goods'},
            {'license': 'EIN', 'issuer': 'IRS', 'renewal': 'N/A', 'condition': 'If employees or corporate'},
        ],
        'construction': [
            {'license': 'Contractor\'s License', 'issuer': 'CSLB', 'renewal': 'Biennial'},
            {'license': 'Workers\' Comp Insurance', 'issuer': 'Various', 'renewal': 'Annual'},
        ],
        'food_service': [
            {'license': 'Health Permit', 'issuer': 'County Health Dept', 'renewal': 'Annual'},
            {'license': 'Food Handler Certification', 'issuer': 'Various', 'renewal': 'Varies'},
            {'license': 'ABC License', 'issuer': 'ABC', 'renewal': 'Annual', 'condition': 'If serving alcohol'},
        ],
        'professional_services': [
            {'license': 'Professional License', 'issuer': 'State Board', 'renewal': 'Biennial typically'},
        ],
        'healthcare': [
            {'license': 'Medical/Healthcare License', 'issuer': 'Medical Board of CA', 'renewal': 'Biennial'},
            {'license': 'HIPAA Compliance', 'issuer': 'Self-certification', 'renewal': 'Ongoing'},
        ],
        'retail': [
            {'license': 'Seller\'s Permit', 'issuer': 'CDTFA', 'renewal': 'N/A'},
            {'license': 'Resale Certificate', 'issuer': 'CDTFA', 'renewal': 'N/A'},
        ],
    }
    
    def check_requirements(self, industry: str, location: str = 'California') -> Dict:
        """Check regulatory requirements for industry."""
        
        # Get general + industry-specific requirements
        requirements = list(self.CA_LICENSE_REQUIREMENTS.get('general', []))
        industry_reqs = self.CA_LICENSE_REQUIREMENTS.get(industry.lower().replace(' ', '_'), [])
        requirements.extend(industry_reqs)
        
        return {
            'industry': industry,
            'location': location,
            'required_licenses': requirements,
            'common_pitfalls': [
                'Operating without city business license',
                'Missing seller\'s permit for taxable sales',
                'Expired professional licenses',
                'No workers\' comp for employees',
            ],
            'resources': [
                {'name': 'CalGold', 'url': 'calgold.ca.gov', 'description': 'CA business permit lookup'},
                {'name': 'CDTFA', 'url': 'cdtfa.ca.gov', 'description': 'Sales tax and seller\'s permits'},
                {'name': 'CSLB', 'url': 'cslb.ca.gov', 'description': 'Contractor licensing'},
            ]
        }


# =============================================================================
# 6. DISPUTE STRATEGY
# =============================================================================

class DisputeStrategist:
    """
    Customer complaints, vendor issues, collections.
    """
    
    CA_COLLECTION_RULES = {
        'small_claims_limit': 12500,  # As of 2024
        'interest_rate_legal': 0.10,  # 10% per annum
        'sol_written_contract': 4,  # years
        'sol_oral_contract': 2,
        'sol_goods_services': 4,
    }
    
    def collection_strategy(self, amount: float, debt_type: str, debt_age_days: int) -> Dict:
        """Generate collection strategy."""
        
        # Check statute of limitations
        sol_years = self.CA_COLLECTION_RULES.get(f'sol_{debt_type}', 4)
        sol_days = sol_years * 365
        sol_expired = debt_age_days > sol_days
        
        # Determine venue
        if amount <= self.CA_COLLECTION_RULES['small_claims_limit']:
            venue = 'Small Claims Court'
            venue_note = 'No attorney needed. Quick resolution.'
        elif amount <= 25000:
            venue = 'Limited Civil Court'
            venue_note = 'Simplified procedures. Attorney optional.'
        else:
            venue = 'Unlimited Civil Court'
            venue_note = 'Full litigation. Attorney recommended.'
        
        strategy = {
            'amount': amount,
            'debt_age_days': debt_age_days,
            'sol_status': 'EXPIRED' if sol_expired else f'{sol_days - debt_age_days} days remaining',
            'recommended_venue': venue,
            'venue_note': venue_note,
            'steps': []
        }
        
        if sol_expired:
            strategy['warning'] = '⚠️ Statute of limitations expired. Legal action not recommended.'
            return strategy
        
        # Build strategy steps
        strategy['steps'] = [
            {
                'step': 1,
                'action': 'Send formal demand letter',
                'timeline': 'Immediately',
                'details': 'Certified mail, return receipt. Give 10-day deadline.'
            },
            {
                'step': 2,
                'action': 'Follow up by phone/email',
                'timeline': 'After 5 days',
                'details': 'Document all contact attempts.'
            },
            {
                'step': 3,
                'action': 'Offer payment plan',
                'timeline': 'If no response after 10 days',
                'details': 'Get agreement in writing. Include default provisions.'
            },
            {
                'step': 4,
                'action': f'File in {venue}',
                'timeline': 'If no resolution after 30 days',
                'details': venue_note
            },
        ]
        
        # Calculate potential recovery
        interest = amount * self.CA_COLLECTION_RULES['interest_rate_legal'] * (debt_age_days / 365)
        strategy['potential_recovery'] = {
            'principal': amount,
            'interest': round(interest, 2),
            'total': round(amount + interest, 2)
        }
        
        return strategy
    
    def demand_letter_template(self, creditor: str, debtor: str, amount: float, 
                                invoice_date: str, description: str) -> str:
        """Generate demand letter."""
        deadline = (datetime.now() + timedelta(days=10)).strftime('%B %d, %Y')
        
        return f"""
{creditor}
[Your Address]
[City, State ZIP]

{datetime.now().strftime('%B %d, %Y')}

{debtor}
[Debtor Address]
[City, State ZIP]

Re: Demand for Payment - Amount Due: ${amount:,.2f}

Dear {debtor}:

This letter serves as formal demand for payment of the outstanding balance owed to {creditor}.

**Amount Due:** ${amount:,.2f}
**Original Invoice Date:** {invoice_date}
**Description:** {description}

Despite prior requests for payment, this balance remains unpaid. Under California law, you are liable for this amount plus interest at the legal rate of 10% per annum.

**DEMAND:** Payment in full of ${amount:,.2f} must be received by {deadline}.

If payment is not received by this date, we will pursue all available legal remedies, including filing suit in the appropriate California court. You will be responsible for court costs, and any additional interest accrued.

To resolve this matter, please remit payment to the address above or contact us immediately to discuss payment arrangements.

Sincerely,

{creditor}
"""


# =============================================================================
# UNIFIED BUSINESS TOOLS INTERFACE
# =============================================================================

class BusinessLegalTools:
    """
    Unified interface to all Small Business Pro tools.
    """
    
    def __init__(self):
        self.contract_reviewer = ContractReviewer()
        self.employment_advisor = EmploymentAdvisor()
        self.entity_manager = EntityManager()
        self.risk_assessor = RiskAssessor()
        self.regulatory_checker = RegulatoryChecker()
        self.dispute_strategist = DisputeStrategist()
    
    def get_tool(self, capability: str):
        """Get tool by capability name."""
        tools = {
            'contract_review': self.contract_reviewer,
            'employment': self.employment_advisor,
            'entity': self.entity_manager,
            'risk': self.risk_assessor,
            'regulatory': self.regulatory_checker,
            'dispute': self.dispute_strategist,
        }
        return tools.get(capability.lower())


if __name__ == '__main__':
    print("=" * 60)
    print("SMALL BUSINESS PRO - LEGAL TOOLS")
    print("=" * 60)
    
    tools = BusinessLegalTools()
    
    # Test Contract Review
    print("\n📋 CONTRACT REVIEW TEST")
    sample_contract = """
    This Service Agreement includes standard indemnification provisions.
    The agreement shall automatically renew for successive one-year terms.
    Any disputes shall be resolved by binding arbitration in Los Angeles.
    Contractor agrees not to compete for 2 years after termination.
    """
    result = tools.contract_reviewer.analyze(sample_contract, 'service_agreement')
    print(f"Risk Level: {result['risk_level']}")
    print(f"Risks Found: {len(result['risks'])}")
    for r in result['risks'][:3]:
        print(f"  - {r['risk']} ({r['severity']})")
    
    # Test Employment
    print("\n👥 EMPLOYMENT CHECKLIST TEST")
    checklist = tools.employment_advisor.new_hire_checklist('non_exempt')
    print(f"Day One Items: {len(checklist['day_one'])}")
    
    # Test Entity Management
    print("\n🏢 ENTITY COMPLIANCE TEST")
    calendar = tools.entity_manager.compliance_calendar('llc', '2023-06-15')
    print(f"Upcoming Deadlines: {len(calendar)}")
    for item in calendar[:2]:
        print(f"  - {item['filing']}: {item['deadline']} ({item['days_until']} days)")
    
    # Test Risk Assessment
    print("\n⚠️ RISK ASSESSMENT TEST")
    profile = {'employee_count': 25, 'annual_revenue': 500000, 'handles_data': True}
    risk = tools.risk_assessor.assess_business(profile)
    print(f"Overall Risk: {risk['overall_risk_level']}")
    print(f"Priority Actions: {risk['priority_actions']}")
    
    # Test Regulatory
    print("\n📜 REGULATORY CHECK TEST")
    reqs = tools.regulatory_checker.check_requirements('retail', 'California')
    print(f"Required Licenses: {len(reqs['required_licenses'])}")
    
    # Test Dispute
    print("\n⚖️ DISPUTE STRATEGY TEST")
    strategy = tools.dispute_strategist.collection_strategy(8500, 'written_contract', 180)
    print(f"Venue: {strategy['recommended_venue']}")
    print(f"Steps: {len(strategy['steps'])}")
    
    print("\n✅ All business tools ready!")
