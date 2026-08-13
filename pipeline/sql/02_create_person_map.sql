CREATE TABLE IF NOT EXISTS dwh_ezru_loans.metrica_person_map (
  cid text PRIMARY KEY,
  uid text,
  pkey text NOT NULL,
  visits_seen integer NOT NULL DEFAULT 0,
  updated_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE dwh_ezru_loans.metrica_person_map IS
  'Metrica browser (clientID) -> person key. uid = UserID from parsedParams = clients.client_number. Owner: analytics (E.Rybakov).';
CREATE INDEX IF NOT EXISTS ix_metrica_person_map_uid
  ON dwh_ezru_loans.metrica_person_map(uid);
CREATE INDEX IF NOT EXISTS ix_metrica_person_map_pkey
  ON dwh_ezru_loans.metrica_person_map(pkey);