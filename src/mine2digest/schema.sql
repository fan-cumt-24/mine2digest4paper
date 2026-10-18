-- mine2digest schema
-- 维护约定：任何结构变更都要同步 db.SCHEMA_VERSION 与 db.MIGRATIONS。
-- 迁移通过 PRAGMA user_version 追踪。

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS papers (
  paper_id       TEXT PRIMARY KEY,
  doi            TEXT,
  title          TEXT NOT NULL,
  abstract       TEXT,
  year           INTEGER,
  venue          TEXT,
  source         TEXT,
  status         TEXT NOT NULL DEFAULT 'candidate',
  added_at       TEXT,
  updated_at     TEXT,
  revival_rounds INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);

-- status 状态机（见 db.ALLOWED_TRANSITIONS）：
-- candidate → screened → queued → fetching → fetched
--           ↘ low_priority  ↘ discarded
-- fetched → confirming → confirmed / rejected
-- fetch_failed → queued（重试）
-- low_priority → queued（复活赛）; discarded → candidate（复活重筛）

-- 一篇论文的多个外部 id（跨源去重的真相源）
-- id_type ∈ {doi, openalex, s2, arxiv, title}；title 存 normalize_title 的 sha1
CREATE TABLE IF NOT EXISTS paper_ids (
  paper_id      TEXT NOT NULL,
  id_type       TEXT NOT NULL,
  id_value      TEXT NOT NULL,
  PRIMARY KEY (id_type, id_value)
);
CREATE INDEX IF NOT EXISTS idx_paper_ids_paper ON paper_ids(paper_id);

CREATE TABLE IF NOT EXISTS relations (
  src_id        TEXT NOT NULL,
  dst_id        TEXT NOT NULL,
  relation      TEXT NOT NULL,
  is_influential INTEGER NOT NULL DEFAULT 0,
  contexts      TEXT,
  PRIMARY KEY (src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(dst_id);

CREATE TABLE IF NOT EXISTS screening_votes (
  vote_id       INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id      TEXT NOT NULL,
  agent_id      TEXT NOT NULL,
  uses_json     TEXT NOT NULL,
  topic_relevant INTEGER NOT NULL,
  created_at    TEXT NOT NULL,
  UNIQUE (paper_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_votes_paper ON screening_votes(paper_id);

CREATE TABLE IF NOT EXISTS screening_merged (
  paper_id      TEXT PRIMARY KEY,
  merged_uses   TEXT NOT NULL,
  decision      TEXT NOT NULL,
  priority      REAL NOT NULL DEFAULT 0,
  disagreement  TEXT NOT NULL DEFAULT '[]',
  merged_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_merged_decision ON screening_merged(decision, priority DESC);

-- 规范化的“用途向量”：覆盖矩阵/查询按 (target_section, use_type) 直接 join。
CREATE TABLE IF NOT EXISTS confirmed_uses (
  paper_id               TEXT NOT NULL,
  use_type               TEXT NOT NULL,
  target_section         TEXT NOT NULL,
  confidence             REAL NOT NULL,
  needs_fulltext_confirm INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (paper_id, use_type, target_section)
);
CREATE INDEX IF NOT EXISTS idx_confirmed_use ON confirmed_uses(target_section, use_type);

CREATE TABLE IF NOT EXISTS fetch_queue (
  paper_id      TEXT PRIMARY KEY,
  priority      REAL NOT NULL DEFAULT 0,
  enqueued_at   TEXT,
  claimed_at    TEXT,
  claimed_by    TEXT
);

CREATE TABLE IF NOT EXISTS fetch_attempts (
  attempt_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id      TEXT NOT NULL,
  source        TEXT,
  url           TEXT,
  success       INTEGER NOT NULL DEFAULT 0,
  error         TEXT,
  attempted_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fetch_attempts_paper ON fetch_attempts(paper_id);

CREATE TABLE IF NOT EXISTS evidence_cards (
  card_id             TEXT PRIMARY KEY,
  paper_id            TEXT NOT NULL,
  use_type            TEXT NOT NULL,
  target_section      TEXT NOT NULL,
  claim               TEXT,
  evidence_locator    TEXT,
  source_span         TEXT,
  method_snippet      TEXT,
  caveat              TEXT,
  confidence          REAL,
  stage               TEXT,  -- fulltext_confirmed | fulltext_discovered | rejected
  locator_status      TEXT,  -- pending | verified | unverified | failed
  original_prediction TEXT,
  reject_reason       TEXT,
  created_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_cards_cell ON evidence_cards(target_section, use_type, stage);

CREATE TABLE IF NOT EXISTS locator_checks (
  card_id       TEXT PRIMARY KEY,
  match_ratio   REAL,
  matched_text  TEXT,
  checked_at    TEXT
);

CREATE TABLE IF NOT EXISTS audit_samples (
  card_id       TEXT PRIMARY KEY,
  passes        INTEGER,
  reason        TEXT,
  sampled_at    TEXT
);

CREATE TABLE IF NOT EXISTS source_reliability (
  source        TEXT PRIMARY KEY,
  weight        REAL NOT NULL DEFAULT 1.0,
  updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS state_transitions (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id      TEXT NOT NULL,
  from_status   TEXT,
  to_status     TEXT NOT NULL,
  command       TEXT,
  at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transitions_paper ON state_transitions(paper_id, at);

-- 复活赛留痕（iter2 使用，schema 现在预留避免迁移）
CREATE TABLE IF NOT EXISTS revival_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id      TEXT NOT NULL,
  from_status   TEXT NOT NULL,
  trigger_cell  TEXT,
  round         INTEGER NOT NULL DEFAULT 1,
  at            TEXT NOT NULL
);
