#!/usr/bin/env python3
"""
Reading Recommender — local web server (runs on your Mac)
  Start:  bash start.sh   (or: python3 server.py)
  Open:   http://localhost:7432

Architecture
  - SQLite database lives locally on your Mac (reading.db)
  - The Cowork skill writes JSON files to inbox/ (works over any filesystem)
  - This server imports inbox files into SQLite and keeps context.json in sync
  - context.json is read by the skill each run to load preferences/history
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn, json, shutil
from pathlib import Path
from datetime import date

from db import init_db, get_conn

PORT = 7432
BASE = Path(__file__).parent
CONSTITUTION_PATH = BASE / "constitution.md"
CONTEXT_PATH      = BASE / "context.json"
INBOX_DIR         = BASE / "inbox"
IMPORTED_DIR      = BASE / "inbox" / "imported"

app = FastAPI(title="Reading Recommender", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
def startup():
    init_db()
    INBOX_DIR.mkdir(exist_ok=True)
    IMPORTED_DIR.mkdir(exist_ok=True)
    _import_inbox()        # pick up anything queued while server was off
    _export_context()      # write fresh context.json for the skill


# ── Frontend ──────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse((BASE / "index.html").read_text())


# ── Inbox sync ────────────────────────────────────────────────────────────────

def _import_inbox() -> dict:
    """Import all pending JSON files from inbox/ into the SQLite database."""
    files = sorted(INBOX_DIR.glob("*.json"))
    total_added = total_skipped = 0

    for f in files:
        try:
            payload = json.loads(f.read_text())
            run_date = payload.get("date") or str(date.today())
            articles = payload.get("articles", [])

            with get_conn() as conn:
                cur    = conn.execute("INSERT INTO runs (date) VALUES (?)", [run_date])
                run_id = cur.lastrowid

                for a in articles:
                    topics = a.get("topics", [])
                    topics_json = json.dumps(topics if isinstance(topics, list) else [topics])
                    tks = a.get("takeaways", "")
                    if isinstance(tks, list):
                        tks = "\n".join(f"• {t}" for t in tks)
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO articles
                               (run_id, title, url, source, summary, takeaways,
                                topics, category, date_recommended)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            [run_id, a.get("title","Untitled"), a.get("url",""),
                             a.get("source",""), a.get("summary",""), tks,
                             topics_json, a.get("category","regular"), run_date],
                        )
                        total_added += 1
                    except Exception:
                        total_skipped += 1

            shutil.move(str(f), str(IMPORTED_DIR / f.name))
        except Exception as e:
            print(f"  Failed to import {f.name}: {e}")

    return {"files": len(files), "added": total_added, "skipped": total_skipped}


def _export_context():
    """Write context.json so the Cowork skill can read preferences & history."""
    try:
        with get_conn() as conn:
            past_urls = [r[0] for r in conn.execute("SELECT url FROM articles").fetchall()]

            # Per-topic avg combined score (interest_rating weighted 60%, quality_rating 40%)
            # Falls back to legacy `rating` scaled to 1-7 range if new columns are NULL.
            topic_scores: dict = {}
            for row in conn.execute(
                """SELECT topics, quality_rating, interest_rating, rating
                   FROM articles WHERE topics IS NOT NULL
                     AND (quality_rating IS NOT NULL OR interest_rating IS NOT NULL OR rating IS NOT NULL)"""
            ).fetchall():
                q, i, r = row[1], row[2], row[3]
                if q is not None and i is not None:
                    score = q * 0.4 + i * 0.6          # 1-7 scale
                elif q is not None:
                    score = q
                elif i is not None:
                    score = i
                elif r is not None:
                    score = r * 7 / 5                   # map legacy 1-5 → 1-7
                else:
                    continue
                try:
                    for t in json.loads(row[0]):
                        topic_scores.setdefault(t, []).append(score)
                except Exception:
                    pass
            topic_avgs = {t: sum(v)/len(v) for t, v in topic_scores.items() if v}
            preferred_topics = sorted([t for t, a in topic_avgs.items() if a >= 4.0], key=lambda t: -topic_avgs[t])
            disliked_topics  = [t for t, a in topic_avgs.items() if a < 2.5]

            # Per-source avg combined score (≥ 2 rated articles)
            src_rows = conn.execute("""
                SELECT source,
                       AVG(CASE WHEN quality_rating IS NOT NULL AND interest_rating IS NOT NULL
                                THEN quality_rating * 0.4 + interest_rating * 0.6
                                WHEN quality_rating IS NOT NULL THEN quality_rating
                                WHEN interest_rating IS NOT NULL THEN interest_rating
                                ELSE rating * 7.0 / 5 END) avg_score,
                       COUNT(*) cnt
                FROM articles
                WHERE (quality_rating IS NOT NULL OR interest_rating IS NOT NULL OR rating IS NOT NULL)
                  AND source IS NOT NULL
                GROUP BY source HAVING cnt >= 2 ORDER BY avg_score DESC
            """).fetchall()
            preferred_sources = [r[0] for r in src_rows if r[1] >= 4.0]
            disliked_sources  = [r[0] for r in src_rows if r[1] < 2.5]

            # Qualitative signals — include both new rating columns
            rich_feedback = [dict(r) for r in conn.execute("""
                SELECT title, source, url, topics,
                       quality_rating, interest_rating, rating,
                       notes, date_recommended
                FROM articles
                WHERE (quality_rating IS NOT NULL OR interest_rating IS NOT NULL OR rating IS NOT NULL)
                  AND notes IS NOT NULL AND notes != ''
                ORDER BY created_at DESC
            """).fetchall()]
            top_rated_no_notes = [dict(r) for r in conn.execute("""
                SELECT title, source, topics, quality_rating, interest_rating, rating
                FROM articles
                WHERE (interest_rating >= 5 OR quality_rating >= 5 OR rating >= 4)
                  AND (notes IS NULL OR notes = '')
                ORDER BY COALESCE(interest_rating, 0) DESC, created_at DESC LIMIT 20
            """).fetchall()]
            low_rated = [dict(r) for r in conn.execute("""
                SELECT title, source, topics, quality_rating, interest_rating, rating, notes
                FROM articles
                WHERE (interest_rating <= 2 OR quality_rating <= 2 OR rating <= 2)
                ORDER BY created_at DESC LIMIT 20
            """).fetchall()]
            noted_unrated = [dict(r) for r in conn.execute("""
                SELECT title, source, topics, notes FROM articles
                WHERE quality_rating IS NULL AND interest_rating IS NULL AND rating IS NULL
                  AND notes IS NOT NULL AND notes != ''
                ORDER BY created_at DESC LIMIT 20
            """).fetchall()]

            total_articles = len(past_urls)
            total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            unincorporated = conn.execute("""
                SELECT COUNT(*) FROM articles
                WHERE (quality_rating IS NOT NULL OR interest_rating IS NOT NULL
                       OR rating IS NOT NULL
                       OR (notes IS NOT NULL AND notes != ''))
                  AND created_at > (
                    SELECT COALESCE(MAX(created_at), '1970-01-01') FROM constitution_history
                  )
            """).fetchone()[0]

        ctx = {
            "past_urls": past_urls,
            "constitution": CONSTITUTION_PATH.read_text().strip() if CONSTITUTION_PATH.exists() else "",
            "rich_feedback": rich_feedback,
            "top_rated_no_notes": top_rated_no_notes,
            "low_rated": low_rated,
            "noted_unrated": noted_unrated,
            "preferred_topics": preferred_topics,
            "disliked_topics": disliked_topics,
            "preferred_sources": preferred_sources,
            "disliked_sources": disliked_sources,
            "unincorporated_feedback": unincorporated,
            "total_articles": total_articles,
            "total_runs": total_runs,
        }
        CONTEXT_PATH.write_text(json.dumps(ctx, indent=2))
    except Exception as e:
        print(f"  context.json export failed: {e}")


@app.post("/api/sync")
def sync():
    """Import pending inbox files and refresh context.json."""
    result = _import_inbox()
    _export_context()
    return {**result, "context_updated": True}


@app.get("/api/inbox")
def inbox_status():
    pending  = len(list(INBOX_DIR.glob("*.json")))
    imported = len(list(IMPORTED_DIR.glob("*.json")))
    return {"pending": pending, "imported": imported}


# ── Articles ──────────────────────────────────────────────────────────────────

@app.get("/api/articles")
def list_articles(
    status: str = None, source: str = None, q: str = None,
    run_id: int = None, min_rating: int = None, unrated_only: bool = False,
    needs_enrichment: bool = False,
):
    conds, params = [], []
    if status:       conds.append("status = ?");       params.append(status)
    if source:       conds.append("source = ?");       params.append(source)
    if run_id:       conds.append("run_id = ?");       params.append(run_id)
    if min_rating:   conds.append("rating >= ?");      params.append(min_rating)
    if unrated_only: conds.append("rating IS NULL")
    if needs_enrichment:
        conds.append("(summary IS NULL OR summary = '' OR topics IS NULL OR topics = '[]' OR takeaways IS NULL OR takeaways = '')")
    if q:
        conds.append("(title LIKE ? OR summary LIKE ? OR notes LIKE ?)")
        params += [f"%{q}%"] * 3
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM articles {where} ORDER BY created_at DESC", params
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/articles/latest-run")
def latest_run():
    with get_conn() as conn:
        run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        if not run:
            return {"run": None, "articles": []}
        arts = conn.execute(
            "SELECT * FROM articles WHERE run_id = ? ORDER BY category DESC, id",
            [run["id"]],
        ).fetchall()
    return {"run": dict(run), "articles": [dict(a) for a in arts]}


@app.post("/api/articles")
async def create_article(request: Request):
    """Create a new article (e.g. from the browser extension). No run_id required."""
    body = await request.json()
    url = body.get("url", "").strip()
    title = body.get("title", "").strip()
    if not url:
        raise HTTPException(400, "url is required")
    if not title:
        raise HTTPException(400, "title is required")

    # summary, topics, and takeaways are left NULL — Claude enriches these
    # during the scheduled recommender task.

    with get_conn() as conn:
        # Check for duplicate URL
        existing = conn.execute("SELECT * FROM articles WHERE url = ?", [url]).fetchone()
        if existing:
            return JSONResponse(dict(existing), status_code=409)

        conn.execute(
            """INSERT INTO articles
               (title, url, source, summary, takeaways, topics, category,
                status, quality_rating, interest_rating, notes, date_recommended)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            [title, url, body.get("source", ""), None,
             None, None, body.get("category", "regular"),
             body.get("status", "unread"),
             body.get("quality_rating"), body.get("interest_rating"),
             body.get("notes"), body.get("date_recommended", str(date.today()))],
        )
        row = conn.execute("SELECT * FROM articles WHERE url = ?", [url]).fetchone()

    _export_context()
    return JSONResponse(dict(row), status_code=201)


