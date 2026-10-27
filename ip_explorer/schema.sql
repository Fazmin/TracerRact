-- Uploaded and seed source files. Per kind there is at most one 'active' (latest upload, replaces
-- the previous one) and optionally one 'builtin' (loaded with init-db); 'staged' files are mid-import.
CREATE TABLE IF NOT EXISTS source_files (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('rz', 'bc')),
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('builtin', 'active', 'staged')),
  uploaded_at TEXT NOT NULL,
  headers TEXT NOT NULL DEFAULT '[]',
  column_map TEXT NOT NULL DEFAULT '{}',
  row_count INTEGER NOT NULL DEFAULT 0
);

-- BlueCat rows after header mapping (same keys as ingest.BCF).
CREATE TABLE IF NOT EXISTS bluecat_rows (
  file_id INTEGER NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
  ip TEXT NOT NULL, host TEXT NOT NULL, an TEXT NOT NULL, mac TEXT NOT NULL, loc TEXT NOT NULL, owner TEXT NOT NULL,
  room TEXT NOT NULL, state TEXT NOT NULL, parent TEXT NOT NULL, sub TEXT NOT NULL, subName TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bluecat_rows_file ON bluecat_rows (file_id);

-- runZero rows as raw cells; the column map is applied when merging so it can change before commit.
CREATE TABLE IF NOT EXISTS runzero_rows (
  file_id INTEGER NOT NULL REFERENCES source_files (id) ON DELETE CASCADE,
  idx INTEGER NOT NULL,
  cells TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runzero_rows_file ON runzero_rows (file_id, idx);

-- Merged assets, rebuilt from the effective source files on every import. Column names follow the
-- record keys in merge.py, except first/last/match which are SQL keywords.
CREATE TABLE IF NOT EXISTS assets (
  id INTEGER PRIMARY KEY,
  hasRz INTEGER NOT NULL, hasBc INTEGER NOT NULL, dup INTEGER NOT NULL,
  ip TEXT NOT NULL, n INTEGER NOT NULL, ipSrc TEXT NOT NULL,
  host TEXT NOT NULL, mac TEXT NOT NULL, vendor TEXT NOT NULL, bcIp TEXT NOT NULL, bcHost TEXT NOT NULL, an TEXT NOT NULL,
  owner TEXT NOT NULL, room TEXT NOT NULL, roomL TEXT NOT NULL, bldC TEXT NOT NULL, bld TEXT NOT NULL,
  bldSrc TEXT NOT NULL, sub TEXT NOT NULL, subName TEXT NOT NULL, parent TEXT NOT NULL, state TEXT NOT NULL,
  loc TEXT NOT NULL, type TEXT NOT NULL, cat TEXT NOT NULL, os TEXT NOT NULL, osv TEXT NOT NULL, hw TEXT NOT NULL,
  risk TEXT NOT NULL, alive TEXT NOT NULL, first_ms INTEGER NOT NULL, last_ms INTEGER NOT NULL, agent TEXT NOT NULL,
  det TEXT NOT NULL, eol TEXT NOT NULL, serial TEXT NOT NULL, vlan TEXT NOT NULL, func TEXT NOT NULL,
  crit TEXT NOT NULL, notes TEXT NOT NULL, cov TEXT NOT NULL, match_by TEXT NOT NULL, blk TEXT NOT NULL,
  seenB TEXT NOT NULL, seenM TEXT NOT NULL, seenD INTEGER NOT NULL,
  scopeIps TEXT NOT NULL, ips2 TEXT NOT NULL, hosts TEXT NOT NULL, macs TEXT NOT NULL, tcp TEXT NOT NULL, udp TEXT NOT NULL,
  proto TEXT NOT NULL, prod TEXT NOT NULL, tags TEXT NOT NULL, bcMore TEXT NOT NULL, raw TEXT,
  risk_idx INTEGER NOT NULL, tcp_count INTEGER NOT NULL, tag_count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_room ON assets (bld, roomL);
CREATE INDEX IF NOT EXISTS idx_assets_sub ON assets (sub);

-- One row per facet value per asset (multi-valued facets such as ports get several).
CREATE TABLE IF NOT EXISTS asset_facets (
  asset_id INTEGER NOT NULL,
  facet TEXT NOT NULL,
  value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_facets_value ON asset_facets (facet, value, asset_id);
CREATE INDEX IF NOT EXISTS idx_asset_facets_asset ON asset_facets (asset_id, facet, value);

-- Lowercased text each search key looks in (see search.SEARCH_FIELDS); all_text is free-text search.
CREATE TABLE IF NOT EXISTS asset_search (
  asset_id INTEGER PRIMARY KEY,
  all_text TEXT NOT NULL,
  f_ip TEXT NOT NULL, f_host TEXT NOT NULL, f_mac TEXT NOT NULL, f_room TEXT NOT NULL, f_bldg TEXT NOT NULL,
  f_owner TEXT NOT NULL, f_type TEXT NOT NULL, f_os TEXT NOT NULL, f_hw TEXT NOT NULL, f_vendor TEXT NOT NULL,
  f_risk TEXT NOT NULL, f_port TEXT NOT NULL, f_tag TEXT NOT NULL, f_sub TEXT NOT NULL, f_state TEXT NOT NULL,
  f_cat TEXT NOT NULL, f_proto TEXT NOT NULL, f_product TEXT NOT NULL, f_agent TEXT NOT NULL, f_note TEXT NOT NULL,
  f_cov TEXT NOT NULL, f_alive TEXT NOT NULL, f_serial TEXT NOT NULL, f_vlan TEXT NOT NULL, f_eol TEXT NOT NULL
);

-- Every IPv4 address of each asset (see merge.addresses), for CIDR and range searches.
CREATE TABLE IF NOT EXISTS asset_ips (
  asset_id INTEGER NOT NULL,
  n INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_ips_n ON asset_ips (n, asset_id);

-- Merge stats and the reference time used for "last seen" buckets.
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- IP tracker (see tracker.py): addresses checked on a schedule. Times are Unix seconds (UTC).
-- A scan is a set of addresses added together, such as a /24, with one schedule for all of them.
CREATE TABLE IF NOT EXISTS track_scans (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  spec TEXT NOT NULL DEFAULT '',            -- what was entered, e.g. 130.113.10.0/24
  every INTEGER NOT NULL,
  paced INTEGER NOT NULL DEFAULT 0,
  paused INTEGER NOT NULL DEFAULT 0,
  added_at INTEGER NOT NULL
);

-- next_at 0 means check as soon as possible; last_* describe the latest check. Addresses tracked on
-- their own have no scan. db.migrate adds scan_id and paced to tables made before they existed.
CREATE TABLE IF NOT EXISTS track_targets (
  id INTEGER PRIMARY KEY,
  ip TEXT NOT NULL UNIQUE,
  n INTEGER NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  every INTEGER NOT NULL,
  paused INTEGER NOT NULL DEFAULT 0,
  added_at INTEGER NOT NULL,
  next_at INTEGER NOT NULL DEFAULT 0,
  last_at INTEGER, last_up INTEGER, last_up_at INTEGER, last_rtt REAL,
  last_via TEXT NOT NULL DEFAULT '',
  checks INTEGER NOT NULL DEFAULT 0,
  ups INTEGER NOT NULL DEFAULT 0,
  scan_id INTEGER REFERENCES track_scans (id) ON DELETE CASCADE,
  paced INTEGER NOT NULL DEFAULT 0          -- checked one at a time, a random gap apart
);
CREATE INDEX IF NOT EXISTS idx_track_targets_due ON track_targets (paused, next_at);
CREATE INDEX IF NOT EXISTS idx_track_targets_n ON track_targets (n);
CREATE INDEX IF NOT EXISTS idx_track_targets_scan ON track_targets (scan_id);

-- One row per check of a tracked address.
CREATE TABLE IF NOT EXISTS track_checks (
  target_id INTEGER NOT NULL REFERENCES track_targets (id) ON DELETE CASCADE,
  at INTEGER NOT NULL,
  up INTEGER NOT NULL,
  rtt REAL,
  via TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_track_checks_target ON track_checks (target_id, at);
CREATE INDEX IF NOT EXISTS idx_track_checks_at ON track_checks (at);

-- Scheduler lease and heartbeat, last cycle, scanner self-test (JSON values).
CREATE TABLE IF NOT EXISTS tracker_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
