"""Small additive migration registry for BDHub-Agent-owned SQLite databases.

The project historically let each feature create its own tables on import.  That is safe for an
isolated prototype, but it cannot prove that an existing production-shaped database is ready for a
new reader.  This registry is deliberately narrow: only the two databases touched by the current
V1 alignment live here, every migration is additive, and checking never creates a file or table.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
from pathlib import Path
import sqlite3
import time


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self):
        return hashlib.sha256(self.sql.strip().encode("utf-8")).hexdigest()


CATALOG_LINKS = Migration(1, "canonical_catalog_binding_v1", """
CREATE TABLE IF NOT EXISTS catalog_current_binding(
  market TEXT NOT NULL,
  catalog_source TEXT NOT NULL,
  pid TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  offer_fingerprint TEXT NOT NULL,
  list_id TEXT NOT NULL,
  commission_rule_version TEXT NOT NULL,
  naming_rule_version TEXT NOT NULL,
  creator_percent TEXT NOT NULL,
  list_name TEXT NOT NULL,
  card_payload TEXT NOT NULL,
  link_intent_id TEXT,
  state TEXT NOT NULL,
  verified_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(market,catalog_source,pid,campaign_id)
);
CREATE INDEX IF NOT EXISTS catalog_current_binding_state
  ON catalog_current_binding(state,market,catalog_source,pid);
