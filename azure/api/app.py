import os

from flask import Flask, jsonify, request

import firestore
from db import db

app = Flask(__name__)
FIREBASE_PROJECT = os.environ.get("FIREBASE_PROJECT", "")
FACT_CHECKERS = {e.strip().lower() for e in os.environ.get("FACT_CHECKERS", "").split(",") if e.strip()}
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
VERDICTS = {"fausse", "trompeuse", "vraie", "en_verification", "hors_sujet"}
PUBLIC_COLS = """id, titre, nb_publications, publications_heure, portee, part_suspects, langues, score,
                 exemples, statut, alerte_envoyee, premiere_detection, derniere_maj"""


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGIN
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/<path:_any>", methods=["OPTIONS"])
def preflight(_any):
    return "", 204


def iso(row):
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def verify_fact_checker():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token
    try:
        claims = id_token.verify_firebase_token(auth[7:], grequests.Request(), audience=FIREBASE_PROJECT)
    except Exception:
        return None
    email = (claims.get("email") or "").lower()
    if FACT_CHECKERS and email not in FACT_CHECKERS:
        return None
    return email


@app.get("/")
@app.get("/health")
def health():
    try:
        with db() as cur:
            cur.execute("SELECT count(*) AS n FROM rumeurs")
            n = cur.fetchone()["n"]
        return jsonify(status="ok", base="PostgreSQL connectée", rumeurs=n)
    except Exception as e:
        return jsonify(status="erreur", detail=str(e)), 500


@app.get("/api/rumeurs")
def list_rumeurs():
    with db() as cur:
        cur.execute(f"SELECT {PUBLIC_COLS} FROM rumeurs ORDER BY score DESC, derniere_maj DESC LIMIT 200")
        return jsonify([iso(x) for x in cur.fetchall()])


@app.get("/api/rumeurs/<rid>")
def get_rumeur(rid):
    with db() as cur:
        cur.execute(f"SELECT {PUBLIC_COLS} FROM rumeurs WHERE id = %s", (rid,))
        r = cur.fetchone()
        if not r:
            return jsonify(erreur="rumeur introuvable"), 404
        cur.execute("""SELECT texte AS text, langue AS lang, source, url, auteur_pseudo, portee, recue_le
                       FROM publications WHERE rumeur_id = %s ORDER BY recue_le""", (rid,))
        pubs = [iso(p) for p in cur.fetchall()]
        cur.execute("SELECT verdict, dementi, auteur, cree_le FROM verdicts WHERE rumeur_id = %s ORDER BY cree_le DESC",
                    (rid,))
        return jsonify({**iso(r), "exemples": pubs, "verdicts": [iso(v) for v in cur.fetchall()]})


@app.post("/api/rumeurs/<rid>/verdict")
def add_verdict(rid):
    email = verify_fact_checker()
    if not email:
        return jsonify(erreur="accès réservé aux fact-checkeurs connectés"), 401
    v = request.get_json()
    if v.get("verdict") not in VERDICTS:
        return jsonify(erreur=f"verdict parmi {sorted(VERDICTS)}"), 400
    with db() as cur:
        cur.execute("SELECT 1 FROM rumeurs WHERE id = %s", (rid,))
        if not cur.fetchone():
            return jsonify(erreur="rumeur introuvable"), 404
        cur.execute("INSERT INTO verdicts (rumeur_id, verdict, dementi, auteur) VALUES (%s, %s, %s, %s) RETURNING cree_le",
                    (rid, v["verdict"], v.get("dementi"), email))
        cree = cur.fetchone()["cree_le"]
        cur.execute("UPDATE rumeurs SET statut = %s WHERE id = %s RETURNING titre, score", (v["verdict"], rid))
        r = cur.fetchone()
    if firestore.enabled():
        try:
            firestore.set_doc("verdicts", rid, {"titre": r["titre"], "verdict": v["verdict"],
                                                "dementi": v.get("dementi"), "score": r["score"], "date": cree})
        except Exception as e:
            app.logger.warning(f"Firestore : {e}")
    return jsonify(enregistre=True, rumeur=rid, verdict=v["verdict"], auteur=email, horodatage=cree.isoformat())


@app.get("/api/dementis")
def dementis():
    with db() as cur:
        cur.execute("""SELECT DISTINCT ON (r.id) r.id, r.titre, r.score, r.nb_publications, r.langues,
                              v.verdict, v.dementi, v.cree_le
                       FROM rumeurs r JOIN verdicts v ON v.rumeur_id = r.id
                       WHERE v.verdict IN ('fausse', 'trompeuse', 'vraie') AND v.dementi IS NOT NULL
                       ORDER BY r.id, v.cree_le DESC""")
        rows = sorted((iso(x) for x in cur.fetchall()), key=lambda x: x["cree_le"], reverse=True)
        return jsonify(rows)


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 8000)), debug=True)
