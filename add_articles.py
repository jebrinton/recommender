#!/usr/bin/env python3
"""
add_articles.py — called by the recommender skill to queue new articles.

Because the skill runs in a sandboxed environment where SQLite file-locking
doesn't work over the mounted filesystem, this script writes a JSON file to
the inbox/ folder. The local web server (running on the user's Mac) picks it
up, imports it into the SQLite database, and updates context.json.

Usage:
    python3 add_articles.py '<json_payload>'

Payload shape:
{
  "date": "2026-03-31",        # optional, defaults to today
  "articles": [
    {
      "title":     "...",
      "url":       "https://...",
      "source":    "Quanta Magazine",
      "summary":   "2–3 sentence description.",
      "takeaways": ["point 1", "point 2", "point 3"],   # list OR plain string
      "topics":    ["math", "physics"],
      "category":  "regular"                             # "regular" | "wildcard"
    }
  ]
}

Outputs JSON: {"queued": "<filename>", "articles": N}
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

BASE = Path(__file__).parent


def queue_articles(payload: dict):
    inbox_dir = BASE / "inbox"
    inbox_dir.mkdir(exist_ok=True)

    if "date" not in payload:
        payload["date"] = str(date.today())

    fname = datetime.now().strftime("%Y-%m-%d-%H%M%S") + ".json"
    (inbox_dir / fname).write_text(json.dumps(payload, indent=2))

    n = len(payload.get("articles", []))
    print(json.dumps({"queued": fname, "articles": n}))
    print(
        f"\n  ✓ {n} articles queued → inbox/{fname}"
        "\n  Open http://localhost:7432 and click Sync to import.\n",
        file=sys.stderr,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 add_articles.py '<json>'")
    queue_articles(json.loads(sys.argv[1]))