CREATE TABLE IF NOT EXISTS catalog_current_binding_event(
  id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  catalog_source TEXT NOT NULL,
  pid TEXT NOT NULL,
  campaign_id TEXT NOT NULL,
  state TEXT NOT NULL,
  payload TEXT NOT NULL,
  observed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS catalog_current_binding_event_key
  ON catalog_current_binding_event(market,catalog_source,pid,campaign_id,observed_at);
""")


SECOND_CYCLE = Migration(1, "current_lead_selection_v1", """
CREATE TABLE IF NOT EXISTS source_edge_index(
  plan_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  source_handle TEXT NOT NULL,
  source_rank INTEGER NOT NULL,
  units INTEGER NOT NULL,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  source_kind TEXT NOT NULL,
  PRIMARY KEY(plan_id,source_id)
);
CREATE INDEX IF NOT EXISTS source_edge_index_current
  ON source_edge_index(plan_id,pid,source_rank,units,source_handle);
CREATE TABLE IF NOT EXISTS lead_query_run(
  query_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  state TEXT NOT NULL,
  selected_count INTEGER NOT NULL,
  receipt_fingerprint TEXT NOT NULL,
  published_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS lead_query_run_pid
  ON lead_query_run(plan_id,pid,published_at);
CREATE TABLE IF NOT EXISTS lead_query_selection(
  query_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_rank INTEGER NOT NULL,
  units INTEGER NOT NULL,
  position INTEGER NOT NULL,
  PRIMARY KEY(query_id,source_id),
  UNIQUE(query_id,position)
);
CREATE TABLE IF NOT EXISTS lead_query_head(
  plan_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  query_id TEXT NOT NULL,
  PRIMARY KEY(plan_id,pid)
);
CREATE INDEX IF NOT EXISTS lead_query_head_query ON lead_query_head(query_id);
""")

SECOND_CYCLE_INDEXES = Migration(2, "current_lead_selection_indexes_v1", """
CREATE INDEX IF NOT EXISTS lead_query_selection_source
  ON lead_query_selection(source_id,query_id);
CREATE INDEX IF NOT EXISTS source_edge_index_handle
  ON source_edge_index(plan_id,source_handle,pid,source_rank,units);
""")


SECOND_CYCLE_FROZEN_SEND = Migration(3, "frozen_send_batch_v1", """
CREATE TABLE IF NOT EXISTS cycle_bulk(
  id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  target INTEGER NOT NULL,
  authorization TEXT NOT NULL,
  state TEXT NOT NULL,
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cycle_bulk_item(
  batch_id TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  state TEXT NOT NULL,
  delivery_id TEXT,
  reason TEXT,
  retry_at REAL NOT NULL DEFAULT 0,
  retry_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(batch_id,creator_id)
);
CREATE TABLE IF NOT EXISTS cycle_bulk_runtime(
  batch_id TEXT PRIMARY KEY,
  pid INTEGER,
  seen REAL,
  phase TEXT
);
CREATE TABLE IF NOT EXISTS cycle_bulk_timing(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id TEXT,
  stage TEXT,
  seconds REAL,
  exit_code INTEGER,
  at REAL
);
CREATE TABLE IF NOT EXISTS cycle_bulk_freeze(
  batch_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  preview_hash TEXT NOT NULL,
  config_json TEXT NOT NULL,
  authorization_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL,
  authorized_at REAL,
  stop_requested_at REAL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS cycle_bulk_candidate(
  batch_id TEXT NOT NULL,
  position_order INTEGER NOT NULL,
  creator_id TEXT NOT NULL,
  oec TEXT NOT NULL,
  pid TEXT NOT NULL,
  source_id TEXT NOT NULL,
  offer_key TEXT NOT NULL,
  offer_fingerprint TEXT NOT NULL,
  current_list_id TEXT NOT NULL,
  candidate_json TEXT NOT NULL,
  candidate_hash TEXT NOT NULL,
  PRIMARY KEY(batch_id,creator_id),
  UNIQUE(batch_id,position_order)
);
CREATE INDEX IF NOT EXISTS cycle_bulk_freeze_state
  ON cycle_bulk_freeze(state,created_at);
CREATE INDEX IF NOT EXISTS cycle_bulk_candidate_order
  ON cycle_bulk_candidate(batch_id,position_order);
CREATE TRIGGER IF NOT EXISTS cycle_bulk_freeze_scope_immutable
BEFORE UPDATE ON cycle_bulk_freeze
WHEN OLD.batch_id != NEW.batch_id OR OLD.request_id != NEW.request_id
  OR OLD.preview_hash != NEW.preview_hash OR OLD.config_json != NEW.config_json
  OR OLD.authorization_json != NEW.authorization_json OR OLD.created_at != NEW.created_at
BEGIN SELECT RAISE(ABORT,'frozen batch scope is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cycle_bulk_candidate_no_update
BEFORE UPDATE ON cycle_bulk_candidate
BEGIN SELECT RAISE(ABORT,'frozen candidate is immutable'); END;
CREATE TRIGGER IF NOT EXISTS cycle_bulk_candidate_no_delete
BEFORE DELETE ON cycle_bulk_candidate
BEGIN SELECT RAISE(ABORT,'frozen candidate is immutable'); END;
""")


SECOND_CYCLE_REPLY_EVENTS = Migration(4, "reply_event_ledger_v1", """
CREATE TABLE IF NOT EXISTS outbound_episode(
  episode_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  oec TEXT NOT NULL,
  delivery_id TEXT NOT NULL UNIQUE,
  pid TEXT NOT NULL,
  offer_key TEXT NOT NULL,
  list_id TEXT NOT NULL,
  sent_at REAL NOT NULL,
  payload_json TEXT NOT NULL,
  snapshot_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS outbound_episode_creator_time
  ON outbound_episode(plan_id,creator_id,sent_at);
CREATE TABLE IF NOT EXISTS inbound_turn(
  turn_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  oec TEXT NOT NULL,
  cid TEXT NOT NULL,
  message_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  format TEXT NOT NULL,
  text TEXT,
  occurred_ms INTEGER,
  historical INTEGER NOT NULL,
  observed_at REAL NOT NULL,
  UNIQUE(plan_id,cid,message_id,content_hash)
);
CREATE INDEX IF NOT EXISTS inbound_turn_creator_time
  ON inbound_turn(plan_id,creator_id,occurred_ms,message_id);
CREATE TABLE IF NOT EXISTS turn_episode_link(
  turn_id TEXT NOT NULL,
  episode_id TEXT NOT NULL,
  candidate_rank INTEGER NOT NULL,
  evidence TEXT NOT NULL,
  confidence TEXT NOT NULL,
  PRIMARY KEY(turn_id,episode_id),
  UNIQUE(turn_id,candidate_rank)
);
CREATE TABLE IF NOT EXISTS service_case_turn(
  case_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  PRIMARY KEY(case_id,turn_id)
);
CREATE TABLE IF NOT EXISTS reply_classification(
  classification_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  input_hash TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  state TEXT NOT NULL,
  input_json TEXT NOT NULL,
  response_json TEXT,
  decision_json TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS reply_classification_input
  ON reply_classification(input_hash,policy_version,provider,state);
CREATE TABLE IF NOT EXISTS reply_review(
  classification_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  verdict TEXT NOT NULL,
  correct_action TEXT,
  note TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(classification_id,revision)
);
CREATE TRIGGER IF NOT EXISTS outbound_episode_no_update
BEFORE UPDATE ON outbound_episode BEGIN SELECT RAISE(ABORT,'outbound episode is immutable'); END;
CREATE TRIGGER IF NOT EXISTS outbound_episode_no_delete
BEFORE DELETE ON outbound_episode BEGIN SELECT RAISE(ABORT,'outbound episode is immutable'); END;
CREATE TRIGGER IF NOT EXISTS inbound_turn_no_update
BEFORE UPDATE ON inbound_turn BEGIN SELECT RAISE(ABORT,'inbound turn is immutable'); END;
CREATE TRIGGER IF NOT EXISTS inbound_turn_no_delete
BEFORE DELETE ON inbound_turn BEGIN SELECT RAISE(ABORT,'inbound turn is immutable'); END;
CREATE TRIGGER IF NOT EXISTS turn_episode_link_no_update
BEFORE UPDATE ON turn_episode_link BEGIN SELECT RAISE(ABORT,'turn episode link is immutable'); END;
CREATE TRIGGER IF NOT EXISTS turn_episode_link_no_delete
BEFORE DELETE ON turn_episode_link BEGIN SELECT RAISE(ABORT,'turn episode link is immutable'); END;
""")


SECOND_CYCLE_TURN_REVIEW = Migration(5, "reply_turn_review_v1", """
CREATE TABLE IF NOT EXISTS turn_review(
  turn_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  correct_action TEXT NOT NULL,
  note TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(turn_id,revision)
);
CREATE INDEX IF NOT EXISTS turn_review_latest ON turn_review(turn_id,revision DESC);
CREATE TRIGGER IF NOT EXISTS turn_review_no_update
BEFORE UPDATE ON turn_review BEGIN SELECT RAISE(ABORT,'turn review is append only'); END;
CREATE TRIGGER IF NOT EXISTS turn_review_no_delete
BEFORE DELETE ON turn_review BEGIN SELECT RAISE(ABORT,'turn review is append only'); END;
""")


SECOND_CYCLE_REVIEW_APPLICATION = Migration(6, "reply_review_application_v1", """
CREATE TABLE IF NOT EXISTS turn_review_application(
  application_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  request_json TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  review_revision INTEGER NOT NULL,
  action TEXT NOT NULL,
  plan_id TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  expected_control_revision INTEGER NOT NULL,
  expected_pending_revision INTEGER NOT NULL,
  state TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS turn_review_application_turn
  ON turn_review_application(turn_id,review_revision);
CREATE TABLE IF NOT EXISTS review_reply_candidate(
  candidate_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  review_revision INTEGER NOT NULL,
  plan_id TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  action TEXT NOT NULL,
  template_key TEXT NOT NULL,
  template_text TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(turn_id,review_revision)
);
CREATE TRIGGER IF NOT EXISTS turn_review_application_no_update
BEFORE UPDATE ON turn_review_application BEGIN SELECT RAISE(ABORT,'review application is immutable'); END;
CREATE TRIGGER IF NOT EXISTS turn_review_application_no_delete
BEFORE DELETE ON turn_review_application BEGIN SELECT RAISE(ABORT,'review application is immutable'); END;
""")


SECOND_CYCLE_VIDEO_EVIDENCE = Migration(7, "kalodata_video_evidence_v1", """
CREATE TABLE IF NOT EXISTS kalodata_video_run(
  run_id TEXT PRIMARY KEY,
  pid TEXT NOT NULL,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  min_views INTEGER NOT NULL,
  max_videos INTEGER NOT NULL,
  list_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL,
  rows_received INTEGER NOT NULL,
  qualifying_videos INTEGER NOT NULL,
  resolved_videos INTEGER NOT NULL,
  network_requests INTEGER NOT NULL,
  observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kalodata_video_evidence(
  run_id TEXT NOT NULL,
  video_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  kalodata_creator_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  views INTEGER NOT NULL,
  sale INTEGER NOT NULL,
  revenue_raw TEXT,
  release_time TEXT,
  duration TEXT,
  description TEXT NOT NULL,
  video_url TEXT NOT NULL,
  content_type TEXT,
  is_ad INTEGER NOT NULL,
  is_ai INTEGER NOT NULL,
  payload_hash TEXT NOT NULL,
  observed_at REAL NOT NULL,
  PRIMARY KEY(run_id,video_id)
);
CREATE TABLE IF NOT EXISTS kalodata_video_head(
  pid TEXT PRIMARY KEY,
  run_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS kalodata_video_run_pid_time
  ON kalodata_video_run(pid,observed_at);
CREATE INDEX IF NOT EXISTS kalodata_video_evidence_pid_views
  ON kalodata_video_evidence(pid,views DESC);
CREATE INDEX IF NOT EXISTS kalodata_video_evidence_handle
  ON kalodata_video_evidence(handle,pid);
CREATE TRIGGER IF NOT EXISTS kalodata_video_run_no_update
BEFORE UPDATE ON kalodata_video_run BEGIN SELECT RAISE(ABORT,'video run is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kalodata_video_run_no_delete
BEFORE DELETE ON kalodata_video_run BEGIN SELECT RAISE(ABORT,'video run is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kalodata_video_evidence_no_update
BEFORE UPDATE ON kalodata_video_evidence BEGIN SELECT RAISE(ABORT,'video evidence is immutable'); END;
CREATE TRIGGER IF NOT EXISTS kalodata_video_evidence_no_delete
BEFORE DELETE ON kalodata_video_evidence BEGIN SELECT RAISE(ABORT,'video evidence is immutable'); END;
""")


SECOND_CYCLE_VIDEO_PAGING = Migration(8, "kalodata_video_paging_v1", """
ALTER TABLE kalodata_video_run ADD COLUMN sort_field TEXT NOT NULL DEFAULT 'views';
ALTER TABLE kalodata_video_run ADD COLUMN max_pages INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kalodata_video_run ADD COLUMN pages_read INTEGER NOT NULL DEFAULT 1;
ALTER TABLE kalodata_video_run ADD COLUMN selected_videos INTEGER;
ALTER TABLE kalodata_video_run ADD COLUMN coverage TEXT NOT NULL DEFAULT 'legacy_top_cap';
""")


DATABASES = {
    "catalog-links": ("catalog-links.sqlite", (CATALOG_LINKS,)),
    "second-cycle": ("second-cycle.sqlite", (SECOND_CYCLE, SECOND_CYCLE_INDEXES,
                                                SECOND_CYCLE_FROZEN_SEND,
                                                SECOND_CYCLE_REPLY_EVENTS,
                                                SECOND_CYCLE_TURN_REVIEW,
                                                SECOND_CYCLE_REVIEW_APPLICATION,
                                                SECOND_CYCLE_VIDEO_EVIDENCE,
                                                SECOND_CYCLE_VIDEO_PAGING)),
}

REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS agent_schema_migration(
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at REAL NOT NULL
)
"""


def _path(root, key):
    if key not in DATABASES:
        raise ValueError("migration_database_invalid")
    return Path(root) / "var" / DATABASES[key][0]


def _applied(path):
    if not path.exists():
        return {}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_schema_migration'").fetchone():
            return {}
        return {row[0]: {"name": row[1], "checksum": row[2], "appliedAt": row[3]}
                for row in db.execute("SELECT version,name,checksum,applied_at FROM agent_schema_migration")}


def check_database(root, key):
    path = _path(root, key)
    migrations = DATABASES[key][1]
    applied = _applied(path)
    known = {migration.version: migration for migration in migrations}
    if set(applied) - set(known):
        raise ValueError("migration_unknown_version")
    for version, row in applied.items():
        migration = known[version]
        if row["name"] != migration.name or row["checksum"] != migration.checksum:
            raise ValueError("migration_checksum_mismatch")
    pending = [{"version": migration.version, "name": migration.name,
                "checksum": migration.checksum} for migration in migrations if migration.version not in applied]
    return {"database": key, "path": str(path), "exists": path.exists(),
            "ready": path.exists() and not pending,
            "applied": [{"version": version, **applied[version]} for version in sorted(applied)],
            "pending": pending}


def _statements(sql):
    # `sqlite3.complete_statement` keeps trigger bodies together.  Splitting on every semicolon would
    # cut ``BEGIN ...; END`` in half and make an otherwise additive migration impossible to replay.
    statements = []
    pending = ""
    for line in sql.splitlines():
        pending += line + "\n"
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""
    if pending.strip():
        raise ValueError("migration_sql_incomplete")
    return statements


def apply_database(root, key, *, clock=time.time):
    path = _path(root, key)
    if not path.exists():
        raise ValueError("migration_database_missing")
    migrations = DATABASES[key][1]
    before = check_database(root, key)
    applied_now = []
    with closing(sqlite3.connect(path, timeout=30, isolation_level=None)) as db:
        db.row_factory = sqlite3.Row
        for migration in migrations:
            if any(row["version"] == migration.version for row in before["applied"]):
                continue
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(REGISTRY_SQL)
                for statement in _statements(migration.sql):
                    db.execute(statement)
                db.execute("INSERT INTO agent_schema_migration(version,name,checksum,applied_at) VALUES(?,?,?,?)",
                           (migration.version, migration.name, migration.checksum, clock()))
                db.execute("COMMIT")
                applied_now.append({"version": migration.version, "name": migration.name})
            except BaseException:
                db.execute("ROLLBACK")
                raise
    return check_database(root, key) | {"appliedNow": applied_now}


def check_all(root, keys=None):
    selected = tuple(keys or DATABASES)
    states = [check_database(root, key) for key in selected]
    return {"ready": all(state["ready"] for state in states), "databases": states}


def apply_all(root, keys=None, *, clock=time.time):
    selected = tuple(keys or DATABASES)
    states = [apply_database(root, key, clock=clock) for key in selected]
    return {"ready": all(state["ready"] for state in states), "databases": states}
