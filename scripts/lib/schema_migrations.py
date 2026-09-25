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


SECOND_CYCLE_AB_LEADS = Migration(9, "ab_leads_and_video_scan_v1", """
ALTER TABLE source_edge_index ADD COLUMN revenue_value TEXT;
ALTER TABLE source_edge_index ADD COLUMN revenue_currency TEXT;
CREATE INDEX IF NOT EXISTS source_edge_index_revenue
  ON source_edge_index(plan_id,pid,revenue_value,units,source_handle);
CREATE TABLE IF NOT EXISTS kalodata_video_generation(
  generation_id TEXT PRIMARY KEY,
  window_start TEXT NOT NULL,
  window_end TEXT NOT NULL,
  min_views INTEGER NOT NULL,
  state TEXT NOT NULL,
  scope_fingerprint TEXT NOT NULL,
  scope_count INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS kalodata_video_scan_job(
  generation_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  priority_units INTEGER NOT NULL,
  state TEXT NOT NULL,
  next_page INTEGER NOT NULL,
  list_done INTEGER NOT NULL,
  list_requests INTEGER NOT NULL,
  detail_requests INTEGER NOT NULL,
  error TEXT,
  updated_at REAL NOT NULL,
  PRIMARY KEY(generation_id,pid)
);
CREATE INDEX IF NOT EXISTS kalodata_video_scan_job_queue
  ON kalodata_video_scan_job(generation_id,state,priority_units DESC,updated_at,pid);
CREATE TABLE IF NOT EXISTS kalodata_video_scan_page(
  generation_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  page_no INTEGER NOT NULL,
  rows_received INTEGER NOT NULL,
  rows_fingerprint TEXT NOT NULL,
  newest_release_date TEXT,
  oldest_release_date TEXT,
  reached_window_start INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  observed_at REAL NOT NULL,
  PRIMARY KEY(generation_id,pid,page_no)
);
CREATE TABLE IF NOT EXISTS kalodata_video_scan_item(
  generation_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  video_id TEXT NOT NULL,
  source_rank INTEGER NOT NULL,
  views INTEGER NOT NULL,
  sale INTEGER NOT NULL,
  revenue_raw TEXT,
  release_time TEXT NOT NULL,
  duration TEXT,
  description TEXT NOT NULL,
  content_type TEXT,
  is_ad INTEGER NOT NULL,
  is_ai INTEGER NOT NULL,
  list_payload_hash TEXT NOT NULL,
  detail_state TEXT NOT NULL,
  kalodata_creator_id TEXT,
  handle TEXT,
  detail_payload_hash TEXT,
  video_url TEXT,
  observed_at REAL NOT NULL,
  PRIMARY KEY(generation_id,pid,video_id)
);
CREATE INDEX IF NOT EXISTS kalodata_video_scan_item_pending
  ON kalodata_video_scan_item(generation_id,pid,detail_state,source_rank);
CREATE TABLE IF NOT EXISTS kalodata_video_author_cache(
  video_id TEXT PRIMARY KEY,
  kalodata_creator_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  video_url TEXT NOT NULL,
  observed_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS video_lead_current(
  generation_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  kalodata_creator_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  run_id TEXT NOT NULL,
  video_id TEXT NOT NULL,
  views INTEGER NOT NULL,
  released_at TEXT NOT NULL,
  video_sale INTEGER NOT NULL,
  observed_at REAL NOT NULL,
  PRIMARY KEY(pid,kalodata_creator_id)
);
CREATE INDEX IF NOT EXISTS video_lead_current_order
  ON video_lead_current(views DESC,released_at DESC,pid,kalodata_creator_id);
""")


