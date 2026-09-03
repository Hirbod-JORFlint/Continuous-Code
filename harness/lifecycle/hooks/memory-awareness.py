#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Memory Awareness Hook (UserPromptSubmit) (Python port).

Port of .claude/hooks/src/memory-awareness.ts:
Checks if user prompt is similar to stored learnings.
Shows hint to BOTH user (visible) AND Claude (system context).

Flow:
1. Extract INTENT from user prompt (not just keywords)
2. Semantic search using hybrid RRF (text + vector)
3. If score > threshold, show visible hint with top learning preview
4. Claude proactively discloses and acts on relevant memories
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _lib.opc_path import get_opc_dir  # noqa: E402
from _payload import read_stdin, project_dir  # noqa: E402


def extract_intent(prompt: str) -> str:
    # Meta-phrases to remove (these describe HOW, not WHAT)
    meta_phrases = [
        re.compile(r"^(can you|could you|would you|please|help me|i want to|i need to|let's|lets)\s+", re.IGNORECASE),
        re.compile(r"^(show me|tell me|find|search for|look for|recall|remember)\s+", re.IGNORECASE),
        re.compile(r"^(how do i|how can i|how to|what is|what are|where is|where are)\s+", re.IGNORECASE),
        re.compile(r"\s+(for me|please|thanks|thank you)$", re.IGNORECASE),
        re.compile(r"\?$"),
    ]

    intent = prompt.strip()

    # Strip meta-phrases iteratively
    for pattern in meta_phrases:
        intent = pattern.sub("", intent)

    intent = intent.strip()

    # If we stripped too much, fall back to keyword extraction
    if len(intent) < 5:
        return extract_keywords(prompt)

    return intent


def extract_keywords(prompt: str) -> str:
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "can", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "under", "again",
        "further", "then", "once", "here", "there", "when", "where", "why",
        "how", "all", "each", "few", "more", "most", "other", "some", "such",
        "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very",
        "s", "t", "just", "don", "now", "i", "me", "my", "you", "your", "we", "help", "with",
        "our", "they", "them", "their", "it", "its", "this", "that", "these",
        "what", "which", "who", "whom", "and", "but", "if", "or", "because",
        "until", "while", "about", "against", "also", "get", "got", "make",
        "want", "need", "look", "see", "use", "like", "know", "think", "take",
        "come", "go", "say", "said", "tell", "please", "help", "let", "sure",
        "recall", "remember", "similar", "problems", "issues",
    }

    cleaned = re.sub(r"[^\w\s-]", " ", prompt.lower())
    words = [w for w in cleaned.split() if len(w) > 2 and w not in stop_words]

    # Deduplicate preserving order, take first 5
    seen = set()
    uniq: list[str] = []
    for w in words:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
        if len(uniq) == 5:
            break

    return " ".join(uniq)


def check_memory_relevance(intent: str, project_dir_str: str) -> dict[str, Any] | None:
    if not intent or len(intent) < 3:
        return None

    opc_dir = get_opc_dir()
    if not opc_dir:
        return None  # Graceful degradation if OPC not available

    # PostgreSQL full-text search handles stopwords automatically via plainto_tsquery
    # Just clean up the intent: remove paths, underscores, short words
    search_term = re.sub(r"[_\/]", " ", intent)        # Convert underscores/slashes to spaces
    search_term = re.sub(r"\b\w{1,2}\b", "", search_term)  # Remove 1-2 char words
    search_term = re.sub(r"\s+", " ", search_term)     # Collapse whitespace
    search_term = search_term.strip()

    # Use text-only for fast checking (< 1s), user can run /recall for semantic
    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = opc_dir
        result = subprocess.run(
            [
                "uv", "run", "python", "scripts/core/recall_learnings.py",
                "--query", search_term,  # Single keyword for text match
                "--k", "3",
                "--json",
                "--text-only",  # Fast text search for hints
            ],
            cwd=opc_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=5,  # 5s timeout for fast check
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        return None

    if result.returncode != 0 or not result.stdout:
        return None

    try:
        data = json.loads(result.stdout)

        if not data.get("results") or len(data["results"]) == 0:
            return None

        # ts_rank returns small values (0.0001-0.1), ILIKE fallback returns 0.1
        # Any match from FTS is relevant enough to show

        # Extract structured results with better previews
        results: list[dict[str, Any]] = []
        for r in data["results"][:3]:
            content = r.get("content") or ""
            # Get first meaningful line up to 120 chars
            preview = " ".join(
                line.strip() for line in content.split("\n") if line.strip()
            )[:120]

            results.append({
                "id": (r.get("id") or "unknown")[:8],
                "type": r.get("learning_type") or r.get("type") or "UNKNOWN",
                "content": preview + ("..." if len(content) > 120 else ""),
                "score": r.get("score") or 0,
            })

        return {
            "count": len(data["results"]),
            "results": results,
        }
    except Exception:
        return None


def main() -> None:
    payload = read_stdin(timeout=2.0)
    project_dir_str = os.environ.get("OPC_PROJECT_DIR") or project_dir(payload)

    # Skip for subagents - they don't need memory recall (saves tokens)
    if os.environ.get("OPC_AGENT_ID"):
        return

    prompt = payload.get("prompt") or ""

    # Skip very short prompts (greetings, commands)
    if len(prompt) < 15:
        return

    # Skip if prompt is just a slash command
    if prompt.strip().startswith("/"):
        return

    # Extract intent (semantic query, not just keywords)
    intent = extract_intent(prompt)

    # Skip if no meaningful intent
    if len(intent) < 3:
        return

    # Check memory relevance using semantic search
    match = check_memory_relevance(intent, project_dir_str)

    if match:
        # Build structured context for Claude
        result_lines = "\n".join(
            f"{i + 1}. [{r['type']}] {r['content']} (id: {r['id']})"
            for i, r in enumerate(match["results"])
        )

        claude_context = (
            f"MEMORY MATCH ({match['count']} results) for \"{intent}\":\n"
            f"{result_lines}\n"
            f"Use /recall \"{intent}\" for full content. Disclose if helpful."
        )

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": claude_context,
            }
        }))


if __name__ == "__main__":
    main()
