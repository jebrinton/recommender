"""
db.py — SQLite layer for the Reading Recommender
All tables defined here. Import get_conn() and init_db() everywhere else.
"""

import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent / "reading.db"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # safe concurrent reads + writes
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            -- Each time the recommender runs it creates one row here
            CREATE TABLE IF NOT EXISTS runs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT    NOT NULL,
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            -- Every recommended article
            CREATE TABLE IF NOT EXISTS articles (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id           INTEGER REFERENCES runs(id),
                title            TEXT    NOT NULL,
                url              TEXT    NOT NULL,
                source           TEXT,
                summary          TEXT,
                takeaways        TEXT,
                topics           TEXT,           -- JSON array: ["science","math"]
                category         TEXT    NOT NULL DEFAULT 'regular',  -- 'regular' | 'wildcard'
                status           TEXT    NOT NULL DEFAULT 'unread',   -- 'unread' | 'read' | 'skipped'
                rating           INTEGER,        -- legacy 1–5 (kept for backward compat)
                quality_rating   INTEGER,        -- 1–7: depth, conciseness, writing mechanics
                interest_rating  INTEGER,        -- 1–7: personal resonance / spark
                notes            TEXT,           -- free-form user commentary
                date_recommended TEXT,
                date_read        TEXT,
                created_at       TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(url)
            );

            -- Evolving taste constitution: each row is one saved version
            CREATE TABLE IF NOT EXISTS constitution_history (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT    NOT NULL,
                summary    TEXT,               -- one-sentence changelog
                created_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            -- Arbitrary key-value store for preferences / config
            CREATE TABLE IF NOT EXISTS preferences (
                key        TEXT    PRIMARY KEY,
                value      TEXT,
                updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_articles_status  ON articles(status);
            CREATE INDEX IF NOT EXISTS idx_articles_run     ON articles(run_id);
            CREATE INDEX IF NOT EXISTS idx_articles_rating  ON articles(rating);
        """)

        # Migrate existing databases: add new columns if they don't exist yet
        for col, typedef in [("quality_rating", "INTEGER"), ("interest_rating", "INTEGER")]:
            try:
                conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # column already exists
