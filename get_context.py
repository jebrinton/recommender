#!/usr/bin/env python3
"""
get_context.py — called by the recommender skill before searching.

Reads context.json, which is generated and kept up-to-date by the local web
server (running on the user's Mac). If no context file exists yet, returns an
empty context so the first run can proceed without history.

Usage:
    python3 get_context.py
"""

import json
import sys
from pathlib import Path

CONTEXT_PATH = Path(__file__).parent / "context.json"
CONSTITUTION_PATH = Path(__file__).parent / "constitution.md"


def get_context() -> dict:
    if CONTEXT_PATH.exists():
        try:
            ctx = json.loads(CONTEXT_PATH.read_text())
            # Also attach the latest constitution from file (server may not
            # have synced it yet if the skill just wrote it)
            if CONSTITUTION_PATH.exists():
                ctx["constitution"] = CONSTITUTION_PATH.read_text().strip()
            return ctx
        except Exception as e:
            print(f"Warning: could not parse context.json: {e}", file=sys.stderr)

    # First-run fallback — no history yet
    return {
        "past_urls": [],
        "constitution": CONSTITUTION_PATH.read_text().strip() if CONSTITUTION_PATH.exists() else "",
        "rich_feedback": [],
        "top_rated_no_notes": [],
        "low_rated": [],
        "noted_unrated": [],
        "preferred_topics": [],
        "disliked_topics": [],
        "preferred_sources": [],
        "disliked_sources": [],
        "unincorporated_feedback": 0,
        "total_articles": 0,
        "total_runs": 0,
        "note": "No context.json yet. Start the web server (bash start.sh) and click Sync to initialize.",
    }


if __name__ == "__main__":
    print(json.dumps(get_context(), indent=2))
