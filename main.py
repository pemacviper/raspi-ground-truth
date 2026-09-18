from __future__ import annotations

import json
import os
import re
import sqlite3
import math
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv("GROUND_TRUTH_DB", "/data/ground-truth.db"))
SEED_PATH = Path(os.getenv("GROUND_TRUTH_SEED", "/app/ground_truth_seed.json"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "512"))
AWS_REGION = os.getenv("AWS_REGION", "eu-central-1")
VERSION_PATH = Path(os.getenv("GROUND_TRUTH_VERSION_FILE", "/app/VERSION"))
APP_VERSION = VERSION_PATH.read_text(encoding="utf-8").strip() if VERSION_PATH.exists() else "dev"

app = FastAPI(title="ScanSnap Ground Truth", version=APP_VERSION)


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


CREATE TABLE IF NOT EXISTS case_embeddings (
    case_id INTEGER PRIMARY KEY REFERENCES ground_truth_cases(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    embedding_json TEXT NOT NULL,
    embedded_text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_file_id TEXT NOT NULL,
    file_name TEXT,
    analysis_json TEXT NOT NULL,
    live_folders_json TEXT,
    suggested_parent_id TEXT,
    suggested_parent_path TEXT,
    suggested_folder_name TEXT,
    confidence REAL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    resolution_type TEXT,
    selected_drive_folder_id TEXT,
    selected_path TEXT,
    approved_new_folder_name TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT,
    processed_at TEXT,
    error TEXT,
    UNIQUE(drive_file_id, status)
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);

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


def ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """Add a column to an existing SQLite table when upgrading a persistent DB."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def migrate_db(conn: sqlite3.Connection) -> None:
    """Forward-only, idempotent migrations for databases created by older releases."""
    ensure_column(conn, "review_queue", "resolution_type", "TEXT")
    ensure_column(conn, "review_queue", "selected_drive_folder_id", "TEXT")
    ensure_column(conn, "review_queue", "selected_path", "TEXT")
    ensure_column(conn, "review_queue", "approved_new_folder_name", "TEXT")
    ensure_column(conn, "review_queue", "decided_at", "TEXT")
    ensure_column(conn, "review_queue", "processed_at", "TEXT")
    ensure_column(conn, "review_queue", "error", "TEXT")


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        migrate_db(conn)
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



def case_embedding_text(row: sqlite3.Row | dict[str, Any]) -> str:
    def g(key: str) -> str:
        try:
            value = row[key]
        except (KeyError, IndexError):
            value = None
        return str(value or "")
    return "\n".join([
        f"Datei: {g('file_name')}",
        f"Absender: {g('sender')}",
        f"Leistungserbringer: {g('provider')}",
        f"Person: {g('person')}",
        f"Dokumenttyp: {g('document_type')}",
        f"Vertrags-ID: {g('contract_id')}",
        f"Kontext: {g('context')}",
        f"Bestätigtes Ziel: {g('correct_path')}",
    ]).strip()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return -1.0
    dot = sum(x*y for x,y in zip(a,b))
    na = math.sqrt(sum(x*x for x in a))
    nb = math.sqrt(sum(y*y for y in b))
    return dot/(na*nb) if na and nb else -1.0


def semantic_matches(conn: sqlite3.Connection, query: list[float] | None, top_k: int) -> list[dict[str, Any]]:
    if not query:
        return []
    rows = conn.execute("""SELECT c.*, e.model_id, e.dimensions, e.embedding_json
                           FROM case_embeddings e
                           JOIN ground_truth_cases c ON c.id=e.case_id
                           WHERE c.status IN ('confirmed','corrected')""").fetchall()
    hits=[]
    for r in rows:
        vec=json.loads(r["embedding_json"])
        score=cosine_similarity(query, vec)
        if score >= 0:
            hits.append({"score": round(score,4), "drive_file_id":r["drive_file_id"],
                         "file_name":r["file_name"], "sender":r["sender"], "provider":r["provider"],
                         "person":r["person"], "document_type":r["document_type"], "context":r["context"],
                         "correct_drive_folder_id":r["correct_drive_folder_id"], "correct_path":r["correct_path"],
                         "reason":r["reason"], "model_id":r["model_id"]})
    hits.sort(key=lambda x:x["score"], reverse=True)
    return hits[:max(1,min(top_k,20))]

class ContextRequest(BaseModel):
    text: str = Field(min_length=1)
    file_name: str | None = None
    embedding: list[float] | None = None
    semantic_top_k: int = 5




class EmbeddingUpsertRequest(BaseModel):
    drive_file_id: str
    embedding: list[float]
    embedded_text: str | None = None
    model_id: str = EMBEDDING_MODEL

class ReviewCreateRequest(BaseModel):
    drive_file_id: str
    file_name: str | None = None
    analysis: dict[str, Any]
    live_folders: list[dict[str, Any]] = []
    suggested_parent_id: str | None = None
    suggested_parent_path: str | None = None
    suggested_folder_name: str | None = None
    confidence: float | None = None
    reason: str

class ReviewCompleteRequest(BaseModel):
    success: bool = True
    error: str | None = None

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
            "version": APP_VERSION,
            "db": str(DB_PATH),
            "folders": conn.execute("SELECT COUNT(*) n FROM canonical_folders WHERE active=1").fetchone()["n"],
            "entities": conn.execute("SELECT COUNT(*) n FROM entities WHERE active=1").fetchone()["n"],
            "cases": conn.execute("SELECT COUNT(*) n FROM ground_truth_cases WHERE status IN ('confirmed','corrected')").fetchone()["n"],
            "pending_reviews": conn.execute("SELECT COUNT(*) n FROM review_queue WHERE status='pending'").fetchone()["n"],
            "embeddings": conn.execute("SELECT COUNT(*) n FROM case_embeddings").fetchone()["n"],
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": EMBEDDING_DIMENSIONS,
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
        sem = semantic_matches(conn, req.embedding, req.semantic_top_k)

    return {
        "ground_truth_version": APP_VERSION,
        "matches": matches[:8],
        "semantic_matches": sem,
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





@app.post("/embeddings/upsert")
def upsert_embedding(req: EmbeddingUpsertRequest) -> dict[str, Any]:
    with db() as conn:
        case=conn.execute("SELECT * FROM ground_truth_cases WHERE drive_file_id=?", (req.drive_file_id,)).fetchone()
        if not case:
            raise HTTPException(404, "Confirmed Ground Truth case not found")
        if len(req.embedding) not in (256,512,1024,1536):
            raise HTTPException(400, "Unexpected embedding dimensions")
        text=req.embedded_text or case_embedding_text(case)
        conn.execute("""INSERT INTO case_embeddings(case_id,model_id,dimensions,embedding_json,embedded_text,updated_at)
                        VALUES (?,?,?,?,?,?)
                        ON CONFLICT(case_id) DO UPDATE SET model_id=excluded.model_id,
                        dimensions=excluded.dimensions,embedding_json=excluded.embedding_json,
                        embedded_text=excluded.embedded_text,updated_at=excluded.updated_at""",
                     (case["id"],req.model_id,len(req.embedding),json.dumps(req.embedding),text,now_iso()))
    return {"success":True,"drive_file_id":req.drive_file_id,"dimensions":len(req.embedding),"model_id":req.model_id}


@app.get("/embeddings/missing")
def missing_embeddings() -> dict[str, Any]:
    with db() as conn:
        rows=conn.execute("""SELECT c.* FROM ground_truth_cases c
                             LEFT JOIN case_embeddings e ON e.case_id=c.id
                             WHERE c.status IN ('confirmed','corrected') AND e.case_id IS NULL
                             ORDER BY c.id""").fetchall()
    return {"model_id":EMBEDDING_MODEL,"dimensions":EMBEDDING_DIMENSIONS,
            "cases":[{"drive_file_id":r["drive_file_id"],"text":case_embedding_text(r)} for r in rows]}

@app.post("/review")
def create_review(req: ReviewCreateRequest) -> dict[str, Any]:
    ts = now_iso()
    with db() as conn:
        existing = conn.execute(
            "SELECT id,status FROM review_queue WHERE drive_file_id=? AND status IN ('pending','approved_existing','approved_new') ORDER BY id DESC LIMIT 1",
            (req.drive_file_id,),
        ).fetchone()
        if existing:
            return {"success": True, "review_id": existing["id"], "status": existing["status"], "deduplicated": True}
        cur = conn.execute(
            """INSERT INTO review_queue
               (drive_file_id,file_name,analysis_json,live_folders_json,suggested_parent_id,
                suggested_parent_path,suggested_folder_name,confidence,reason,status,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'pending',?)""",
            (req.drive_file_id, req.file_name, json.dumps(req.analysis, ensure_ascii=False),
             json.dumps(req.live_folders, ensure_ascii=False), req.suggested_parent_id,
             req.suggested_parent_path, req.suggested_folder_name, req.confidence, req.reason, ts),
        )
        rid = cur.lastrowid
    return {"success": True, "review_id": rid, "status": "pending",
            "review_url": f"http://192.168.1.115:8011/reviews/{rid}"}


@app.get("/reviews")
def reviews(status: str = "pending") -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status=? ORDER BY created_at ASC", (status,)
        ).fetchall()
    return {"reviews": [dict(r) for r in rows]}


@app.get("/reviews/approved")
def approved_reviews() -> dict[str, Any]:
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM review_queue WHERE status IN ('approved_existing','approved_new') ORDER BY decided_at ASC"
        ).fetchall()
    return {"reviews": [dict(r) for r in rows]}


def page_shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title>
<style>
:root{{--bg:#f4f6f8;--card:#fff;--text:#18212b;--muted:#667085;--line:#e4e7ec;--accent:#175cd3;--ok:#067647;--warn:#b54708}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,-apple-system,sans-serif}}
main{{max-width:980px;margin:32px auto;padding:0 18px}} .top{{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:20px}}
h1{{font-size:25px;margin:0}} h2{{font-size:18px;margin:0 0 14px}} .version{{color:var(--muted);font-size:13px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;margin:14px 0;box-shadow:0 1px 2px #1018280d}}
.grid{{display:grid;grid-template-columns:160px 1fr;gap:8px 16px}} .label{{color:var(--muted)}} .value{{font-weight:550;overflow-wrap:anywhere}}
.badge{{display:inline-block;padding:3px 9px;border-radius:999px;background:#ecfdf3;color:var(--ok);font-weight:650}}
.badge.warn{{background:#fffaeb;color:var(--warn)}} select,input{{width:100%;padding:11px;border:1px solid #d0d5dd;border-radius:8px;font:inherit}}
button{{border:0;border-radius:8px;padding:11px 15px;font:inherit;font-weight:650;cursor:pointer;background:var(--accent);color:white}}
button.secondary{{background:#475467}} button.warning{{background:#b54708}} details{{margin-top:12px}} summary{{cursor:pointer;color:var(--accent);font-weight:600}}
pre{{white-space:pre-wrap;word-break:break-word;background:#101828;color:#eaecf0;padding:16px;border-radius:10px;overflow:auto;font-size:12px}}
.actions{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .success{{border-left:5px solid var(--ok)}} .hint{{color:var(--muted);margin:5px 0 0}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}.actions{{grid-template-columns:1fr}}}}
</style></head><body><main><div class="top"><h1>{escape(title)}</h1><span class="version">Ground Truth {escape(APP_VERSION)}</span></div>{body}</main></body></html>"""


def analysis_summary(analysis: dict[str, Any]) -> str:
    rows = [
        ("Dokument", analysis.get("neuer_name") or analysis.get("original_file_name")),
        ("Absender", analysis.get("absender") or analysis.get("sender")),
        ("Leistungserbringer", analysis.get("leistungserbringer") or analysis.get("firma")),
        ("Person", analysis.get("person")),
        ("Dokumenttyp", analysis.get("dokumenttyp") or analysis.get("document_type")),
        ("Datum", analysis.get("datum")),
        ("Betreff", analysis.get("betreff")),
        ("Kontext", analysis.get("kontext")),
    ]
    return "".join(
        f'<div class="label">{escape(label)}</div><div class="value">{escape(str(value))}</div>'
        for label, value in rows if value not in (None, "")
    )


@app.get("/reviews/{review_id}", response_class=HTMLResponse)
def review_page(review_id: int) -> str:
    with db() as conn:
        r = conn.execute("SELECT * FROM review_queue WHERE id=?", (review_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Review not found")
        canonical = conn.execute(
            "SELECT drive_folder_id,path FROM canonical_folders WHERE active=1 ORDER BY path"
        ).fetchall()

    analysis = json.loads(r["analysis_json"] or "{}")
    live = json.loads(r["live_folders_json"] or "[]")
    options: dict[str, str] = {}
    for folder in canonical:
        options[folder["drive_folder_id"]] = folder["path"]
    for folder in live:
        fid = folder.get("id")
        if fid:
            options[fid] = folder.get("path") or (
                (r["suggested_parent_path"] + " / " if r["suggested_parent_path"] else "") +
                (folder.get("name") or folder.get("title") or fid)
            )
    option_html = "".join(
        f'<option value="{escape(fid, quote=True)}">{escape(path)}</option>'
        for fid, path in sorted(options.items(), key=lambda x: x[1].casefold())
    )

    if r["status"] != "pending":
        body = f"""<section class="card success">
        <span class="badge">{escape(r["status"])}</span>
        <h2>Review bereits entschieden</h2>
        <div class="grid"><div class="label">Datei</div><div class="value">{escape(r["file_name"] or "")}</div>
        <div class="label">Ziel</div><div class="value">{escape(r["selected_path"] or r["approved_new_folder_name"] or "—")}</div></div>
        <p class="hint">Die Entscheidung liegt in der Review Queue und wird von n8n verarbeitet.</p></section>"""
        return page_shell(f"ScanSnap Review #{review_id}", body)

    confidence = "—" if r["confidence"] is None else f'{float(r["confidence"])*100:.0f} %'
    suggested = " / ".join(x for x in [r["suggested_parent_path"], r["suggested_folder_name"]] if x) or "Kein eindeutiger Vorschlag"
    evidence = analysis.get("evidence") or []
    evidence_html = "".join(f"<li>{escape(str(x))}</li>" for x in evidence)
    body = f"""
    <section class="card">
      <span class="badge warn">Manuelle Prüfung</span>
      <h2>{escape(analysis.get("betreff") or analysis.get("dokumenttyp") or "Dokument prüfen")}</h2>
      <div class="grid">{analysis_summary(analysis)}</div>
    </section>
    <section class="card">
      <h2>Warum ist eine Entscheidung nötig?</h2>
      <div class="grid">
        <div class="label">Grund</div><div class="value">{escape(r["reason"] or "—")}</div>
        <div class="label">Modell-Sicherheit</div><div class="value">{escape(confidence)}</div>
        <div class="label">Vorgeschlagener Kontext</div><div class="value">{escape(suggested)}</div>
      </div>
      {f'<h3>Evidenz</h3><ul>{evidence_html}</ul>' if evidence_html else ''}
      <details><summary>Technische Analyse anzeigen</summary><pre>{escape(json.dumps(analysis, ensure_ascii=False, indent=2))}</pre></details>
    </section>
    <form method="post" action="/reviews/{review_id}/resolve">
      <div class="actions">
        <section class="card"><h2>Bestehenden Ordner verwenden</h2>
          <p class="hint">Bevorzugte Option, wenn der passende Ablageort bereits existiert.</p>
          <select name="existing_folder_id"><option value="">Ordner auswählen…</option>{option_html}</select><br><br>
          <button name="action" value="existing">Zuordnung bestätigen</button>
        </section>
        <section class="card"><h2>Neuen Ordner freigeben</h2>
          <p class="hint">Nur verwenden, wenn wirklich eine neue Kategorie benötigt wird.</p>
          <div class="label">Übergeordneter Ordner</div><div class="value">{escape(r["suggested_parent_path"] or "nicht erkannt")}</div><br>
          <input name="new_folder_name" value="{escape(r["suggested_folder_name"] or "", quote=True)}" placeholder="Neuer Ordnername"><br><br>
          <button class="warning" name="action" value="new">Neuen Ordner freigeben</button>
        </section>
      </div>
      <section class="card"><button class="secondary" name="action" value="defer">Später entscheiden</button></section>
    </form>"""
    return page_shell(f"ScanSnap Review #{review_id}", body)


@app.post("/reviews/{review_id}/resolve", response_class=HTMLResponse)
def resolve_review(review_id: int, action: str = Form(...),
                   existing_folder_id: str = Form(""), new_folder_name: str = Form("")) -> str:
    ts = now_iso()
    with db() as conn:
        r = conn.execute("SELECT * FROM review_queue WHERE id=?", (review_id,)).fetchone()
        if not r:
            raise HTTPException(404, "Review not found")
        if r["status"] != "pending":
            return page_shell(f"Review #{review_id}", f'<section class="card"><h2>Bereits entschieden</h2><p>Status: <span class="badge">{escape(r["status"])}</span></p></section>')
        if action == "defer":
            return page_shell(f"Review #{review_id}", '<section class="card"><h2>Keine Änderung</h2><p>Das Dokument bleibt zur Prüfung vorgemerkt. Du kannst diese Seite später erneut öffnen.</p></section>')
        if action == "existing":
            if not existing_folder_id:
                raise HTTPException(400, "No existing folder selected")
            folder = conn.execute("SELECT path FROM canonical_folders WHERE drive_folder_id=? AND active=1", (existing_folder_id,)).fetchone()
            path = folder["path"] if folder else None
            if not path:
                live = json.loads(r["live_folders_json"] or "[]")
                hit = next((x for x in live if x.get("id") == existing_folder_id), None)
                if not hit:
                    raise HTTPException(400, "Selected folder was not offered by this review")
                path = (r["suggested_parent_path"] + " / " if r["suggested_parent_path"] else "") + (hit.get("name") or hit.get("title") or existing_folder_id)
            conn.execute("""UPDATE review_queue SET status='approved_existing',resolution_type='existing',
                         selected_drive_folder_id=?,selected_path=?,decided_at=? WHERE id=?""",
                         (existing_folder_id, path, ts, review_id))
            body = f"""<section class="card success"><span class="badge">Bestätigt</span>
            <h2>Zuordnung gespeichert</h2><div class="grid">
            <div class="label">Dokument</div><div class="value">{escape(r["file_name"] or "")}</div>
            <div class="label">Zielordner</div><div class="value">{escape(path)}</div></div>
            <p class="hint">n8n übernimmt die Datei beim nächsten Review-Lauf. Danach wird die bestätigte Zuordnung in Ground Truth gespeichert und kann künftig als Präzedenzfall dienen.</p></section>"""
            return page_shell("Zuordnung bestätigt", body)
        if action == "new":
            name = re.sub(r'[\\/:*?"<>|]+', '-', new_folder_name).strip()
            if not name or not r["suggested_parent_id"]:
                raise HTTPException(400, "New folder needs a name and an approved parent")
            conn.execute("""UPDATE review_queue SET status='approved_new',resolution_type='new',
                         approved_new_folder_name=?,decided_at=? WHERE id=?""", (name, ts, review_id))
            path = f'{r["suggested_parent_path"] or ""} / {name}'.strip(" /")
            body = f"""<section class="card success"><span class="badge">Freigegeben</span>
            <h2>Neuer Ordner wird angelegt</h2><div class="grid">
            <div class="label">Neuer Ablageort</div><div class="value">{escape(path)}</div></div>
            <p class="hint">n8n legt den Ordner beim nächsten Review-Lauf in Google Drive an, registriert seine echte Drive-ID in Ground Truth und verschiebt anschließend das Dokument.</p></section>"""
            return page_shell("Ordnerfreigabe gespeichert", body)
        raise HTTPException(400, "Unknown action")


@app.post("/reviews/{review_id}/complete")
def complete_review(review_id: int, req: ReviewCompleteRequest) -> dict[str, Any]:
    with db() as conn:
        r=conn.execute("SELECT status FROM review_queue WHERE id=?", (review_id,)).fetchone()
        if not r: raise HTTPException(404, "Review not found")
        conn.execute("UPDATE review_queue SET status=?,processed_at=?,error=? WHERE id=?",
                     ("resolved" if req.success else "error", now_iso(), req.error, review_id))
    return {"success": True, "review_id": review_id, "status": "resolved" if req.success else "error"}

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
            "version": APP_VERSION,
            "exported_at": now_iso(),
            "canonical_folders": [dict(x) for x in conn.execute("SELECT * FROM canonical_folders ORDER BY path")],
            "entities": [dict(x) for x in conn.execute("SELECT * FROM entities ORDER BY canonical_name")],
            "aliases": [dict(x) for x in conn.execute("SELECT * FROM aliases ORDER BY entity_id,alias")],
            "contracts": [dict(x) for x in conn.execute("SELECT * FROM contracts ORDER BY entity_id,contract_id")],
            "signals": [dict(x) for x in conn.execute("SELECT * FROM signals ORDER BY entity_id,signal")],
            "rules": [dict(x) for x in conn.execute("SELECT * FROM rules ORDER BY priority,rule_key")],
            "ground_truth_cases": [dict(x) for x in conn.execute("SELECT * FROM ground_truth_cases ORDER BY id")],
            "review_queue": [dict(x) for x in conn.execute("SELECT * FROM review_queue ORDER BY id")],
            "case_embeddings": [dict(x) for x in conn.execute("SELECT case_id,model_id,dimensions,embedded_text,updated_at FROM case_embeddings ORDER BY case_id")],
        }