@app.get("/api/articles/by-url")
def article_by_url(url: str):
    """Look up a single article by its URL."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM articles WHERE url = ?", [url]).fetchone()
    if not row:
        raise HTTPException(404, "Article not found")
    return dict(row)


@app.get("/api/sources")
def list_sources():
    """Return distinct source names for autocomplete."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT source FROM articles WHERE source IS NOT NULL AND source != '' ORDER BY source"
        ).fetchall()
    return [r[0] for r in rows]


@app.patch("/api/articles/{aid}")
async def update_article(aid: int, request: Request):
    body = await request.json()
    allowed = {"rating", "quality_rating", "interest_rating", "notes", "status", "date_read",
                "title", "source", "summary", "takeaways", "topics"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(400, "No valid fields")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE articles SET {set_clause} WHERE id = ?",
            list(updates.values()) + [aid],
        )
        row = conn.execute("SELECT * FROM articles WHERE id = ?", [aid]).fetchone()
    if not row:
        raise HTTPException(404)
    # Re-export context so skill sees fresh data on next run
    _export_context()
    return dict(row)


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    with get_conn() as conn:
        total   = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        read_n  = conn.execute("SELECT COUNT(*) FROM articles WHERE status='read'").fetchone()[0]
        rated_n = conn.execute(
            "SELECT COUNT(*) FROM articles WHERE quality_rating IS NOT NULL OR interest_rating IS NOT NULL"
        ).fetchone()[0]
        noted_n = conn.execute("SELECT COUNT(*) FROM articles WHERE notes IS NOT NULL AND notes != ''").fetchone()[0]
        avg_quality  = conn.execute("SELECT AVG(quality_rating)  FROM articles WHERE quality_rating  IS NOT NULL").fetchone()[0]
        avg_interest = conn.execute("SELECT AVG(interest_rating) FROM articles WHERE interest_rating IS NOT NULL").fetchone()[0]
        by_source = [dict(r) for r in conn.execute("""
            SELECT source, COUNT(*) cnt,
                   ROUND(AVG(quality_rating), 1)  avg_quality,
                   ROUND(AVG(interest_rating), 1) avg_interest
            FROM articles WHERE source IS NOT NULL GROUP BY source ORDER BY cnt DESC LIMIT 12
        """).fetchall()]
        by_status = [dict(r) for r in conn.execute(
            "SELECT status, COUNT(*) cnt FROM articles GROUP BY status"
        ).fetchall()]
        quality_dist = [dict(r) for r in conn.execute("""
            SELECT quality_rating rating, COUNT(*) cnt FROM articles
            WHERE quality_rating IS NOT NULL GROUP BY quality_rating ORDER BY quality_rating
        """).fetchall()]
        interest_dist = [dict(r) for r in conn.execute("""
            SELECT interest_rating rating, COUNT(*) cnt FROM articles
            WHERE interest_rating IS NOT NULL GROUP BY interest_rating ORDER BY interest_rating
        """).fetchall()]
        ratings_dist = quality_dist  # kept for legacy chart compat
        topic_counts: dict = {}
        for (raw,) in conn.execute("SELECT topics FROM articles WHERE topics IS NOT NULL").fetchall():
            try:
                for t in json.loads(raw): topic_counts[t] = topic_counts.get(t, 0) + 1
            except Exception: pass
        top_topics = sorted(topic_counts.items(), key=lambda x: -x[1])[:14]
        runs_hist = [dict(r) for r in conn.execute("""
            SELECT r.date, COUNT(a.id) cnt
            FROM runs r LEFT JOIN articles a ON a.run_id = r.id
            GROUP BY r.id ORDER BY r.date ASC LIMIT 60
        """).fetchall()]
        pending_inbox = len(list(INBOX_DIR.glob("*.json")))
    return {
        "total": total, "read": read_n, "rated": rated_n, "noted": noted_n,
        "avg_quality":  round(avg_quality,  2) if avg_quality  else None,
        "avg_interest": round(avg_interest, 2) if avg_interest else None,
        "by_source": by_source, "by_status": by_status,
        "ratings_dist": ratings_dist,
        "quality_dist": quality_dist,
        "interest_dist": interest_dist,
        "top_topics": [{"topic": t, "count": c} for t, c in top_topics],
        "runs_history": runs_hist,
        "pending_inbox": pending_inbox,
    }


# ── Runs ──────────────────────────────────────────────────────────────────────

@app.get("/api/runs")
def list_runs():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT r.*, COUNT(a.id) article_count
            FROM runs r LEFT JOIN articles a ON a.run_id = r.id
            GROUP BY r.id ORDER BY r.date DESC
        """).fetchall()
    return [dict(r) for r in rows]


# ── Constitution ──────────────────────────────────────────────────────────────

@app.get("/api/constitution")
def get_constitution():
    text = CONSTITUTION_PATH.read_text() if CONSTITUTION_PATH.exists() else ""
    with get_conn() as conn:
        history = [dict(r) for r in conn.execute("""
            SELECT id, summary, created_at FROM constitution_history
            ORDER BY id DESC LIMIT 10
        """).fetchall()]
    return {"content": text, "history": history}


@app.put("/api/constitution")
async def save_constitution(request: Request):
    body = await request.json()
    content = body.get("content", "")
    summary = body.get("summary", "")
    CONSTITUTION_PATH.write_text(content)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO constitution_history (content, summary) VALUES (?, ?)",
            [content, summary],
        )
    _export_context()
    return {"ok": True}


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n  📚 Recommender → http://localhost:{PORT}\n")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
