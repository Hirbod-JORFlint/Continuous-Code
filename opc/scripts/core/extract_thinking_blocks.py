#!/usr/bin/env python3
"""
Extract thinking blocks from session transcripts (driver-agnostic).

Two-phase extraction:
1. Deterministic: Extract all thinking blocks (grep-like) from the transcript
2. Filter: Keep only blocks with perception change signals

Transcript formats:
- anthropic (Claude-Code style JSONL): ``type: assistant|user`` with
  ``message.content[]`` entries of ``type: thinking`` (legacy shape).
- codex: rollout JSONL — tolerant recursive scan for ``thinking``/``reasoning``
  keys inside any event payload.
- opencode: event JSONL shaped like anthropic content blocks plus a generic
  ``thinking``/``reasoning`` walker fallback.
- auto: anthropic shape first, generic walker as fallback (default).

Usage:
    uv run python scripts/core/extract_thinking_blocks.py --jsonl path/to/session.jsonl
    uv run python scripts/core/extract_thinking_blocks.py --jsonl path/to/session.jsonl \
        --format codex
    uv run python scripts/core/extract_thinking_blocks.py --jsonl path/to/session.jsonl --filter
    uv run python scripts/core/extract_thinking_blocks.py --jsonl path/to/session.jsonl \
        --output /tmp/blocks.txt
"""

import argparse
import json
import re
import sys
from pathlib import Path

# Perception change signal patterns (Alan Kay "point of view" moments)
PERCEPTION_SIGNALS = [
    r"\bactually\b",
    r"\brealized?\b",
    r"\bthe issue\b",
    r"\bthat'?s why\b",
    r"\bturns out\b",
    r"\bI was wrong\b",
    r"\bworks because\b",
    r"\bthe problem is\b",
    r"\bOh,",
    r"\bAha\b",
    r"\bnow I see\b",
    r"\bnow I understand\b",
    r"\bI see now\b",
    r"\bmisunderstood\b",
    r"\bwait,?\b",
    r"\bhmm\b",
    r"\binteresting\b",
    r"\bunexpected\b",
    r"\bsurpris",  # surprising, surprised
    r"\bdifferent than\b",
    r"\bdifferent from\b",
    r"\bnot what I\b",
    r"\bwasn'?t\b.*\bexpect",
]

PERCEPTION_PATTERN = re.compile("|".join(PERCEPTION_SIGNALS), re.IGNORECASE)

THINKING_KEYS = ("thinking", "reasoning", "chain_of_thought")


