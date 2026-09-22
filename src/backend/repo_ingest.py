"""
GitHub Repository Ingestion Pipeline for Krusch-Nexus.

Clones a GitHub repo, parses source files, generates LLM-based summaries/tags
via a lightweight local model, and indexes them into the Nexus knowledge base
(pgvector + GraphRAG).

This gives Nexus the ability to treat entire codebases as searchable
institutional knowledge alongside its existing document ingestion.
"""

import os
import subprocess
import json
import time
from typing import Optional
from pathlib import Path

import httpx
from llama_index.core import Document
import concurrent.futures

from fastapi import BackgroundTasks
from .db import SessionLocal, Workspace, Document as DocModel
from .rag_engine import index_documents, extract_and_store_graph, Settings

# --- Configuration ---
TAGGING_PROVIDER = os.getenv("TAGGING_PROVIDER", "ollama").lower()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_TAG_MODEL = os.getenv("OPENROUTER_TAG_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
OLLAMA_TAG_HOST = os.getenv("OLLAMA_TAG_HOST", "http://10.0.0.144:11434")
OLLAMA_TAG_MODEL = os.getenv("OLLAMA_TAG_MODEL", "qwen2.5-coder:1.5b")
CLONE_DIR = os.getenv("REPO_CLONE_DIR", "/tmp/nexus-repos")

# File extensions worth indexing (source code + docs)
INDEXABLE_EXTENSIONS = {
    '.ts', '.tsx', '.js', '.jsx', '.py', '.md', '.mdx',
    '.json', '.yaml', '.yml', '.toml', '.sql', '.sh',
    '.go', '.rs', '.java', '.kt', '.swift', '.rb',
    '.css', '.html', '.vue', '.svelte',
    '.dockerfile', '.env.example',
}

# Directories to always skip
SKIP_DIRS = {
    'node_modules', '.git', '__pycache__', '.venv', 'venv',
    'dist', 'build', '.next', '.nuxt', 'coverage',
    '.turbo', '.cache', 'vendor', 'target',
}

# Max file size to process (50KB)
MAX_FILE_SIZE = 50_000

TAG_SYSTEM_PROMPT = """You are a code librarian. Given a source code file, output a JSON object with:
- "summary": A single sentence (max 120 chars) describing what this file does.
- "tags": An array of 3-5 lowercase tags categorizing the file.
- "security_critical": A boolean indicating if this file handles authentication, cryptography, passwords, or PII.
- "component_type": A string categorizing the component (e.g., "Frontend", "API", "Database Schema", "CI/CD Workflow", "Config", "Script").

Rules:
- Summary must be specific about the technology, pattern, or feature.
- Output ONLY valid JSON, no markdown fences.

Example: {"summary":"Express middleware for JWT auth with RBAC","tags":["auth","jwt","middleware","express"],"security_critical":true,"component_type":"API"}"""


def clone_repo(repo_url: str, target_dir: str) -> str:
    """Clone a GitHub repo to a local directory. Returns the clone path."""
    if os.path.exists(target_dir):
        print(f"  Cleaning existing directory: {target_dir}")
        subprocess.run(["rm", "-rf", target_dir], check=True)

    print(f"  Cloning {repo_url} into {target_dir}...")
    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, target_dir],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE
    )
    return target_dir


def discover_files(repo_dir: str) -> list:
    """Find all indexable source files in the cloned repo, ignoring build/meta dirs."""
    files = []
    repo_path = Path(repo_dir)

    for path in repo_path.rglob('*'):
        if not path.is_file():
            continue

        # Check skip dirs
        parts = path.relative_to(repo_path).parts
        if any(part in SKIP_DIRS for part in parts[:-1]):
            continue

        # Check extension
        ext = path.suffix.lower()
        if ext not in INDEXABLE_EXTENSIONS and path.name.lower() not in ('.dockerfile', 'dockerfile'):
            continue

        # Check file size
        try:
            size = path.stat().st_size
            if size > MAX_FILE_SIZE or size == 0:
                continue
            
            with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()

            if not content.strip():
                continue

            files.append({
                'path': str(path),
                'relative_path': str(path.relative_to(repo_path)),
                'name': path.name,
                'content': content,
                'size': size,
                'extension': ext,
            })
        except Exception:
            continue

    return files


def parse_tag_json(raw: str) -> Optional[dict]:
    """Clean and parse JSON from LLM output."""
    if not raw:
        return None
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    last_brace = cleaned.rfind('}')
    if last_brace > 0:
        cleaned = cleaned[:last_brace + 1]

    try:
        parsed = json.loads(cleaned)
        if "summary" in parsed and "tags" in parsed:
            return {
                "summary": str(parsed["summary"])[:200],
                "tags": [str(t).lower()[:30] for t in parsed["tags"][:8]],
                "security_critical": bool(parsed.get("security_critical", False)),
                "component_type": str(parsed.get("component_type", "Unknown"))[:50]
            }
    except Exception:
        pass
    return None


