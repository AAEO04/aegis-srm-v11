"""
AEGIS-SRM — SQLite Persistence Layer
Replaces Python-dict research_db with a proper queryable database.

Schema:
  ref_values   — one row per (category, name, parameter) with full provenance
  design_runs  — completed simulation results (immutable audit log)
  vv_results   — V&V gate outcomes per run

The Python-dict research_db is used as the canonical source of truth and
is migrated into SQLite on first use. After that, the DB is the live store
and can be queried, extended, and exported.

Usage:
    db = AEGISDatabase()          # opens/creates ~/.aegis/aegis.db
    db.migrate_from_research_db() # populate from Python dicts (idempotent)
    rows = db.query_propellant("APCP_HTPB")
    db.save_run(result)
    runs = db.list_runs()
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Default database path — can be overridden via env var AEGIS_DB_PATH
_DEFAULT_DB = Path(os.environ.get("AEGIS_DB_PATH",
                                   Path.home() / ".aegis" / "aegis.db"))


# ── Schema ─────────────────────────────────────────────────────────────────────

_DDL = """
-- Reference data: every entry from research_db
CREATE TABLE IF NOT EXISTS ref_values (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT    NOT NULL,   -- 'propellant' | 'material' | 'motor' | 'nozzle' | 'destination'
    name        TEXT    NOT NULL,   -- e.g. 'APCP_HTPB', 'CF_EPOXY'
    parameter   TEXT    NOT NULL,   -- e.g. 'isp_sl', 'yield_strength'
    value_json  TEXT    NOT NULL,   -- JSON-encoded value (number, string, or dict)
    unit        TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT '',
    confidence  REAL    NOT NULL DEFAULT 0.0,
    conditions  TEXT    NOT NULL DEFAULT '',
    notes       TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(category, name, parameter)
);

-- Design run index
CREATE TABLE IF NOT EXISTS design_runs (
    run_id      TEXT    PRIMARY KEY,
    mission     TEXT    NOT NULL,   -- JSON-encoded MissionIntent summary
    success     INTEGER NOT NULL,   -- 1 = pass, 0 = blocked
    blocked_by  TEXT,
    outputs     TEXT    NOT NULL DEFAULT '{}',  -- JSON
    parameters  TEXT    NOT NULL DEFAULT '{}',  -- JSON snapshot (68 params)
    audit_log   TEXT    NOT NULL DEFAULT '[]',  -- JSON
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- V&V gate outcomes per run
CREATE TABLE IF NOT EXISTS vv_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT    NOT NULL REFERENCES design_runs(run_id),
    gate_name   TEXT    NOT NULL,
    status      TEXT    NOT NULL,   -- 'pass' | 'fail'
    measured    REAL    NOT NULL,
    threshold   REAL    NOT NULL,
    blocks      INTEGER NOT NULL    -- 1 = hard gate
);

