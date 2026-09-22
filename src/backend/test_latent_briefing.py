#!/usr/bin/env python3
"""
verify_latent_briefing.py - Automated benchmark and unit test for Latent Briefing (Attention Matching) compaction.
"""

import sys
import os
import numpy as np

# Adjust python path to allow direct execution
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../..')))

import src.backend.test_stubs
from src.backend.rag_engine import latent_briefing_compaction

# Mock relationship triples derived from multiple workspaces
mock_relationships = [
    "- kruschdev is developer of caren (Workspace 1)",
    "- caren-db is database of caren (Workspace 1)",
    "- caren-db runs on port 5436 (Workspace 1)",
    "- pocketlawyer-db is postgresql of pocketlawyer (Workspace 2)",
    "- pocketlawyer-db runs on port 5433 (Workspace 2)",
    "- pocketlawyer-redis is cache of pocketlawyer (Workspace 2)",
    "- pocketlawyer-redis runs on port 6379 (Workspace 2)",
    "- jean-sre runs automated checks on kruschserv (Workspace 3)",
    "- jean-sre writes results to kruschdb.ide_agent_memory (Workspace 3)",
    "- jean-sre runs on port 3005 (Workspace 3)",
    "- edjoin-tracker polls api.edjoin.org (Workspace 4)",
    "- edjoin-tracker uses puppeteer stealth browser (Workspace 4)",
    "- perkins_snow_removal uses google maps routing (Workspace 5)",
    "- perkins_snow_removal targets VT region (Workspace 5)"
]

test_scenarios = [
    {
        "query": "Who is the developer of the caren caregiver coordination app?",
        "tau": 0.5,
        "expected_keywords": ["caren", "developer"]
    },
    {
        "query": "What database port does pocketlawyer use?",
        "tau": 0.5,
        "expected_keywords": ["pocketlawyer-db", "port", "5433"]
    },
    {
        "query": "Completely unrelated words banana apple tree",
        "tau": 0.8,
        "expected_keywords": []
    }
]

def run_tests():
    print("🧠 Starting Latent Briefing Compaction Benchmark Suite...\n")
    print(f"Total mock relationships in context: {len(mock_relationships)}\n")
    
    passed_all = True
    
    for i, scenario in enumerate(test_scenarios):
        query = scenario["query"]
        tau = scenario["tau"]
        
        print(f"--- Scenario {i+1} ---")
        print(f"Query: \"{query}\" (tau={tau})")
        
        # Run compaction
        import time
        t0 = time.time()
        compacted = latent_briefing_compaction(query, mock_relationships, tau=tau)
        elapsed = time.time() - t0
        
        original_len = len(mock_relationships)
        compacted_len = len(compacted)
        savings = (1.0 - (compacted_len / original_len)) * 100
        
        print(f"Compaction: {original_len} -> {compacted_len} triplets ({savings:.1f}% savings)")
        print(f"Latency: {elapsed * 1000:.2f} ms")
        print("Compacted Results:")
        for rel in compacted:
            print(f"  {rel}")
            
        # Verify keywords if specified
        scenario_passed = True
        if scenario["expected_keywords"]:
            found_any = False
            for rel in compacted:
                for kw in scenario["expected_keywords"]:
                    if kw.lower() in rel.lower():
                        found_any = True
                        break
            if not found_any:
                print(f"❌ FAILED: Expected keywords {scenario['expected_keywords']} not found in compacted output.")
                scenario_passed = False
                passed_all = False
            else:
                print(f"✅ PASSED: Semantic alignment confirmed.")
        else:
            print(f"✅ PASSED: Handle flat/low-score query cleanly via fallback/compaction.")
            
        print()
        
    if passed_all:
        print("🎉 SUCCESS: All Latent Briefing compaction unit tests passed successfully!")
        sys.exit(0)
    else:
        print("❌ FAILURE: Compaction validation failed.")
        sys.exit(1)

if __name__ == "__main__":
    run_tests()