SECOND_CYCLE_CONVERSATION_WORKBENCH = Migration(10, "conversation_workbench_and_templates_v1", """
CREATE TABLE IF NOT EXISTS send_message_template(
  template_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  current_revision INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS send_message_template_revision(
  template_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  body_it TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(template_id,revision)
);
CREATE TRIGGER IF NOT EXISTS send_message_template_revision_no_update
BEFORE UPDATE ON send_message_template_revision BEGIN SELECT RAISE(ABORT,'send template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS send_message_template_revision_no_delete
BEFORE DELETE ON send_message_template_revision BEGIN SELECT RAISE(ABORT,'send template revision is immutable'); END;
CREATE TABLE IF NOT EXISTS manual_reply_template(
  template_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  state TEXT NOT NULL,
  current_revision INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_reply_template_revision(
  template_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(template_id,revision)
);
CREATE TRIGGER IF NOT EXISTS manual_reply_template_revision_no_update
BEFORE UPDATE ON manual_reply_template_revision BEGIN SELECT RAISE(ABORT,'manual template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS manual_reply_template_revision_no_delete
BEFORE DELETE ON manual_reply_template_revision BEGIN SELECT RAISE(ABORT,'manual template revision is immutable'); END;
CREATE TABLE IF NOT EXISTS conversation_draft(
  plan_id TEXT NOT NULL,
  cid TEXT NOT NULL,
  text TEXT NOT NULL,
  revision INTEGER NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(plan_id,cid)
);
CREATE TABLE IF NOT EXISTS agent_reply_setting(
  plan_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL,
  timezone TEXT NOT NULL,
  reply_start TEXT NOT NULL,
  reply_end TEXT NOT NULL,
  send_start TEXT NOT NULL,
  send_end TEXT NOT NULL,
  buffer_minutes INTEGER NOT NULL,
  actions_json TEXT NOT NULL,
  revision INTEGER NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_reply_run(
  run_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  state TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL,
  claimed INTEGER NOT NULL,
  no_reply INTEGER NOT NULL,
  prepared INTEGER NOT NULL,
  human INTEGER NOT NULL,
  confirmed INTEGER NOT NULL,
  unknown INTEGER NOT NULL,
  error TEXT
);
CREATE INDEX IF NOT EXISTS agent_reply_run_plan_time ON agent_reply_run(plan_id,started_at DESC);
""")


SECOND_CYCLE_AUTOMATED_WORKFLOW = Migration(11, "automated_operations_workflow_v1", """
CREATE TABLE IF NOT EXISTS market_automation_setting(
  market TEXT PRIMARY KEY,
  automatic_operations_enabled INTEGER NOT NULL DEFAULT 0,
  full_catalog_weekly_enabled INTEGER NOT NULL DEFAULT 0,
  continuous_send_enabled INTEGER NOT NULL DEFAULT 0,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS market_automation_request(
  request_id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  expected_revision INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  result_revision INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_run(
  run_id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  trigger_source TEXT NOT NULL,
  scheduled_at REAL NOT NULL,
  applicable_sources_json TEXT NOT NULL,
  config_revision INTEGER NOT NULL,
  state TEXT NOT NULL,
  started_at REAL NOT NULL,
  finished_at REAL,
  stop_requested_at REAL,
  error_code TEXT
);
CREATE INDEX IF NOT EXISTS workflow_run_market_time
  ON workflow_run(market,started_at DESC);
CREATE INDEX IF NOT EXISTS workflow_run_active
  ON workflow_run(market,state,started_at DESC);
CREATE TABLE IF NOT EXISTS workflow_stage_run(
  stage_run_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  position INTEGER NOT NULL,
  state TEXT NOT NULL,
  input_generation_id TEXT,
  output_generation_id TEXT,
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  counts_json TEXT NOT NULL DEFAULT '{}',
  platform_writes INTEGER NOT NULL DEFAULT 0,
  started_at REAL,
  finished_at REAL,
  error_code TEXT,
  UNIQUE(run_id,stage)
);
CREATE INDEX IF NOT EXISTS workflow_stage_run_queue
  ON workflow_stage_run(run_id,position,state);
CREATE TABLE IF NOT EXISTS workflow_generation(
  generation_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  market TEXT NOT NULL,
  scope_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL,
  item_count INTEGER NOT NULL,
  payload_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  published_at REAL
);
CREATE INDEX IF NOT EXISTS workflow_generation_current
  ON workflow_generation(market,stage,state,published_at DESC);
CREATE TABLE IF NOT EXISTS workflow_checkpoint(
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  checkpoint_key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(run_id,stage,checkpoint_key)
);
CREATE TRIGGER IF NOT EXISTS workflow_generation_no_update
BEFORE UPDATE ON workflow_generation
WHEN OLD.generation_id != NEW.generation_id OR OLD.run_id != NEW.run_id
  OR OLD.stage != NEW.stage OR OLD.market != NEW.market
  OR OLD.scope_fingerprint != NEW.scope_fingerprint OR OLD.item_count != NEW.item_count
  OR OLD.payload_json != NEW.payload_json OR OLD.created_at != NEW.created_at
BEGIN SELECT RAISE(ABORT,'workflow generation facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS workflow_generation_no_delete
BEFORE DELETE ON workflow_generation
BEGIN SELECT RAISE(ABORT,'workflow generation is append only'); END;
""")


