CREATE SCHEMA IF NOT EXISTS metrica_raw;
COMMENT ON SCHEMA metrica_raw IS 'Raw Yandex Metrica Logs API data. Owner: analytics (E.Rybakov). Created 2026-08-02 for funnel automation pipeline v1.0. Rollback: pipeline/sql/99_rollback_metrica_raw.sql';
CREATE TABLE IF NOT EXISTS metrica_raw.goals_dict(
  goal_id bigint PRIMARY KEY,
  name text,
  goal_type text,
  status text,
  is_retargeting bool,
  raw jsonb,
  synced_at timestamptz DEFAULT now()
);
COMMENT ON TABLE metrica_raw.goals_dict IS 'Goals reference from Metrica Management API. Refreshed by pipeline/sync_goals.py';
CREATE TABLE IF NOT EXISTS metrica_raw.pipeline_runs(
  run_id bigserial PRIMARY KEY,
  started_at timestamptz DEFAULT now(),
  finished_at timestamptz,
  stage text,
  status text,
  rows_loaded bigint,
  error_text text
);
COMMENT ON TABLE metrica_raw.pipeline_runs IS 'Pipeline execution journal. One row per stage per run. Also used to measure Metrica finalization lag via rows_loaded deltas.';
CREATE INDEX IF NOT EXISTS ix_runs_started ON metrica_raw.pipeline_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS ix_runs_stage_status ON metrica_raw.pipeline_runs(stage, status);