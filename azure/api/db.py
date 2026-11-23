import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

SCHEMA = """
CREATE TABLE IF NOT EXISTS rumeurs (
    id                    TEXT PRIMARY KEY,
    titre                 TEXT NOT NULL,
    entite                TEXT,
    centroid              DOUBLE PRECISION[] NOT NULL,
    nb_publications       INTEGER NOT NULL DEFAULT 0,
    publications_heure    INTEGER NOT NULL DEFAULT 0,
    portee                BIGINT  NOT NULL DEFAULT 0,
    part_suspects         REAL    NOT NULL DEFAULT 0,
    langues               TEXT[]  NOT NULL DEFAULT '{}',
    score                 INTEGER NOT NULL DEFAULT 0,
    exemples              JSONB   NOT NULL DEFAULT '[]',
    statut                TEXT    NOT NULL DEFAULT 'nouvelle',
    alerte_envoyee        BOOLEAN NOT NULL DEFAULT FALSE,
    premiere_detection    TIMESTAMPTZ NOT NULL DEFAULT now(),
    derniere_maj          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS publications (
    post_id        TEXT PRIMARY KEY,
    rumeur_id      TEXT NOT NULL REFERENCES rumeurs(id) ON DELETE CASCADE,
    texte          TEXT NOT NULL,
    langue         TEXT,
    source         TEXT,
    url            TEXT,
    auteur_pseudo  TEXT,
    portee         BIGINT  NOT NULL DEFAULT 0,
    partages       INTEGER NOT NULL DEFAULT 0,
    age_compte     INTEGER,
    recue_le       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS verdicts (
    id          SERIAL PRIMARY KEY,
    rumeur_id   TEXT NOT NULL REFERENCES rumeurs(id) ON DELETE CASCADE,
    verdict     TEXT NOT NULL,
    dementi     TEXT,
    auteur      TEXT NOT NULL,
    cree_le     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_ready = False


@contextmanager
def db():
    global _ready
    conn = psycopg2.connect(os.environ["DATABASE_URL"], cursor_factory=psycopg2.extras.RealDictCursor,
                            connect_timeout=15)
    try:
        with conn:
            with conn.cursor() as cur:
                if not _ready:
                    cur.execute(SCHEMA)
                    _ready = True
                yield cur
    finally:
        conn.close()