SECOND_CYCLE_ACCOUNT_IDENTITY = Migration(12, "account_identity_generation_v1", """
CREATE TABLE IF NOT EXISTS account_runtime_setting(
  market TEXT NOT NULL,
  account TEXT NOT NULL,
  role TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(market,account)
);
CREATE TABLE IF NOT EXISTS account_identity_generation(
  generation_id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  account TEXT NOT NULL,
  role TEXT NOT NULL,
  reason TEXT NOT NULL,
  browser_ref TEXT,
  http_ref TEXT,
  im_ref TEXT,
  institution_fingerprint TEXT,
  capability_json TEXT NOT NULL,
  state TEXT NOT NULL,
  created_at REAL NOT NULL,
  published_at REAL,
  error_code TEXT
);
CREATE INDEX IF NOT EXISTS account_identity_generation_current
  ON account_identity_generation(market,account,state,published_at DESC);
CREATE TABLE IF NOT EXISTS account_maintenance_intent(
  intent_id TEXT PRIMARY KEY,
  request_id TEXT NOT NULL UNIQUE,
  market TEXT NOT NULL,
  account TEXT NOT NULL,
  role TEXT NOT NULL,
  operation TEXT NOT NULL,
  state TEXT NOT NULL,
  expected_generation_id TEXT,
  result_generation_id TEXT,
  scheduled_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  checkpoint_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS account_maintenance_queue
  ON account_maintenance_intent(state,scheduled_at,role,created_at);
CREATE TABLE IF NOT EXISTS account_capability_observation(
  observation_id TEXT PRIMARY KEY,
  generation_id TEXT NOT NULL,
  capability TEXT NOT NULL,
  state TEXT NOT NULL,
  evidence_ref TEXT,
  observed_at REAL NOT NULL,
  UNIQUE(generation_id,capability)
);
CREATE TRIGGER IF NOT EXISTS account_identity_generation_no_update
BEFORE UPDATE ON account_identity_generation
WHEN OLD.generation_id != NEW.generation_id OR OLD.market != NEW.market
  OR OLD.account != NEW.account OR OLD.role != NEW.role OR OLD.reason != NEW.reason
  OR COALESCE(OLD.browser_ref,'') != COALESCE(NEW.browser_ref,'')
  OR COALESCE(OLD.http_ref,'') != COALESCE(NEW.http_ref,'')
  OR COALESCE(OLD.im_ref,'') != COALESCE(NEW.im_ref,'')
  OR COALESCE(OLD.institution_fingerprint,'') != COALESCE(NEW.institution_fingerprint,'')
  OR OLD.capability_json != NEW.capability_json OR OLD.created_at != NEW.created_at
BEGIN SELECT RAISE(ABORT,'account identity generation facts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS account_identity_generation_no_delete
BEFORE DELETE ON account_identity_generation
BEGIN SELECT RAISE(ABORT,'account identity generation is append only'); END;
CREATE TRIGGER IF NOT EXISTS account_capability_observation_no_update
BEFORE UPDATE ON account_capability_observation
BEGIN SELECT RAISE(ABORT,'account capability observation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS account_capability_observation_no_delete
BEFORE DELETE ON account_capability_observation
BEGIN SELECT RAISE(ABORT,'account capability observation is append only'); END;
""")


