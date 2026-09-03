#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""PromptSubmit Hook: Skill Activation Prompt (Python port).

Detects skill/agent matches from skill-rules.json based on prompt triggers,
runs agentica pattern inference, and injects an activation reminder. Ports
shared/resource-reader.ts (readResourceState) and skill-validation-prompt.ts
(shouldValidateWithLLM / buildValidationPrompt) inline as native Python.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _payload import read_stdin  # noqa: E402

# Pattern-to-agent mapping
PATTERN_AGENT_MAP = {
    "swarm": "research-agent",
    "hierarchical": "kraken",
    "pipeline": "kraken",
    "generator_critic": "review-agent",
    "adversarial": "validate-agent",
    "map_reduce": "kraken",
    "jury": "validate-agent",
    "blackboard": "maestro",
    "circuit_breaker": "kraken",
    "chain_of_responsibility": "maestro",
    "event_driven": "kraken",
}

# Keywords that are commonly ambiguous (have both technical and everyday meanings)
AMBIGUOUS_KEYWORDS = {
    "commit",
    "push",
    "pull",
    "merge",
    "branch",
    "checkout",
    "debug",
    "build",
    "implement",
    "plan",
    "research",
    "deploy",
    "release",
    "fix",
    "test",
    "validate",
    "review",
    "analyze",
    "document",
    "refactor",
    "optimize",
}

# Keywords that are highly specific and unlikely to be false positives
SPECIFIC_TECHNICAL_TERMS = {
    "sympy",
    "braintrust",
    "perplexity",
    "agentica",
    "firecrawl",
    "qlty",
    "repoprompt",
    "ast-grep",
    "morph",
    "ragie",
    "lean4",
    "mathlib",
    "z3",
    "shapely",
    "pint",
}

# Technical context indicators for specific skills
TECHNICAL_CONTEXT_INDICATORS = {
    "commit": ["git", "changes", "files", "message", "push", "repository", "branch", "staged"],
    "push": ["git", "remote", "origin", "branch", "repository", "upstream"],
    "pull": ["git", "remote", "origin", "branch", "merge", "rebase", "request"],
    "merge": ["git", "branch", "conflict", "pull request", "pr"],
    "branch": ["git", "checkout", "create", "switch", "feature"],
    "checkout": ["git", "branch", "file", "commit", "HEAD"],
    "debug": ["error", "bug", "issue", "logs", "stack trace", "exception", "crash", "breakpoint"],
    "build": ["npm", "yarn", "cargo", "make", "compile", "webpack", "bundle", "project"],
    "implement": ["code", "feature", "function", "class", "method", "api", "interface", "module"],
    "plan": ["implementation", "phase", "architecture", "design", "roadmap", "milestone"],
    "research": ["api", "library", "documentation", "docs", "best practices", "pattern", "codebase"],
    "deploy": ["server", "production", "staging", "kubernetes", "docker", "cloud", "ci/cd"],
    "release": ["version", "tag", "changelog", "npm", "package", "publish"],
    "fix": ["bug", "error", "issue", "broken", "failing", "test", "regression"],
    "test": ["unit", "integration", "e2e", "coverage", "spec", "jest", "pytest", "vitest"],
    "validate": ["input", "schema", "data", "form", "field", "type"],
    "review": ["code", "pr", "pull request", "changes", "diff"],
    "analyze": ["code", "codebase", "performance", "metrics", "logs"],
    "document": ["api", "readme", "docs", "jsdoc", "docstring", "comments"],
    "refactor": ["code", "function", "class", "module", "clean up", "simplify"],
    "optimize": ["performance", "speed", "memory", "query", "algorithm"],
}

# Default resource state used when file is missing or corrupt.
DEFAULT_RESOURCE_STATE = {
    "freeMemMB": 4096,
    "activeAgents": 0,
    "maxAgents": 10,
    "contextPct": 0,
}


def shouldValidateWithLLM(match: dict) -> bool:
    """Determines whether a skill match should be validated by LLM."""
    # Never delay explicit invocations
    if match.get("matchType") == "explicit":
        return False

    # Never delay blocking enforcement skills
    if match.get("enforcement") == "block":
        return False

    # Intent pattern matches are usually strong signals
    if match.get("matchType") == "intent":
        return False

    # Highly specific technical terms are unlikely to be false positives
    termLower = (match.get("matchedTerm") or "").lower()
    if termLower in SPECIFIC_TECHNICAL_TERMS:
        return False

    # For keyword matches with ambiguous terms
    if match.get("matchType") == "keyword" and termLower in AMBIGUOUS_KEYWORDS:
        promptLower = (match.get("prompt") or "").lower()

        # Check for technical context indicators that suggest genuine usage
        technicalIndicators = TECHNICAL_CONTEXT_INDICATORS.get(termLower) or []
        for indicator in technicalIndicators:
            # Match whole word only (with word boundaries)
            if re.search(rf"\b{indicator.lower()}\b", promptLower):
                # Technical context found - no validation needed
                return False

        # No technical context found - this IS ambiguous, needs validation
        return True

    # Default: don't validate (assume match is good)
    return False


