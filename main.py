from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("GROUND_TRUTH_DB", "/data/ground-truth.db"))
SEED_PATH = Path(os.getenv("GROUND_TRUTH_SEED", "/app/ground_truth_seed.json"))

app = FastAPI(title="ScanSnap Ground Truth", version="1.1.0")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS canonical_folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_folder_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    parent_drive_folder_id TEXT,
    path TEXT NOT NULL,
    category TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    canonical_drive_folder_id TEXT,
    person TEXT,
    notes TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(canonical_name, entity_type, person)
);

CREATE TABLE IF NOT EXISTS aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL DEFAULT 'name',
    weight REAL NOT NULL DEFAULT 1.0,
    UNIQUE(entity_id, alias, alias_type)
);

CREATE TABLE IF NOT EXISTS contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    contract_id TEXT NOT NULL,
    person TEXT,
    notes TEXT,
    UNIQUE(entity_id, contract_id)
);

CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    signal TEXT NOT NULL,
    signal_type TEXT NOT NULL DEFAULT 'text',
    weight REAL NOT NULL DEFAULT 1.0,
    UNIQUE(entity_id, signal, signal_type)
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ground_truth_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_file_id TEXT UNIQUE,
    file_name TEXT,
    sender TEXT,
    provider TEXT,
    person TEXT,
    document_type TEXT,
    contract_id TEXT,
    context TEXT,
    correct_drive_folder_id TEXT NOT NULL,
    correct_path TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'confirmed',
    source TEXT NOT NULL DEFAULT 'manual_cleanup',
    confirmed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_file_id TEXT,
    file_name TEXT,
    proposed_drive_folder_id TEXT,
    proposed_path TEXT,
    confidence REAL,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_aliases_alias ON aliases(alias);
CREATE INDEX IF NOT EXISTS idx_contracts_contract_id ON contracts(contract_id);
CREATE INDEX IF NOT EXISTS idx_signals_signal ON signals(signal);
CREATE INDEX IF NOT EXISTS idx_cases_sender ON ground_truth_cases(sender);
CREATE INDEX IF NOT EXISTS idx_cases_provider ON ground_truth_cases(provider);
"""


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        # Seed is idempotent (INSERT OR IGNORE). Run it on every startup so
        # schema/catalog additions are also applied to an already existing DB volume.
        if SEED_PATH.exists():
            seed_database(conn, json.loads(SEED_PATH.read_text(encoding="utf-8")))


def seed_database(conn: sqlite3.Connection, seed: dict[str, Any]) -> None:
    ts = now_iso()

    for f in seed.get("canonical_folders", []):
        conn.execute(
            """INSERT OR IGNORE INTO canonical_folders
               (drive_folder_id,name,parent_drive_folder_id,path,category,active,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                f["drive_folder_id"], f["name"], f.get("parent_drive_folder_id"),
                f["path"], f.get("category"), 1, ts, ts
            ),
        )

    entity_ids: dict[str, int] = {}
    for e in seed.get("entities", []):
        cur = conn.execute(
            """INSERT OR IGNORE INTO entities
               (canonical_name,entity_type,canonical_drive_folder_id,person,notes,active,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                e["canonical_name"], e["entity_type"], e.get("canonical_drive_folder_id"),
                e.get("person"), e.get("notes"), 1, ts, ts
            ),
        )
        row = conn.execute(
            "SELECT id FROM entities WHERE canonical_name=? AND entity_type=? AND person IS ?",
            (e["canonical_name"], e["entity_type"], e.get("person")),
        ).fetchone()
        entity_ids[e["key"]] = row["id"]

        for a in e.get("aliases", []):
            conn.execute(
                "INSERT OR IGNORE INTO aliases(entity_id,alias,alias_type,weight) VALUES (?,?,?,?)",
                (row["id"], a["value"], a.get("type", "name"), a.get("weight", 1.0)),
            )
        for c in e.get("contracts", []):
            conn.execute(
                "INSERT OR IGNORE INTO contracts(entity_id,contract_id,person,notes) VALUES (?,?,?,?)",
                (row["id"], c["contract_id"], c.get("person"), c.get("notes")),
            )
        for s in e.get("signals", []):
            conn.execute(
                "INSERT OR IGNORE INTO signals(entity_id,signal,signal_type,weight) VALUES (?,?,?,?)",
                (row["id"], s["value"], s.get("type", "text"), s.get("weight", 1.0)),
            )

    for r in seed.get("rules", []):
        conn.execute(
            """INSERT OR IGNORE INTO rules(rule_key,description,priority,active,created_at,updated_at)
               VALUES (?,?,?,?,?,?)""",
            (r["rule_key"], r["description"], r.get("priority", 100), 1, ts, ts),
        )

    for c in seed.get("ground_truth_cases", []):
        conn.execute(
            """INSERT OR IGNORE INTO ground_truth_cases
               (drive_file_id,file_name,sender,provider,person,document_type,contract_id,context,
                correct_drive_folder_id,correct_path,reason,status,source,confirmed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                c.get("drive_file_id"), c.get("file_name"), c.get("sender"), c.get("provider"),
                c.get("person"), c.get("document_type"), c.get("contract_id"), c.get("context"),
                c["correct_drive_folder_id"], c["correct_path"], c["reason"],
                c.get("status", "confirmed"), c.get("source", "manual_cleanup"),
                c.get("confirmed_at", ts),
            ),
        )