SECOND_CYCLE_CONTINUOUS_OPERATIONS = Migration(13, "collaboration_and_continuous_send_v1", """
CREATE TABLE IF NOT EXISTS creator_collaboration_event(
  event_id TEXT PRIMARY KEY,
  request_id TEXT UNIQUE,
  plan_id TEXT NOT NULL,
  market TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  source TEXT NOT NULL,
  old_status TEXT NOT NULL,
  new_status TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  revision INTEGER NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(plan_id,creator_id,revision)
);
CREATE TABLE IF NOT EXISTS creator_collaboration_current(
  plan_id TEXT NOT NULL,
  market TEXT NOT NULL,
  creator_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  revision INTEGER NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(plan_id,creator_id)
);
CREATE TABLE IF NOT EXISTS agent_reply_template(
  template_key TEXT PRIMARY KEY,
  action TEXT NOT NULL UNIQUE,
  current_revision INTEGER NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_reply_template_revision(
  template_key TEXT NOT NULL,
  revision INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(template_key,revision)
);
CREATE TABLE IF NOT EXISTS continuous_send_control(
  plan_id TEXT PRIMARY KEY,
  automatic_enabled INTEGER NOT NULL DEFAULT 0,
  run_requested INTEGER NOT NULL DEFAULT 0,
  stop_requested INTEGER NOT NULL DEFAULT 0,
  window_start TEXT NOT NULL DEFAULT '16:30',
  window_end TEXT NOT NULL DEFAULT '24:00',
  template_id TEXT NOT NULL DEFAULT 'standard',
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS continuous_send_control_request(
  request_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  expected_revision INTEGER NOT NULL,
  action TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_revision INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS continuous_send_runtime(
  plan_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  current_delivery_id TEXT,
  current_creator_id TEXT,
  current_pid TEXT,
  confirmed_today INTEGER NOT NULL DEFAULT 0,
  failed_known INTEGER NOT NULL DEFAULT 0,
  unknown INTEGER NOT NULL DEFAULT 0,
  started_at REAL,
  seen_at REAL,
  last_success_at REAL,
  stopped_at REAL,
  stop_reason TEXT,
  worker_pid INTEGER
);
CREATE TABLE IF NOT EXISTS taplink_reconcile_attempt(
  attempt_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  pid TEXT NOT NULL,
  attempt_no INTEGER NOT NULL,
  scheduled_delay_seconds INTEGER NOT NULL,
  state TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  observed_at REAL NOT NULL,
  UNIQUE(intent_id,attempt_no)
);
CREATE INDEX IF NOT EXISTS taplink_reconcile_intent
  ON taplink_reconcile_attempt(intent_id,attempt_no);
CREATE TRIGGER IF NOT EXISTS creator_collaboration_event_no_update
BEFORE UPDATE ON creator_collaboration_event
BEGIN SELECT RAISE(ABORT,'collaboration event is append only'); END;
CREATE TRIGGER IF NOT EXISTS creator_collaboration_event_no_delete
BEFORE DELETE ON creator_collaboration_event
BEGIN SELECT RAISE(ABORT,'collaboration event is append only'); END;
CREATE TRIGGER IF NOT EXISTS agent_reply_template_revision_no_update
BEFORE UPDATE ON agent_reply_template_revision
BEGIN SELECT RAISE(ABORT,'agent template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS agent_reply_template_revision_no_delete
BEFORE DELETE ON agent_reply_template_revision
BEGIN SELECT RAISE(ABORT,'agent template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS taplink_reconcile_attempt_no_update
BEFORE UPDATE ON taplink_reconcile_attempt
BEGIN SELECT RAISE(ABORT,'taplink reconcile attempt is immutable'); END;
CREATE TRIGGER IF NOT EXISTS taplink_reconcile_attempt_no_delete
BEFORE DELETE ON taplink_reconcile_attempt
BEGIN SELECT RAISE(ABORT,'taplink reconcile attempt is append only'); END;
""")


SECOND_CYCLE_TEMPLATE_REVIEW = Migration(14, "send_template_semantic_review_v1", """
CREATE TABLE IF NOT EXISTS send_template_review(
  template_id TEXT PRIMARY KEY,
  content_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL,
  revision INTEGER NOT NULL,
  updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS send_template_review_event(
  request_id TEXT PRIMARY KEY,
  template_id TEXT NOT NULL,
  content_fingerprint TEXT NOT NULL,
  state TEXT NOT NULL,
  expected_revision INTEGER NOT NULL,
  result_revision INTEGER NOT NULL,
  created_at REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS send_template_review_event_no_update
BEFORE UPDATE ON send_template_review_event
BEGIN SELECT RAISE(ABORT,'send template review event is append only'); END;
CREATE TRIGGER IF NOT EXISTS send_template_review_event_no_delete
BEFORE DELETE ON send_template_review_event
BEGIN SELECT RAISE(ABORT,'send template review event is append only'); END;
""")