def buildValidationPrompt(match: dict) -> str:
    skillDesc = match.get("skillDescription") or f"The \"{match.get('skillName')}\" skill"

    return f"Skill validation: Determine if the skill \"{match.get('skillName')}\" is genuinely needed.\n\n**User prompt:**\n\"{match.get('prompt')}\"\n\n**Skill description:**\n{skillDesc}\n\n**Match context:**\n- Matched on: \"{match.get('matchedTerm')}\" ({match.get('matchType')} match)\n\n**Your task:**\nDetermine if the user is requesting functionality that the skill provides, or if they're using the keyword in a different context (e.g., \"commit to an approach\" vs \"git commit\").\n\nRespond with ONLY a JSON object:\n{{\"decision\": \"activate\" | \"skip\", \"confidence\": 0.0-1.0, \"reason\": \"brief explanation\"}}\n\nExamples:\n- \"commit these changes\" -> {{\"decision\": \"activate\", \"confidence\": 0.95, \"reason\": \"User wants to commit code changes\"}}\n- \"commit to this approach\" -> {{\"decision\": \"skip\", \"confidence\": 0.9, \"reason\": \"Using commit as verb meaning to dedicate, not git commit\"}}"


def readResourceState():
    """Read resource state from the JSON file written by status.sh."""
    sessionId = os.environ.get("OPC_SESSION_ID") or str(os.getppid() or os.getpid())
    resourceFile = Path(tempfile.gettempdir()) / f"claude-resources-{sessionId}.json"

    # Return null if file doesn't exist
    if not resourceFile.exists():
        return None

    try:
        content = resourceFile.read_text(encoding="utf-8")
        data = json.loads(content)

        # Merge with defaults to handle missing fields
        return {
            "freeMemMB": data["freeMemMB"] if isinstance(data.get("freeMemMB"), (int, float)) else DEFAULT_RESOURCE_STATE["freeMemMB"],
            "activeAgents": data["activeAgents"] if isinstance(data.get("activeAgents"), (int, float)) else DEFAULT_RESOURCE_STATE["activeAgents"],
            "maxAgents": data["maxAgents"] if isinstance(data.get("maxAgents"), (int, float)) else DEFAULT_RESOURCE_STATE["maxAgents"],
            "contextPct": data["contextPct"] if isinstance(data.get("contextPct"), (int, float)) else DEFAULT_RESOURCE_STATE["contextPct"],
        }
    except Exception:
        # Return null on JSON parse error or file read error
        return None


def runPatternInference(prompt: str, projectDir: str):
    """Run pattern inference using the Python module.

    Returns None if inference fails or module not available.
    Implemented natively in-process (no subprocess needed).
    """
    try:
        scriptPath = Path(projectDir) / "scripts" / "agentica_patterns" / "pattern_inference.py"
        if not scriptPath.exists():
            return None

        # Direct import bypassing __init__.py
        spec = importlib.util.spec_from_file_location("pattern_inference", str(scriptPath))
        patternMod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patternMod)

        result = patternMod.infer_pattern(prompt)
        output = result.to_dict()
        output["work_breakdown_detailed"] = patternMod.generate_work_breakdown(result)
        return output
    except Exception:
        # Pattern inference is optional - fail silently
        return None