def generate_file_tags(content: str, filename: str) -> Optional[dict]:
    """Call the tagging model (OpenRouter or local Ollama) to generate summary + tags for a file."""
    truncated = content[:4000]
    prompt = f"File: {filename}\n\n{truncated}"

    # OpenRouter provider check
    if (TAGGING_PROVIDER == "openrouter" or (OPENROUTER_API_KEY and TAGGING_PROVIDER != "ollama")):
        try:
            resp = httpx.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://github.com/krusch-nexus",
                    "X-Title": "Krusch-Nexus Tagging Engine",
                    "Content-Type": "application/json"
                },
                json={
                    "model": OPENROUTER_TAG_MODEL,
                    "messages": [
                        {"role": "system", "content": TAG_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 200
                },
                timeout=30.0
            )
            resp.raise_for_status()
            data = resp.json()
            if "choices" in data and len(data["choices"]) > 0:
                raw = data["choices"][0]["message"]["content"].strip()
                result = parse_tag_json(raw)
                if result:
                    return result
        except Exception as e:
            print(f"    OpenRouter tagging error for {filename}: {e}. Falling back to local Ollama...")

    # Local Ollama provider check / fallback
    try:
        resp = httpx.post(
            f"{OLLAMA_TAG_HOST}/api/generate",
            json={
                "model": OLLAMA_TAG_MODEL,
                "system": TAG_SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,
                    "num_predict": 200,
                }
            },
            timeout=30.0
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
        result = parse_tag_json(raw)
        if result:
            return result
    except Exception as e:
        if not isinstance(e, json.JSONDecodeError):
            print(f"    Local Ollama tagging error for {filename}: {e}")
    return None


def process_single_file(file_info, workspace_id, workspace_name, repo_name, repo_url, generate_tags, background_tasks):
    db = SessionLocal()
    try:
        # Generate tags if enabled
        tag_meta = {}
        tagged = False
        if generate_tags:
            result = generate_file_tags(file_info['content'], file_info['name'])
            if result:
                tag_meta = {
                    "ai_summary": result["summary"],
                    "ai_tags": ", ".join(result["tags"]),
                    "security_critical": result["security_critical"],
                    "component_type": result["component_type"],
                }
                tagged = True

        # Check if already ingested
        existing = db.query(DocModel).filter(
            DocModel.filename == file_info['path'],
            DocModel.workspace_id == workspace_id
        ).first()

        if existing:
            return {"status": "skipped"}

        # Create document record
        doc_record = DocModel(filename=file_info['path'], workspace_id=workspace_id)
        db.add(doc_record)
        db.commit()
        db.refresh(doc_record)

        # Build LlamaIndex Document with rich metadata
        metadata = {
            "document_id": doc_record.id,
            "workspace_id": workspace_id,
            "workspace_name": workspace_name,
            "filename": file_info['name'],
            "filepath": file_info['path'],
            "extension": file_info['extension'],
            "repo": repo_name,
            "repo_url": repo_url,
            "timestamp": str(time.time()),
            **tag_meta,
        }

        doc = Document(
            text=file_info['content'],
            metadata=metadata,
        )

        # Index into pgvector
        index_documents([doc], metadata, background_tasks=background_tasks, extract_graph=False)
        return {"status": "success", "tagged": tagged}

    except Exception as e:
        return {"status": "error", "error": str(e), "path": file_info['path']}
    finally:
        db.close()


def ingest_github_repo(
    repo_url: str,
    workspace_name: Optional[str] = None,
    generate_tags: bool = True,
    max_files: Optional[int] = None,
    background_tasks: Optional[BackgroundTasks] = None,
) -> dict:
    """
    Full pipeline: clone → discover → tag → index into Nexus.

    Args:
        repo_url: GitHub URL (e.g. https://github.com/vercel/ai)
        workspace_name: Nexus workspace to store under (defaults to repo name)
        generate_tags: Whether to call the tagging model
        max_files: Limit files to process (for testing)

    Returns:
        dict with counts of processed, tagged, indexed files
    """
    repo_name = repo_url.rstrip('/').split('/')[-1].replace('.git', '')
    workspace_name = workspace_name or f"repo:{repo_name}"

    print(f"\n{'='*60}")
    print(f"Nexus Repo Ingestion: {repo_name}")
    print(f"{'='*60}")

    # Step 1: Clone
    clone_path = clone_repo(repo_url, CLONE_DIR)

    # Step 2: Discover files
    files = discover_files(clone_path)
    if max_files:
        files = files[:max_files]
    print(f"  Discovered {len(files)} indexable files")

    # Step 3: Ensure workspace exists
    db = SessionLocal()
    workspace = db.query(Workspace).filter(Workspace.name == workspace_name).first()
    if not workspace:
        workspace = Workspace(name=workspace_name, description=f"GitHub repo: {repo_url}")
        db.add(workspace)
        db.commit()
        db.refresh(workspace)
        print(f"  Created workspace: {workspace_name}")
    else:
        print(f"  Using existing workspace: {workspace_name}")
    workspace_id = workspace.id
    db.close()

    # Step 4: Process files
    stats = {"total": len(files), "indexed": 0, "tagged": 0, "failed": 0}
    start_time = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(
                process_single_file,
                file_info, workspace_id, workspace_name, repo_name, repo_url, generate_tags, background_tasks
            ): file_info for file_info in files
        }

        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            res = future.result()
            if res["status"] == "success":
                stats["indexed"] += 1
                if res.get("tagged"):
                    stats["tagged"] += 1
            elif res["status"] == "error":
                stats["failed"] += 1
                if stats["failed"] <= 5:
                    print(f"  Error on {res['path']}: {res['error']}")
                    
            if (i + 1) % 25 == 0:
                elapsed = time.time() - start_time
                rate = stats["indexed"] / elapsed if elapsed > 0 else 0
                print(f"  [{elapsed:.0f}s] {stats['indexed']}/{stats['total']} indexed, "
                      f"{stats['tagged']} tagged ({rate:.1f}/s)")

    elapsed = time.time() - start_time
    print(f"\n  Done: {stats['indexed']} indexed, {stats['tagged']} tagged, "
          f"{stats['failed']} failed ({elapsed:.0f}s)")
    return stats