SECOND_CYCLE_MARKET_TEMPLATE_SCOPE = Migration(15, "market_template_scope_v1", """
CREATE TABLE IF NOT EXISTS market_manual_reply_template(
  plan_id TEXT NOT NULL,
  template_id TEXT NOT NULL,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  state TEXT NOT NULL,
  current_revision INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(plan_id,template_id)
);
CREATE TABLE IF NOT EXISTS market_manual_reply_template_revision(
  plan_id TEXT NOT NULL,
  template_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(plan_id,template_id,revision)
);
CREATE TABLE IF NOT EXISTS market_agent_reply_template(
  plan_id TEXT NOT NULL,
  template_key TEXT NOT NULL,
  action TEXT NOT NULL,
  current_revision INTEGER NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(plan_id,template_key),
  UNIQUE(plan_id,action)
);
CREATE TABLE IF NOT EXISTS market_agent_reply_template_revision(
  plan_id TEXT NOT NULL,
  template_key TEXT NOT NULL,
  revision INTEGER NOT NULL,
  body TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(plan_id,template_key,revision)
);
CREATE TRIGGER IF NOT EXISTS market_manual_reply_template_revision_no_update
BEFORE UPDATE ON market_manual_reply_template_revision
BEGIN SELECT RAISE(ABORT,'market manual template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS market_manual_reply_template_revision_no_delete
BEFORE DELETE ON market_manual_reply_template_revision
BEGIN SELECT RAISE(ABORT,'market manual template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS market_agent_reply_template_revision_no_update
BEFORE UPDATE ON market_agent_reply_template_revision
BEGIN SELECT RAISE(ABORT,'market agent template revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS market_agent_reply_template_revision_no_delete
BEFORE DELETE ON market_agent_reply_template_revision
BEGIN SELECT RAISE(ABORT,'market agent template revision is immutable'); END;
""")


SECOND_CYCLE_MARKET_READ_MODEL = Migration(16, "market_read_model_v1", """
CREATE TABLE IF NOT EXISTS market_read_generation(
  generation_id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  view TEXT NOT NULL,
  revision INTEGER NOT NULL,
  source_fingerprint TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  observed_at REAL NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(market,view,revision)
);
CREATE TABLE IF NOT EXISTS market_read_head(
  market TEXT NOT NULL,
  view TEXT NOT NULL,
  generation_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  observed_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(market,view)
);
CREATE INDEX IF NOT EXISTS market_read_generation_lookup
  ON market_read_generation(market,view,revision DESC);
CREATE TRIGGER IF NOT EXISTS market_read_generation_no_update
BEFORE UPDATE ON market_read_generation
BEGIN SELECT RAISE(ABORT,'market read generation is immutable'); END;
CREATE TRIGGER IF NOT EXISTS market_read_generation_no_delete
BEFORE DELETE ON market_read_generation
BEGIN SELECT RAISE(ABORT,'market read generation is append only'); END;
""")


SECOND_CYCLE_WORKFLOW_RESOURCES = Migration(17, "workflow_resource_lease_v1", """
CREATE TABLE IF NOT EXISTS workflow_claim_sequence(
  sequence_id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_stage_claim(
  stage_run_id TEXT PRIMARY KEY,
  owner_id TEXT NOT NULL,
  fence INTEGER NOT NULL,
  lease_until REAL NOT NULL,
  heartbeat_at REAL NOT NULL,
  worker_pid INTEGER,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_resource_slot(
  resource_key TEXT NOT NULL,
  slot_no INTEGER NOT NULL,
  owner_stage_run_id TEXT NOT NULL,
  fence INTEGER NOT NULL,
  lease_until REAL NOT NULL,
  heartbeat_at REAL NOT NULL,
  PRIMARY KEY(resource_key,slot_no),
  UNIQUE(owner_stage_run_id,resource_key)
);
CREATE INDEX IF NOT EXISTS workflow_stage_claim_lease
  ON workflow_stage_claim(lease_until,stage_run_id);
CREATE INDEX IF NOT EXISTS workflow_resource_owner
  ON workflow_resource_slot(owner_stage_run_id,fence);
""")


SECOND_CYCLE_AGENT_CONVERSATION = Migration(18, "agent_conversation_v2", """
CREATE TABLE IF NOT EXISTS agent_reply_guide_revision(
  plan_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  body TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(plan_id,revision)
);
CREATE TRIGGER IF NOT EXISTS agent_reply_guide_no_update
BEFORE UPDATE ON agent_reply_guide_revision BEGIN SELECT RAISE(ABORT,'agent guide revision is immutable'); END;
CREATE TRIGGER IF NOT EXISTS agent_reply_guide_no_delete
BEFORE DELETE ON agent_reply_guide_revision BEGIN SELECT RAISE(ABORT,'agent guide revision is immutable'); END;
CREATE TABLE IF NOT EXISTS agent_reply_decision_v2(
  decision_id TEXT PRIMARY KEY,
  plan_id TEXT NOT NULL,
  market TEXT NOT NULL,
  creator_id TEXT,
  turn_id TEXT,
  pending_revision INTEGER,
  guide_revision INTEGER NOT NULL,
  input_hash TEXT NOT NULL,
  input_json TEXT NOT NULL,
  output_json TEXT,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('simulation','production')),
  state TEXT NOT NULL,
  service_reply_id TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_reply_decision_creator
ON agent_reply_decision_v2(plan_id,creator_id,created_at DESC);
CREATE INDEX IF NOT EXISTS agent_reply_decision_turn
ON agent_reply_decision_v2(plan_id,turn_id,mode,created_at DESC);
CREATE TABLE IF NOT EXISTS agent_reply_simulation_turn(
  session_id TEXT NOT NULL,
  turn_no INTEGER NOT NULL,
  market TEXT NOT NULL,
  user_text TEXT NOT NULL,
  decision_id TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(session_id,turn_no)
);
""")


