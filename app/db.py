"""Database layer. SQLAlchemy Core so the same code works on:
  - SQLite (local dev, default)
  - Postgres (Render / any host that sets DATABASE_URL)

We deliberately use Core, not ORM — the schema is tiny (two tables) and
this keeps the surface trivial to reason about."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy import (Column, Integer, MetaData, String, Table, Text,
                        create_engine, func)
from sqlalchemy.engine import Engine

REPO = Path(__file__).resolve().parents[1]


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Render (and Heroku) hand out `postgres://…`. SQLAlchemy requires
        # `postgresql://` (or `postgresql+psycopg2://`). Normalise here.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg2://" + url[len("postgres://"):]
        elif url.startswith("postgresql://") and "+psycopg" not in url:
            url = "postgresql+psycopg2://" + url[len("postgresql://"):]
        return url
    # Local fallback: file-backed SQLite next to the repo.
    return f"sqlite:///{REPO / 'workflows.db'}"


metadata = MetaData()

workflows = Table(
    "workflows", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(200), nullable=False),
    Column("description", Text, nullable=True),
    Column("config_json", Text, nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("run_count", Integer, nullable=False, server_default="0"),
)

runs = Table(
    "runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("workflow_id", Integer, nullable=True),
    Column("started_at", Integer, nullable=True),
    Column("stats_json", Text, nullable=True),
)


_engine: Optional[Engine] = None


def engine() -> Engine:
    """Lazy singleton engine — created on first access."""
    global _engine
    if _engine is None:
        url = _resolve_url()
        # SQLite needs check_same_thread=False for the threaded workflow runner.
        connect_args = {"check_same_thread": False} if url.startswith("sqlite:") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True, pool_pre_ping=True)
    return _engine


def is_postgres() -> bool:
    return _resolve_url().startswith("postgresql")


def init_schema():
    metadata.create_all(engine())


def describe() -> dict:
    url = _resolve_url()
    # Never leak credentials — return only dialect + host summary.
    dialect = "postgres" if url.startswith("postgresql") else "sqlite"
    return {"dialect": dialect, "url_scheme": url.split("://", 1)[0]}
