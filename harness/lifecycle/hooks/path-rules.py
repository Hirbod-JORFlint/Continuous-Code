#!/usr/bin/env python3
"""Path-based rule injection hook (neutral Python port, Step 11 Phase B).

Fires on PreToolUse for Read/Edit/Write. Matches file paths against patterns
and injects relevant skill content from the canonical harness skill library.

Legacy .claude path patterns are re-homed to the harness-neutral tree:
  .claude/hooks/      -> harness/lifecycle/hooks/
  .claude/skills/     -> harness/skills/
  .claude/settings.json -> harness/lifecycle/hooks.toml
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from _payload import read_stdin, project_dir


class PathRule:
    __slots__ = ("pattern", "skill_name", "description")

    def __init__(self, pattern: str, skill_name: str, description: str) -> None:
        self.pattern = re.compile(pattern)
        self.skill_name = skill_name
        self.description = description


PATH_RULES: list[PathRule] = [
    PathRule(r"harness/lifecycle/hooks/", "hooks", "Hook development"),
    PathRule(r"harness/skills/", "skill-development", "Skill development"),
    PathRule(r"[.]opc/cache/agents/", "agent-context-isolation", "Agent context isolation"),
    PathRule(r"thoughts/ledgers/CONTINUITY-", "continuity", "Continuity ledger"),
    PathRule(r"opc/scripts/agentica", "async-repl-protocol", "Agentica REPL protocol"),
    PathRule(r"scripts/.*[.]py$", "mcp-scripts", "MCP scripts"),
    PathRule(r"[.]lean$", "llm-tuning-patterns", "LLM tuning for proofs"),
    PathRule(r"skill-rules[.]json$", "router-first-architecture", "Router-first architecture"),
    PathRule(r"harness/lifecycle/hooks[.]toml$", "wiring", "Wiring verification"),
]


def load_skill_content(project_root: Path, skill_name: str) -> str | None:
    for base in (project_root / "harness" / "skills",
                 project_root / ".opcode" / "skills"):
        skill_path = base / skill_name / "SKILL.md"
        if not skill_path.exists():
            continue
        try:
            content = skill_path.read_text(encoding="utf-8")
        except OSError:
            return None
        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                content = content[end + 3:].strip()
        return content
    return None


def matching_skills(file_path: str) -> list[str]:
    return [r.skill_name for r in PATH_RULES if r.pattern.search(file_path)]


def main() -> None:
    payload = read_stdin()
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path")

    if not file_path:
        print("{}")
        return

    skills = matching_skills(str(file_path))
    if not skills:
        print("{}")
        return

    project_root = Path(project_dir(payload, default=str(Path.cwd())))
    contents = [c for s in skills if (c := load_skill_content(project_root, s))]
    if not contents:
        print("{}")
        return

    print(json.dumps({"continue": True, "systemMessage": "\n\n---\n\n".join(contents)}))


if __name__ == "__main__":
    main()