def generateAgenticaOutput(inference: dict, prompt: str) -> str:
    output = "\n"
    output += "=" * 50 + "\n"
    output += "AGENTICA PATTERN INFERENCE\n"
    output += "=" * 50 + "\n"
    output += "\n"

    if inference.get("confidence", 0) >= 0.7:
        suggestedAgent = PATTERN_AGENT_MAP.get(inference.get("pattern")) or "kraken"
        output += "SUGGESTED APPROACH:\n"
        output += f"  Agent: {suggestedAgent}\n"
        output += f"  Pattern: {inference.get('work_breakdown_detailed')}\n"
        confidencePct = round(inference.get("confidence", 0) * 100)
        output += f"  Confidence: {confidencePct}%\n"
        output += "\n"
        output += "ACTION: Use AskUserQuestion to confirm before spawning:\n"
        output += f"  \"I'll use {suggestedAgent} to {inference.get('work_breakdown')}. Proceed?\"\n"
        output += "  Options: [Yes, proceed] [Different approach] [Let me explain more]\n"
        if len(inference.get("alternatives") or []) > 0:
            output += f"\nAlternative approaches available: {', '.join(inference['alternatives'])}\n"
    else:
        # Low confidence - ask CDM probe
        output += "CLARIFICATION NEEDED:\n"
        output += "\n"
        if inference.get("clarification_probe"):
            output += f"Ask the user: \"{inference['clarification_probe']}\"\n"
        output += "\n"
        output += "Initial analysis suggests: " + inference.get("work_breakdown") + "\n"
        confidencePct = round(inference.get("confidence", 0) * 100)
        output += f"Confidence: {confidencePct}%\n"
        output += "\n"
        output += "ACTION: Use AskUserQuestion to clarify before proceeding.\n"

    output += "=" * 50 + "\n"
    return output


def detectSemanticQuery(prompt: str) -> dict:
    """Detect semantic/natural language queries that would benefit from TLDR semantic search.

    Pattern: Questions starting with how/what/where/why/when/which
    """
    # Question word patterns that indicate semantic queries
    semanticPatterns = [
        re.compile(r"^(how|what|where|why|when|which)\s", re.IGNORECASE),
        re.compile(r"\?$"),
        re.compile(r"^(find|show|list|get|explain)\s+(all|the|every|any)", re.IGNORECASE),
        re.compile(r"^.*\s+(implementation|architecture|flow|pattern|logic|system)$", re.IGNORECASE),
    ]

    isSemanticQuery = any(p.search(prompt.strip()) for p in semanticPatterns)

    if not isSemanticQuery:
        return {"isSemanticQuery": False}

    # Generate suggestion for semantic search
    shortPrompt = prompt[:50] + "..." if len(prompt) > 50 else prompt
    suggestion = f"💡 **Semantic Query Detected**\n\nYour question \"{shortPrompt}\" may benefit from semantic code search.\n\n**Try:**\n```bash\ntldr semantic search \"{prompt[:100]}\" .\n```\n\nOr use the /explore skill for guided exploration.\n"

    return {"isSemanticQuery": True, "suggestion": suggestion}