SECOND_CYCLE_INBOX_HISTORY = Migration(19, "inbox_history_coverage_v1", """
CREATE TABLE IF NOT EXISTS inbox_history_checkpoint(
 plan_id TEXT NOT NULL, cid TEXT NOT NULL, oec TEXT NOT NULL,
 identity_key TEXT NOT NULL, next_cursor TEXT NOT NULL,
 state TEXT NOT NULL, pages INTEGER NOT NULL, started_at REAL NOT NULL,
 updated_at REAL NOT NULL, PRIMARY KEY(plan_id,cid)
);
CREATE TABLE IF NOT EXISTS inbox_history_page(
 plan_id TEXT NOT NULL, cid TEXT NOT NULL, page_number INTEGER NOT NULL,
 request_cursor TEXT NOT NULL, next_cursor TEXT NOT NULL, has_more INTEGER NOT NULL,
 message_count INTEGER NOT NULL, message_ids_hash TEXT NOT NULL,
 body_sha256 TEXT NOT NULL, added INTEGER NOT NULL, contents_added INTEGER NOT NULL,
 observed_at REAL NOT NULL, PRIMARY KEY(plan_id,cid,page_number),
 UNIQUE(plan_id,cid,request_cursor)
);
""")


SECOND_CYCLE_INBOX_HISTORY_DEFERRED = Migration(20, "inbox_history_hot_handoff_v1", """
ALTER TABLE inbox_history_page ADD COLUMN deferred_to_hot INTEGER NOT NULL DEFAULT 0;
""")