def norm(s: str | None) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", s.casefold()).strip()


class ContextRequest(BaseModel):
    text: str = Field(min_length=1)
    file_name: str | None = None



class RegisterFolderRequest(BaseModel):
    drive_folder_id: str
    name: str
    path: str
    parent_drive_folder_id: str | None = None
    category: str | None = None
    reason: str = "manual_folder_approval"

class ConfirmRequest(BaseModel):
    drive_file_id: str
    file_name: str | None = None
    sender: str | None = None
    provider: str | None = None
    person: str | None = None
    document_type: str | None = None
    contract_id: str | None = None
    context: str | None = None
    correct_drive_folder_id: str
    correct_path: str
    reason: str
    source: str = "manual_confirmation"


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, Any]:
    with db() as conn:
        return {
            "status": "ok",
            "service": "ground-truth",
            "version": "1.1.0",
            "db": str(DB_PATH),
            "folders": conn.execute("SELECT COUNT(*) n FROM canonical_folders WHERE active=1").fetchone()["n"],
            "entities": conn.execute("SELECT COUNT(*) n FROM entities WHERE active=1").fetchone()["n"],
            "cases": conn.execute("SELECT COUNT(*) n FROM ground_truth_cases WHERE status IN ('confirmed','corrected')").fetchone()["n"],
        }


@app.post("/context")
def context(req: ContextRequest) -> dict[str, Any]:
    haystack = norm((req.file_name or "") + "\n" + req.text)
    matches: list[dict[str, Any]] = []

    with db() as conn:
        entities = conn.execute(
            """SELECT e.*, f.path AS folder_path, f.name AS folder_name
               FROM entities e
               LEFT JOIN canonical_folders f ON f.drive_folder_id=e.canonical_drive_folder_id
               WHERE e.active=1"""
        ).fetchall()

        for e in entities:
            score = 0.0
            evidence: list[str] = []

            aliases = conn.execute("SELECT * FROM aliases WHERE entity_id=?", (e["id"],)).fetchall()
            for a in aliases:
                if norm(a["alias"]) and norm(a["alias"]) in haystack:
                    score += 0.30 * float(a["weight"])
                    evidence.append(f"Alias: {a['alias']}")

            contracts = conn.execute("SELECT * FROM contracts WHERE entity_id=?", (e["id"],)).fetchall()
            for c in contracts:
                if norm(c["contract_id"]) and norm(c["contract_id"]) in haystack:
                    score += 0.55
                    evidence.append(f"Vertrags-ID: {c['contract_id']}")

            signals = conn.execute("SELECT * FROM signals WHERE entity_id=?", (e["id"],)).fetchall()
            for s in signals:
                if norm(s["signal"]) and norm(s["signal"]) in haystack:
                    score += 0.18 * float(s["weight"])
                    evidence.append(f"Signal: {s['signal']}")

            if score > 0:
                matches.append({
                    "entity": e["canonical_name"],
                    "entity_type": e["entity_type"],
                    "person": e["person"],
                    "drive_folder_id": e["canonical_drive_folder_id"],
                    "folder_path": e["folder_path"],
                    "score": round(min(score, 1.0), 3),
                    "evidence": evidence[:8],
                })

        matches.sort(key=lambda x: x["score"], reverse=True)

        rules = [
            dict(r) for r in conn.execute(
                "SELECT rule_key,description,priority FROM rules WHERE active=1 ORDER BY priority ASC"
            ).fetchall()
        ]

        cases = [
            dict(r) for r in conn.execute(
                """SELECT sender,provider,person,document_type,contract_id,context,
                          correct_drive_folder_id,correct_path,reason
                   FROM ground_truth_cases
                   WHERE status IN ('confirmed','corrected')
                   ORDER BY id DESC LIMIT 50"""
            ).fetchall()
        ]

        canonical_folders = [
            dict(r) for r in conn.execute(
                """SELECT drive_folder_id,name,parent_drive_folder_id,path,category
                   FROM canonical_folders
                   WHERE active=1
                   ORDER BY path"""
            ).fetchall()
        ]

    return {
        "ground_truth_version": "1.1.0",
        "matches": matches[:8],
        "canonical_folders": canonical_folders,
        "rules": rules,
        "confirmed_cases": cases,
        "instruction": (
            "Use Ground Truth as evidence, not as permission to guess. "
            "Sender and filing provider are separate concepts. "
            "Never create or invent Drive folders automatically. "
            "If no existing canonical/live folder is sufficiently supported, request human review. "
            "A new folder becomes Ground Truth only after explicit user approval, Drive creation, /register-folder, and /confirm."
        ),
    }