def main() -> None:
    payload = read_stdin(timeout=2.0)

    # Early validation - prompt is required
    if not payload.get("prompt") or not isinstance(payload.get("prompt"), str):
        return
    promptLower = payload["prompt"].lower()

    # Load skill rules (try project first, then global)
    projectDir = os.environ.get("OPC_PROJECT_DIR") or os.getcwd()
    homeDir = os.environ.get("HOME") or os.environ.get("USERPROFILE") or ""
    projectRulesPath = Path(projectDir) / "harness" / "skills" / "skill-rules.json"
    globalRulesPath = Path(homeDir) / ".opc" / "skills" / "skill-rules.json"

    rulesPath = ""
    if projectRulesPath.exists():
        rulesPath = str(projectRulesPath)
    elif globalRulesPath.exists():
        rulesPath = str(globalRulesPath)
    else:
        # No rules file found, exit silently
        return

    try:
        rules = json.loads(Path(rulesPath).read_text(encoding="utf-8"))
    except Exception:
        return

    # CHANGE 1: Run pattern inference EARLY on all prompts
    patternInference = runPatternInference(payload["prompt"], projectDir)

    # CHANGE 3: Detect semantic queries and suggest TLDR semantic search
    semanticQuery = detectSemanticQuery(payload["prompt"])

    matchedSkills = []

    # Check each skill for matches
    for skillName, config in (rules.get("skills") or {}).items():
        triggers = config.get("promptTriggers")
        if not triggers:
            continue

        # Keyword matching
        if triggers.get("keywords"):
            matchedKeyword = next(
                (kw for kw in triggers["keywords"] if kw.lower() in promptLower),
                None,
            )
            if matchedKeyword:
                # Check if this match needs LLM validation
                skillMatchForValidation = {
                    "skillName": skillName,
                    "matchType": "keyword",
                    "matchedTerm": matchedKeyword,
                    "prompt": payload["prompt"],  # Use original prompt (not lowercased)
                    "skillDescription": config.get("description"),
                    "enforcement": config.get("enforcement"),
                }
                needsValidation = shouldValidateWithLLM(skillMatchForValidation)

                matchedSkills.append({
                    "name": skillName,
                    "matchType": "keyword",
                    "matchedTerm": matchedKeyword,
                    "config": config,
                    "needsValidation": needsValidation,
                })
                continue

        # Intent pattern matching (no validation needed - strong signal)
        if triggers.get("intentPatterns"):
            intentMatch = any(
                _test_regex(pattern, promptLower)
                for pattern in triggers["intentPatterns"]
            )
            if intentMatch:
                matchedSkills.append({
                    "name": skillName,
                    "matchType": "intent",
                    "config": config,
                    "needsValidation": False,
                })

    # Check each agent for matches
    matchedAgents = []
    if rules.get("agents"):
        for agentName, config in rules["agents"].items():
            triggers = config.get("promptTriggers")
            if not triggers:
                continue

            # Keyword matching
            if triggers.get("keywords"):
                matchedKeyword = next(
                    (kw for kw in triggers["keywords"] if kw.lower() in promptLower),
                    None,
                )
                if matchedKeyword:
                    # Check if this match needs LLM validation
                    skillMatchForValidation = {
                        "skillName": agentName,
                        "matchType": "keyword",
                        "matchedTerm": matchedKeyword,
                        "prompt": payload["prompt"],
                        "skillDescription": config.get("description"),
                        "enforcement": config.get("enforcement"),
                    }
                    needsValidation = shouldValidateWithLLM(skillMatchForValidation)

                    matchedAgents.append({
                        "name": agentName,
                        "matchType": "keyword",
                        "matchedTerm": matchedKeyword,
                        "config": config,
                        "isAgent": True,
                        "needsValidation": needsValidation,
                    })
                    continue

            # Intent pattern matching (no validation needed - strong signal)
            if triggers.get("intentPatterns"):
                intentMatch = any(
                    _test_regex(pattern, promptLower)
                    for pattern in triggers["intentPatterns"]
                )
                if intentMatch:
                    matchedAgents.append({
                        "name": agentName,
                        "matchType": "intent",
                        "config": config,
                        "isAgent": True,
                        "needsValidation": False,
                    })

    # Generate output if matches found OR pattern inference succeeded OR semantic query detected
    if len(matchedSkills) > 0 or len(matchedAgents) > 0 or patternInference or semanticQuery.get("isSemanticQuery"):
        # Check which skills need LLM validation (potential false positives)
        skillsNeedingValidation = [s for s in matchedSkills if s["needsValidation"]]
        agentsNeedingValidation = [a for a in matchedAgents if a["needsValidation"]]
        allNeedingValidation = skillsNeedingValidation + agentsNeedingValidation

        # Filter out skills that need validation from the main lists
        confirmedSkills = [s for s in matchedSkills if not s["needsValidation"]]
        confirmedAgents = [a for a in matchedAgents if not a["needsValidation"]]

        output = ""

        # CHANGE 2: Show pattern inference output FIRST if available
        if patternInference:
            output += generateAgenticaOutput(patternInference, payload["prompt"])
            output += "\n"

        # CHANGE 3: Show semantic query suggestion if detected
        if semanticQuery.get("isSemanticQuery") and semanticQuery.get("suggestion"):
            output += semanticQuery["suggestion"]
            output += "\n"

        # Show skill activation check only if skills/agents matched
        if len(matchedSkills) > 0 or len(matchedAgents) > 0:
            output += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            output += "🎯 SKILL ACTIVATION CHECK\n"
            output += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

            # Show skills needing validation FIRST
            if len(allNeedingValidation) > 0:
                output += "❓ AMBIGUOUS MATCHES (validate before activating):\n"
                output += "   The following skills matched on keywords that may be used\n"
                output += "   in a non-technical context. Consider if they're needed:\n\n"

                for item in allNeedingValidation:
                    isAgent = " [agent]" if item.get("isAgent") else ""
                    output += f"   • {item['name']}{isAgent}\n"
                    output += f"     Matched: \"{item.get('matchedTerm')}\" (keyword match)\n"
                    if item.get("config", {}).get("description"):
                        output += f"     Purpose: {item['config']['description']}\n"
                    output += f"     → Skip if the user is NOT asking for this functionality\n"
                    output += "\n"

                output += "   VALIDATION: Before activating these, ask yourself:\n"
                output += "   \"Is the user asking for this skill's capability, or just\n"
                output += "    using the word in everyday language?\"\n\n"

            # Group confirmed skills by priority
            critical = [s for s in confirmedSkills if s["config"].get("priority") == "critical"]
            high = [s for s in confirmedSkills if s["config"].get("priority") == "high"]
            medium = [s for s in confirmedSkills if s["config"].get("priority") == "medium"]
            low = [s for s in confirmedSkills if s["config"].get("priority") == "low"]

            if len(critical) > 0:
                output += "⚠️ CRITICAL SKILLS (REQUIRED):\n"
                for s in critical:
                    output += f"  → {s['name']}\n"
                output += "\n"

            if len(high) > 0:
                output += "📚 RECOMMENDED SKILLS:\n"
                for s in high:
                    output += f"  → {s['name']}\n"
                output += "\n"

            if len(medium) > 0:
                output += "💡 SUGGESTED SKILLS:\n"
                for s in medium:
                    output += f"  → {s['name']}\n"
                output += "\n"

            if len(low) > 0:
                output += "📌 OPTIONAL SKILLS:\n"
                for s in low:
                    output += f"  → {s['name']}\n"
                output += "\n"

            # Add confirmed agents
            if len(confirmedAgents) > 0:
                output += "🤖 RECOMMENDED AGENTS (token-efficient):\n"
                for a in confirmedAgents:
                    output += f"  → {a['name']}\n"
                output += "\n"

            if len(confirmedSkills) > 0:
                output += "ACTION: Use Skill tool BEFORE responding\n"
            if len(confirmedAgents) > 0:
                output += "ACTION: Use Task tool with agent for exploration\n"
            output += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"

            # Check if any matched skill has enforcement: 'block'
            blockingSkills = [s for s in matchedSkills if s["config"].get("enforcement") == "block"]
            if len(blockingSkills) > 0:
                # Return blocking response - Claude must invoke the skill first
                blockMessage = output + "\n⛔ BLOCKING: You MUST invoke " + \
                    ", ".join(s["name"] for s in blockingSkills) + \
                    " skill(s) before generating ANY response."
                print(json.dumps({
                    "result": "block",
                    "reason": blockMessage
                }))
                return

        print(output)

    # Check context % from statusLine temp file and add tiered warnings
    # Use hook input session_id first, then env vars as fallback
    # OPC_PPID kept for backwards compatibility with bash wrapper
    rawSessionId = payload.get("session_id") or os.environ.get("OPC_SESSION_ID") or os.environ.get("OPC_PPID") or "default"
    sessionId = rawSessionId[:8]  # Match status.py truncation
    contextFile = Path(tempfile.gettempdir()) / f"opc-context-pct-{sessionId}.txt"
    if contextFile.exists():
        try:
            pct = int(contextFile.read_text(encoding="utf-8").strip())
            contextWarning = ""

            if pct >= 90:
                contextWarning = "\n" + "=" * 50 + "\n" + \
                    "  CONTEXT CRITICAL: " + str(pct) + "%\n" + \
                    "  Run /create_handoff NOW before auto-compact!\n" + \
                    "=" * 50 + "\n"
            elif pct >= 80:
                contextWarning = "\n" + \
                    "CONTEXT WARNING: " + str(pct) + "%\n" + \
                    "Recommend: /create_handoff then /clear soon\n"
            elif pct >= 70:
                contextWarning = "\nContext at " + str(pct) + "%. Consider handoff when you reach a stopping point.\n"

            if contextWarning:
                print(contextWarning)
        except Exception:
            # Ignore read errors
            pass

    # Check resource limits and add advisory warnings
    resources = readResourceState()
    if resources and resources.get("maxAgents", 0) > 0:
        utilization = resources["activeAgents"] / resources["maxAgents"]
        resourceWarning = ""

        if utilization >= 1.0:
            # At or over limit: CRITICAL
            resourceWarning = "\n" + \
                "=" * 50 + "\n" + \
                "RESOURCE CRITICAL: At limit (" + str(resources["activeAgents"]) + "/" + str(resources["maxAgents"]) + " agents)\n" + \
                "Do NOT spawn new agents until existing ones complete.\n" + \
                "=" * 50 + "\n"
        elif utilization >= 0.8:
            # Near limit (80%+): WARNING
            remaining = resources["maxAgents"] - resources["activeAgents"]
            resourceWarning = "\n" + \
                "RESOURCE WARNING: Near limit (" + str(resources["activeAgents"]) + "/" + str(resources["maxAgents"]) + " agents)\n" + \
                "Only " + str(remaining) + " agent slot(s) remaining. Limit spawning.\n"

        if resourceWarning:
            print(resourceWarning)


def _test_regex(pattern: str, text: str) -> bool:
    """Compile and test a case-insensitive regex; invalid patterns return False."""
    try:
        return re.search(pattern, text, re.IGNORECASE) is not None
    except Exception:
        # Invalid regex pattern, skip
        return False


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never crash - degrade to no-op
        pass
