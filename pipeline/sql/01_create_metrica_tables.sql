CREATE TABLE IF NOT EXISTS dwh_ezru_loans.metrica_goals_dict(
  goal_id bigint PRIMARY KEY,
  name text,
  goal_type text,
  status text,
  is_retargeting bool,
  raw jsonb,
  synced_at timestamptz DEFAULT now()
);
COMMENT ON TABLE dwh_ezru_loans.metrica_goals_dict IS 'Metrica goals reference. Owner: analytics (E.Rybakov). Funnel pipeline v1.0, created 2026-08-02.';
CREATE TABLE IF NOT EXISTS dwh_ezru_loans.metrica_pipeline_runs(
  run_id bigserial PRIMARY KEY,
  started_at timestamptz DEFAULT now(),
  finished_at timestamptz,
  stage text,
  status text,
  rows_loaded bigint,
  error_text text
);
COMMENT ON TABLE dwh_ezru_loans.metrica_pipeline_runs IS 'Pipeline execution journal. Owner: analytics (E.Rybakov). Also measures Metrica finalization lag via rows_loaded deltas.';
CREATE INDEX IF NOT EXISTS ix_metrica_runs_started ON dwh_ezru_loans.metrica_pipeline_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS ix_metrica_runs_stage_status ON dwh_ezru_loans.metrica_pipeline_runs(stage, status);