def _reasoning_text(payload: dict) -> str | None:
    """Extract thinking text from a modern codex reasoning payload.

    Current codex CLI writes ``payload.type == "reasoning"`` with a
    ``summary`` array of ``{"type": "summary_text", "text": ...}`` entries.
    """
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary")
    if isinstance(summary, list):
        texts = [
            item.get("text", "")
            for item in summary
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if texts:
            return "\n".join(t for t in texts if t.strip())
    content = payload.get("content")
    if isinstance(content, str) and content.strip():
        return content
    return None

_FORMATS = ("anthropic", "codex", "opencode", "auto")


def _block(text: str, timestamp: str | None, line_num: int) -> dict:
    return {
        "thinking": text,
        "timestamp": timestamp,
        "line_num": line_num,
        "has_perception_signal": bool(PERCEPTION_PATTERN.search(text)),
    }


def _iter_json_lines(jsonl_path: Path):
    """Yield (line_num, parsed dict) for each parseable JSONL line."""
    with open(jsonl_path) as f:
        for line_num, line in enumerate(f, 1):
            try:
                data = json.loads(line.strip())
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                yield line_num, data


def _parse_anthropic(jsonl_path: Path) -> list[dict]:
    """Anthropic/Claude-Code JSONL shape: message.content[] type==thinking."""
    blocks: list[dict] = []
    for line_num, data in _iter_json_lines(jsonl_path):
        # Skip non-message types
        if data.get("type") not in ("assistant", "user"):
            continue

        message = data.get("message", {})
        content = message.get("content")

        # Content can be string or array
        if not isinstance(content, list):
            continue

        # Extract thinking blocks from content array
        for item in content:
            if isinstance(item, dict) and item.get("type") == "thinking":
                thinking_text = item.get("thinking", "")
                if not thinking_text:
                    continue
                blocks.append(_block(thinking_text, data.get("timestamp"), line_num))
    return blocks


def _walk_dict(value, line_num: int, blocks: list[dict], depth: int = 0) -> None:
    """Recursively find dicts with a ``thinking``/``reasoning`` key (codex/opencode)."""
    if depth > 12:
        return
    if isinstance(value, dict):
        if value.get("type") in THINKING_KEYS:
            text = _reasoning_text(value)
            if isinstance(text, str) and text.strip():
                blocks.append(_block(text, None, line_num))
        for key, val in value.items():
            if key in THINKING_KEYS:
                nested = isinstance(val, dict) and isinstance(val.get("text"), str)
                text = val if isinstance(val, str) else (
                    val.get("text") if nested else None
                )
                if isinstance(text, str) and text.strip():
                    blocks.append(_block(text, None, line_num))
            _walk_dict(val, line_num, blocks, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _walk_dict(item, line_num, blocks, depth + 1)


def _parse_generic(jsonl_path: Path) -> list[dict]:
    """Tolerant recursive scan for thinking/reasoning across any event shape."""
    blocks: list[dict] = []
    for line_num, data in _iter_json_lines(jsonl_path):
        _walk_dict(data, line_num, blocks)
    return blocks


def _parse_codex(jsonl_path: Path) -> list[dict]:
    """Codex rollout JSONL.

    Legacy: ``kind == "agent_reasoning"`` with content in payload.content.
    Current CLI: ``type == "response_item"`` with ``payload.type == "reasoning"``
    and thinking text in ``payload.summary[].text``.
    """
    blocks: list[dict] = []
    for line_num, data in _iter_json_lines(jsonl_path):
        payload = data.get("payload")
        if not isinstance(payload, dict):
            continue
        if data.get("type") == "response_item" and payload.get("type") == "reasoning":
            text = _reasoning_text(payload)
            if isinstance(text, str) and text.strip():
                blocks.append(_block(text, data.get("timestamp"), line_num))
            continue
        if data.get("kind") != "agent_reasoning":
            continue
        text = payload.get("content")
        if isinstance(text, str) and text.strip():
            blocks.append(_block(text, data.get("timestamp"), line_num))
    return blocks


def _dedup(blocks: list[dict]) -> list[dict]:
    """Remove duplicate blocks by (line_num, thinking) — keeps first occurrence."""
    seen: set[tuple[int, str]] = set()
    unique: list[dict] = []
    for b in blocks:
        key = (b["line_num"], b["thinking"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(b)
    return unique


def extract_thinking_blocks(
    jsonl_path: Path, filter_perception: bool = False, fmt: str = "auto"
) -> list[dict]:
    """
    Stream through a transcript and extract thinking blocks.

    Args:
        jsonl_path: Path to session transcript (JSONL)
        filter_perception: If True, only return blocks with perception signals
        fmt: Transcript format - anthropic | codex | opencode | auto

    Returns:
        List of blocks with 'thinking', 'timestamp', 'line_num',
        'has_perception_signal'
    """
    if fmt == "anthropic":
        blocks = _parse_anthropic(jsonl_path)
    elif fmt == "codex":
        blocks = _parse_codex(jsonl_path)
        if not blocks:
            blocks = _parse_generic(jsonl_path)
    elif fmt == "opencode":
        combined = _parse_anthropic(jsonl_path) + _parse_codex(jsonl_path)
        blocks = _dedup(combined + _parse_generic(jsonl_path))
    else:  # auto
        blocks = _parse_anthropic(jsonl_path)
        if not blocks:
            blocks = _parse_codex(jsonl_path)
        if not blocks:
            blocks = _parse_generic(jsonl_path)
        blocks = _dedup(blocks)

    if filter_perception:
        blocks = [b for b in blocks if b["has_perception_signal"]]

    return blocks


def main():
    parser = argparse.ArgumentParser(description="Extract thinking blocks from session transcripts")
    parser.add_argument("--jsonl", required=True, help="Path to session JSONL file")
    parser.add_argument(
        "--format",
        choices=list(_FORMATS),
        default="auto",
        help="Transcript format (auto detects anthropic shape first)",
    )
    parser.add_argument("--filter", action="store_true",
                        help="Only extract blocks with perception signals")
    parser.add_argument("--output", help="Output file (default: stdout)")
    parser.add_argument("--format-out", choices=["text", "json"], default="text",
                        help="Output format")
    parser.add_argument("--stats", action="store_true", help="Show statistics only")

    args = parser.parse_args()

    jsonl_path = Path(args.jsonl)
    if not jsonl_path.exists():
        print(f"Error: File not found: {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    # Extract blocks
    blocks = extract_thinking_blocks(
        jsonl_path, filter_perception=args.filter, fmt=args.format
    )

    if args.stats:
        total = len(blocks)
        with_signal = sum(1 for b in blocks if b["has_perception_signal"])
        print(f"Total thinking blocks: {total}")
        print(f"With perception signals: {with_signal}")
        print(f"Ratio: {with_signal/total*100:.1f}%" if total > 0 else "Ratio: N/A")
        return

    # Format output
    if args.format_out == "json":
        output = json.dumps(blocks, indent=2)
    else:
        output = '\n\n---\n\n'.join(
            f"[Line {b['line_num']}] {'*' if b['has_perception_signal'] else ''}\n{b['thinking']}"
            for b in blocks
        )

    # Write output
    if args.output:
        Path(args.output).write_text(output)
        print(f"Wrote {len(blocks)} blocks to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