@app.post("/register-folder")
def register_folder(req: RegisterFolderRequest) -> dict[str, Any]:
    """Register only a folder that the user has explicitly approved/created in Drive."""
    ts = now_iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO canonical_folders
               (drive_folder_id,name,parent_drive_folder_id,path,category,active,created_at,updated_at)
               VALUES (?,?,?,?,?,1,?,?)
               ON CONFLICT(drive_folder_id) DO UPDATE SET
                 name=excluded.name,
                 parent_drive_folder_id=excluded.parent_drive_folder_id,
                 path=excluded.path,
                 category=COALESCE(excluded.category,canonical_folders.category),
                 active=1,
                 updated_at=excluded.updated_at""",
            (req.drive_folder_id, req.name, req.parent_drive_folder_id, req.path,
             req.category, ts, ts),
        )
    return {
        "success": True,
        "drive_folder_id": req.drive_folder_id,
        "path": req.path,
        "message": "User-approved folder registered as canonical Ground Truth."
    }


@app.post("/confirm")
def confirm(req: ConfirmRequest) -> dict[str, Any]:
    with db() as conn:
        folder = conn.execute(
            "SELECT path FROM canonical_folders WHERE drive_folder_id=? AND active=1",
            (req.correct_drive_folder_id,),
        ).fetchone()
        if not folder:
            raise HTTPException(status_code=400, detail="Unknown canonical Drive folder ID.")

        conn.execute(
            """INSERT INTO ground_truth_cases
               (drive_file_id,file_name,sender,provider,person,document_type,contract_id,context,
                correct_drive_folder_id,correct_path,reason,status,source,confirmed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(drive_file_id) DO UPDATE SET
                 file_name=excluded.file_name,
                 sender=excluded.sender,
                 provider=excluded.provider,
                 person=excluded.person,
                 document_type=excluded.document_type,
                 contract_id=excluded.contract_id,
                 context=excluded.context,
                 correct_drive_folder_id=excluded.correct_drive_folder_id,
                 correct_path=excluded.correct_path,
                 reason=excluded.reason,
                 status='corrected',
                 source=excluded.source,
                 confirmed_at=excluded.confirmed_at""",
            (
                req.drive_file_id, req.file_name, req.sender, req.provider, req.person,
                req.document_type, req.contract_id, req.context,
                req.correct_drive_folder_id, req.correct_path, req.reason,
                "confirmed", req.source, now_iso(),
            ),
        )
    return {"success": True, "drive_file_id": req.drive_file_id}


@app.get("/export")
def export() -> dict[str, Any]:
    with db() as conn:
        return {
            "version": "1.1.0",
            "exported_at": now_iso(),
            "canonical_folders": [dict(x) for x in conn.execute("SELECT * FROM canonical_folders ORDER BY path")],
            "entities": [dict(x) for x in conn.execute("SELECT * FROM entities ORDER BY canonical_name")],
            "aliases": [dict(x) for x in conn.execute("SELECT * FROM aliases ORDER BY entity_id,alias")],
            "contracts": [dict(x) for x in conn.execute("SELECT * FROM contracts ORDER BY entity_id,contract_id")],
            "signals": [dict(x) for x in conn.execute("SELECT * FROM signals ORDER BY entity_id,signal")],
            "rules": [dict(x) for x in conn.execute("SELECT * FROM rules ORDER BY priority,rule_key")],
            "ground_truth_cases": [dict(x) for x in conn.execute("SELECT * FROM ground_truth_cases ORDER BY id")],
        }