SECOND_CYCLE_ROLLING_LEADS = Migration(21, "rolling_market_leads_v1", """
CREATE TABLE kalodata_video_generation_scope(generation_id TEXT PRIMARY KEY,market TEXT NOT NULL,region TEXT NOT NULL,currency TEXT NOT NULL);
INSERT INTO kalodata_video_generation_scope SELECT generation_id,'it','IT','EUR' FROM kalodata_video_generation;
CREATE TABLE kalodata_video_run_scope(run_id TEXT PRIMARY KEY,market TEXT NOT NULL);
INSERT INTO kalodata_video_run_scope SELECT run_id,'it' FROM kalodata_video_run;
ALTER TABLE video_lead_current RENAME TO video_lead_current_legacy_it_v20;
CREATE TABLE video_lead_current(
 generation_id TEXT NOT NULL,pid TEXT NOT NULL,kalodata_creator_id TEXT NOT NULL,handle TEXT NOT NULL,
 run_id TEXT NOT NULL,video_id TEXT NOT NULL,views INTEGER NOT NULL,released_at TEXT NOT NULL,
 video_sale INTEGER NOT NULL,observed_at REAL NOT NULL,market TEXT NOT NULL DEFAULT 'it',
 PRIMARY KEY(market,pid,kalodata_creator_id));
INSERT INTO video_lead_current SELECT *,'it' FROM video_lead_current_legacy_it_v20;
CREATE INDEX video_market_current_order ON video_lead_current(market,views DESC,released_at DESC,pid,kalodata_creator_id);
ALTER TABLE kalodata_video_author_cache RENAME TO kalodata_video_author_cache_legacy_it_v20;
CREATE TABLE kalodata_video_author_cache(
 video_id TEXT NOT NULL,kalodata_creator_id TEXT NOT NULL,handle TEXT NOT NULL,payload_hash TEXT NOT NULL,
 video_url TEXT NOT NULL,observed_at REAL NOT NULL,market TEXT NOT NULL DEFAULT 'it',PRIMARY KEY(market,video_id));
INSERT INTO kalodata_video_author_cache SELECT *,'it' FROM kalodata_video_author_cache_legacy_it_v20;
ALTER TABLE kalodata_video_head RENAME TO kalodata_video_head_legacy_it_v20;
CREATE TABLE kalodata_video_head(pid TEXT NOT NULL,run_id TEXT NOT NULL,market TEXT NOT NULL DEFAULT 'it',PRIMARY KEY(market,pid));
INSERT INTO kalodata_video_head SELECT *,'it' FROM kalodata_video_head_legacy_it_v20;
CREATE VIEW current_identity_source AS
 SELECT h.plan_id,x.source_id,x.source_handle,x.pid,x.source_rank,x.source_kind
 FROM lead_query_head h JOIN lead_query_selection s ON s.query_id=h.query_id
 JOIN source_edge_index x ON x.plan_id=h.plan_id AND x.source_id=s.source_id
 UNION ALL
 SELECT p.id,x.source_id,x.source_handle,x.pid,x.source_rank,x.source_kind
 FROM video_lead_current v JOIN plan p ON p.market=v.market AND p.institution='bjn-local-research'
 JOIN source_edge_index x ON x.plan_id=p.id AND x.source_id=('video:'||v.run_id||':'||v.video_id);
CREATE TABLE lead_query_task(
 market TEXT NOT NULL,pid TEXT NOT NULL,kind TEXT NOT NULL,state TEXT NOT NULL,
 query_id TEXT,window_start TEXT,window_end TEXT,policy_version TEXT NOT NULL,request_scope_json TEXT NOT NULL DEFAULT '{}',
 ready_at REAL NOT NULL,served_at REAL NOT NULL DEFAULT 0,service_order INTEGER NOT NULL DEFAULT 0,units INTEGER NOT NULL DEFAULT 0,
 material_key TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,retry_at REAL NOT NULL DEFAULT 0,
 last_completed REAL,next_due REAL,last_error TEXT,created_at REAL NOT NULL,updated_at REAL NOT NULL,
 PRIMARY KEY(market,pid,kind));
CREATE INDEX lead_query_task_ready ON lead_query_task(market,kind,state,retry_at,served_at,ready_at,pid);
CREATE TABLE lead_query_task_event(
 id INTEGER PRIMARY KEY AUTOINCREMENT,market TEXT NOT NULL,pid TEXT NOT NULL,kind TEXT NOT NULL,
 query_id TEXT,state TEXT NOT NULL,detail_json TEXT NOT NULL,at REAL NOT NULL);
CREATE TABLE lead_query_market(
 market TEXT PRIMARY KEY,next_kind TEXT NOT NULL DEFAULT 'A',next_a_phase TEXT NOT NULL DEFAULT 'resume',next_b_phase TEXT NOT NULL DEFAULT 'resume',service_counter INTEGER NOT NULL DEFAULT 0,state TEXT NOT NULL DEFAULT 'ready',
 retry_at REAL NOT NULL DEFAULT 0,quota_at REAL,available_at REAL,quota_checks INTEGER NOT NULL DEFAULT 0,
 last_error TEXT,scope_checked_at REAL NOT NULL DEFAULT 0,updated_at REAL NOT NULL);
""")


SECOND_CYCLE_IDENTITY_RETRY = Migration(22, "identity_retry_budget_v1", """
CREATE TABLE IF NOT EXISTS identity_handle_budget(
 market TEXT NOT NULL,handle TEXT NOT NULL,failures INTEGER NOT NULL,next_at REAL NOT NULL,
 last_event TEXT NOT NULL,reason TEXT,updated REAL NOT NULL,PRIMARY KEY(market,handle));
CREATE TABLE IF NOT EXISTS identity_retry_event(
 id TEXT PRIMARY KEY,market TEXT NOT NULL,handle TEXT NOT NULL,account TEXT NOT NULL,
 category TEXT NOT NULL,reason TEXT,at REAL NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS identity_retry_market_handle ON identity_retry_event(market,handle,at);
CREATE TABLE IF NOT EXISTS identity_account_wait(
 market TEXT PRIMARY KEY,account TEXT NOT NULL,generation TEXT NOT NULL,failures INTEGER NOT NULL,
 next_at REAL NOT NULL,last_event TEXT NOT NULL,reason TEXT,updated REAL NOT NULL);
""")