CREATE INDEX IF NOT EXISTS idx_ref_cat_name ON ref_values(category, name);
CREATE INDEX IF NOT EXISTS idx_runs_created ON design_runs(created_at);
CREATE INDEX IF NOT EXISTS idx_vv_run       ON vv_results(run_id);
"""


# ── Database class ──────────────────────────────────────────────────────────────

class AEGISDatabase:
    """
    SQLite-backed persistent store for AEGIS-SRM.
    Thread-safe for reads; uses WAL mode for concurrent writers.
    """

    def __init__(self, db_path: Path | str = _DEFAULT_DB):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Connection management ────────────────────────────────────────────────

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.path), timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript(_DDL)

    # ── Migration: Python dicts → SQLite ────────────────────────────────────

    def migrate_from_research_db(self, overwrite: bool = False) -> dict[str, int]:
        """
        Populate the database from the Python research_db module.
        Idempotent by default (skips existing rows unless overwrite=True).
        Returns counts of rows inserted per category.
        """
        from aegis_core.data.research_db import (
            PROPELLANT_DB, MATERIAL_DB, NOZZLE_MATERIAL_DB,
            REFERENCE_MOTORS, DESTINATION_DV_DB
        )

        sources = [
            ("propellant",  PROPELLANT_DB),
            ("material",    MATERIAL_DB),
            ("nozzle",      NOZZLE_MATERIAL_DB),
            ("motor",       REFERENCE_MOTORS),
        ]
        counts = {}

        with self._conn() as conn:
            for category, db in sources:
                inserted = 0
                for name, props in db.items():
                    for param, rv in props.items():
                        if not hasattr(rv, "value"):
                            continue
                        val_json = json.dumps(rv.value)
                        if overwrite:
                            conn.execute("""
                                INSERT OR REPLACE INTO ref_values
                                (category,name,parameter,value_json,unit,source,confidence,conditions,notes)
                                VALUES (?,?,?,?,?,?,?,?,?)
                            """, (category, name, param, val_json,
                                  rv.unit, rv.source, rv.confidence,
                                  rv.conditions, rv.notes))
                            inserted += 1
                        else:
                            cur = conn.execute("""
                                INSERT OR IGNORE INTO ref_values
                                (category,name,parameter,value_json,unit,source,confidence,conditions,notes)
                                VALUES (?,?,?,?,?,?,?,?,?)
                            """, (category, name, param, val_json,
                                  rv.unit, rv.source, rv.confidence,
                                  rv.conditions, rv.notes))
                            inserted += cur.rowcount
                counts[category] = inserted

            # Destinations (different structure)
            ins = 0
            for dest, data in DESTINATION_DV_DB.items():
                rv = data["dv"]
                if overwrite:
                    conn.execute("""
                        INSERT OR REPLACE INTO ref_values
                        (category,name,parameter,value_json,unit,source,confidence,conditions,notes)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, ("destination", dest, "dv",
                          json.dumps(rv.value), rv.unit, rv.source,
                          rv.confidence, rv.conditions, rv.notes))
                    ins += 1
                else:
                    cur = conn.execute("""
                        INSERT OR IGNORE INTO ref_values
                        (category,name,parameter,value_json,unit,source,confidence,conditions,notes)
                        VALUES (?,?,?,?,?,?,?,?,?)
                    """, ("destination", dest, "dv",
                          json.dumps(rv.value), rv.unit, rv.source,
                          rv.confidence, rv.conditions, rv.notes))
                    ins += cur.rowcount
            counts["destination"] = ins

        return counts

    # ── Query reference data ─────────────────────────────────────────────────

    def query_propellant(self, name: str) -> dict[str, Any]:
        """Return all parameters for a named propellant as a plain dict."""
        return self._query_entry("propellant", name)

    def query_material(self, name: str) -> dict[str, Any]:
        return self._query_entry("material", name)

    def query_motor(self, name: str) -> dict[str, Any]:
        return self._query_entry("motor", name)

    def _query_entry(self, category: str, name: str) -> dict[str, Any]:
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT parameter, value_json, unit, source, confidence, conditions, notes
                FROM ref_values WHERE category=? AND name=?
                ORDER BY parameter
            """, (category, name.upper())).fetchall()
        if not rows:
            raise KeyError(f"No {category} entry named '{name}'")
        return {
            row["parameter"]: {
                "value":      json.loads(row["value_json"]),
                "unit":       row["unit"],
                "source":     row["source"],
                "confidence": row["confidence"],
                "conditions": row["conditions"],
                "notes":      row["notes"],
            }
            for row in rows
        }

    def list_names(self, category: str) -> list[str]:
        """List all unique names in a category."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT name FROM ref_values WHERE category=?
                ORDER BY name
            """, (category,)).fetchall()
        return [r["name"] for r in rows]

    def search(self, query: str, category: Optional[str] = None) -> list[dict]:
        """
        Full-text search across source, notes, and parameter names.
        Returns list of matching (category, name, parameter, value, source) rows.
        """
        q = f"%{query}%"
        cat_filter = "AND category=?" if category else ""
        args = [q, q, q] + ([category] if category else [])
        with self._conn() as conn:
            rows = conn.execute(f"""
                SELECT category, name, parameter, value_json, unit, source, confidence
                FROM ref_values
                WHERE (source LIKE ? OR notes LIKE ? OR parameter LIKE ?)
                {cat_filter}
                ORDER BY category, name, parameter
                LIMIT 100
            """, args).fetchall()
        return [dict(r) for r in rows]

    # ── Design run storage ───────────────────────────────────────────────────

    def save_run(self, result, mission_summary: str = "") -> str:
        """
        Persist a SimulationResult to the database.
        Returns the run_id.
        """
        run_id = result.run_id

        # Encode V&V gates
        vv_rows = []
        if result.vv_report:
            for gate in result.vv_report.gates:
                vv_rows.append({
                    "run_id":    run_id,
                    "gate_name": gate.name,
                    "status":    gate.status.value,
                    "measured":  float(gate.measured),
                    "threshold": float(gate.threshold),
                    "blocks":    int(gate.blocks_simulation),
                })

        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO design_runs
                (run_id, mission, success, blocked_by, outputs, parameters, audit_log)
                VALUES (?,?,?,?,?,?,?)
            """, (
                run_id,
                mission_summary or run_id,
                int(result.success),
                result.blocked_by,
                json.dumps(result.outputs or {}),
                json.dumps({k: v.get("value") for k,v in (result.parameter_snapshot or {}).items()}),
                json.dumps(result.audit_log or []),
            ))
            for vv in vv_rows:
                conn.execute("""
                    INSERT OR REPLACE INTO vv_results
                    (run_id,gate_name,status,measured,threshold,blocks)
                    VALUES (?,?,?,?,?,?)
                """, (vv["run_id"], vv["gate_name"], vv["status"],
                      vv["measured"], vv["threshold"], vv["blocks"]))

        return run_id

    def get_run(self, run_id: str) -> Optional[dict]:
        """Retrieve a saved run by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM design_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if not row:
                return None
            vv = conn.execute(
                "SELECT * FROM vv_results WHERE run_id=?", (run_id,)
            ).fetchall()
        result = dict(row)
        result["outputs"]    = json.loads(result["outputs"])
        result["parameters"] = json.loads(result["parameters"])
        result["audit_log"]  = json.loads(result["audit_log"])
        result["vv_gates"]   = [dict(g) for g in vv]
        return result

    def list_runs(self, limit: int = 50) -> list[dict]:
        """List recent design runs (most recent first)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT run_id, mission, success, blocked_by, created_at
                FROM design_runs
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def run_stats(self) -> dict:
        """Summary statistics across all stored runs."""
        with self._conn() as conn:
            total   = conn.execute("SELECT COUNT(*) FROM design_runs").fetchone()[0]
            passed  = conn.execute("SELECT COUNT(*) FROM design_runs WHERE success=1").fetchone()[0]
            n_ref   = conn.execute("SELECT COUNT(*) FROM ref_values").fetchone()[0]
            n_gates = conn.execute("SELECT COUNT(*) FROM vv_results").fetchone()[0]
        return {
            "total_runs":     total,
            "passed_runs":    passed,
            "blocked_runs":   total - passed,
            "ref_data_rows":  n_ref,
            "vv_gate_evals":  n_gates,
            "db_path":        str(self.path),
            "db_size_kb":     self.path.stat().st_size // 1024 if self.path.exists() else 0,
        }

    def export_csv(self, category: str, output_path: str):
        """Export a category to CSV for external analysis."""
        import csv
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT name, parameter, value_json, unit, source, confidence
                FROM ref_values WHERE category=?
                ORDER BY name, parameter
            """, (category,)).fetchall()
        with open(output_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["name","parameter","value","unit","source","confidence"])
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "name":       row["name"],
                    "parameter":  row["parameter"],
                    "value":      json.loads(row["value_json"]),
                    "unit":       row["unit"],
                    "source":     row["source"],
                    "confidence": row["confidence"],
                })
        return output_path


# ── Module-level singleton ────────────────────────────────────────────────────

_DB: Optional[AEGISDatabase] = None

def get_db(db_path: Optional[str] = None) -> AEGISDatabase:
    """Get or create the module-level database singleton."""
    global _DB
    if _DB is None or db_path is not None:
        _DB = AEGISDatabase(db_path or _DEFAULT_DB)
    return _DB


def ensure_migrated(db_path: Optional[str] = None) -> AEGISDatabase:
    """Get the database and migrate reference data if needed."""
    db = get_db(db_path)
    stats = db.run_stats()
    if stats["ref_data_rows"] == 0:
        db.migrate_from_research_db()
    return db