SECOND_CYCLE_OUTREACH_SHARE = Migration(23, "outreach_ab_share_v1", """
CREATE TABLE outreach_rotation(market TEXT PRIMARY KEY,position INTEGER NOT NULL CHECK(position BETWEEN 0 AND 4),updated REAL NOT NULL);
CREATE TABLE outreach_allocation(delivery_id TEXT PRIMARY KEY REFERENCES cycle_delivery(id),market TEXT NOT NULL,day TEXT NOT NULL,
 oec TEXT NOT NULL,creator_id TEXT NOT NULL,actual_class TEXT NOT NULL CHECK(actual_class IN ('A','B')),
 preferred_class TEXT NOT NULL CHECK(preferred_class IN ('A','B')),position INTEGER NOT NULL,
 borrowed_reason TEXT,allocated REAL NOT NULL,UNIQUE(market,day,oec));
CREATE INDEX outreach_allocation_day ON outreach_allocation(market,day,actual_class);
""")

SECOND_CYCLE_INVITATION_INBOX = Migration(24, "invitation_inbox_proof_index_v1", """
CREATE TABLE IF NOT EXISTS cycle_delivery_check(delivery_id TEXT NOT NULL,kind TEXT NOT NULL,checked REAL NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS cycle_delivery_check_lookup ON cycle_delivery_check(delivery_id,kind,checked);
""")

DATABASES = {
    "catalog-links": ("catalog-links.sqlite", (CATALOG_LINKS,)),
    "second-cycle": ("second-cycle.sqlite", (SECOND_CYCLE, SECOND_CYCLE_INDEXES,
                                                SECOND_CYCLE_FROZEN_SEND,
                                                SECOND_CYCLE_REPLY_EVENTS,
                                                SECOND_CYCLE_TURN_REVIEW,
                                                SECOND_CYCLE_REVIEW_APPLICATION,
                                                SECOND_CYCLE_VIDEO_EVIDENCE,
                                                SECOND_CYCLE_VIDEO_PAGING,
                                                SECOND_CYCLE_AB_LEADS,
                                                SECOND_CYCLE_CONVERSATION_WORKBENCH,
                                                SECOND_CYCLE_AUTOMATED_WORKFLOW,
                                                SECOND_CYCLE_ACCOUNT_IDENTITY,
                                                SECOND_CYCLE_CONTINUOUS_OPERATIONS,
                                                SECOND_CYCLE_TEMPLATE_REVIEW,
                                                SECOND_CYCLE_MARKET_TEMPLATE_SCOPE,
                                                SECOND_CYCLE_MARKET_READ_MODEL,
                                                SECOND_CYCLE_WORKFLOW_RESOURCES,
                                                SECOND_CYCLE_AGENT_CONVERSATION,
                                                SECOND_CYCLE_INBOX_HISTORY,
                                                SECOND_CYCLE_INBOX_HISTORY_DEFERRED,
                                                SECOND_CYCLE_ROLLING_LEADS, SECOND_CYCLE_IDENTITY_RETRY, SECOND_CYCLE_OUTREACH_SHARE, SECOND_CYCLE_INVITATION_INBOX)),
}

REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS agent_schema_migration(
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at REAL NOT NULL
)
"""

def _backfill_market_templates(db):
    tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required={"plan","manual_reply_template","manual_reply_template_revision",
              "agent_reply_template","agent_reply_template_revision"}
    if not required<=tables:return
    db.execute("""INSERT OR IGNORE INTO market_manual_reply_template
      SELECT p.id,t.template_id,t.name,t.category,t.state,t.current_revision,t.created_at,t.updated_at
      FROM manual_reply_template t JOIN plan p ON p.market='it' AND p.institution='bjn-local-research'""")
    db.execute("""INSERT OR IGNORE INTO market_manual_reply_template_revision
      SELECT p.id,r.template_id,r.revision,r.body,r.created_at
      FROM manual_reply_template_revision r JOIN plan p ON p.market='it' AND p.institution='bjn-local-research'""")
    db.execute("""INSERT OR IGNORE INTO market_agent_reply_template
      SELECT p.id,t.template_key,t.action,t.current_revision,t.updated_at
      FROM agent_reply_template t JOIN plan p ON p.market='it' AND p.institution='bjn-local-research'""")
    db.execute("""INSERT OR IGNORE INTO market_agent_reply_template_revision
      SELECT p.id,r.template_key,r.revision,r.body,r.created_at
      FROM agent_reply_template_revision r JOIN plan p ON p.market='it' AND p.institution='bjn-local-research'""")


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
                if key=="second-cycle" and migration.version==21:
                    from lib.video_identity import backfill_current
                    backfill_current(db)
                if key=="second-cycle" and migration.version==15:
                    _backfill_market_templates(db)
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
