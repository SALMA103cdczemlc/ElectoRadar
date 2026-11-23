import json
import logging
import math
import os
import re
import urllib.request
import uuid
from datetime import datetime, timezone

import azure.functions as func

import firestore
import notif
from db import db

app = func.FunctionApp(http_auth_level=func.AuthLevel.FUNCTION)

VM_URL = os.environ.get("VM_URL", "").rstrip("/")
VM_API_KEY = os.environ.get("VM_API_KEY", "")
SIM_THRESHOLD = float(os.environ.get("SIM_THRESHOLD", "0.60"))
SEUIL = int(os.environ.get("SEUIL", "70"))
SUSPECT_AGE_DAYS = int(os.environ.get("SUSPECT_AGE_DAYS", "30"))
SITE_URL = os.environ.get("SITE_URL", "")
QUEUE_CONN = "QUEUE_STORAGE"

ENTITES = [
    ("liste-{}", r"(?:liste|lista|اللائحة)\s*(?:n°|n|no|رقم)?\s*(\d+)"),
    ("hay-hassani", r"hay hassani|الحي الحسني|الحسني|ibn battouta|ابن بطوطة"),
    ("tanger", r"tanger|طنجة|tanja|\btng\b"),
    ("fes", r"\bf[eè]s\b|فاس"),
    ("casablanca", r"casablanca|\bcasa\b|الدار البيضاء|كازا"),
    ("rabat", r"\brabat\b|\brbat\b|الرباط"),
    ("marrakech", r"marrakech|marrakesh|مراكش"),
    ("agadir", r"agadir|أكادير"),
    ("oujda", r"oujda|وجدة"),
    ("meknes", r"mekn[eè]s|مكناس"),
    ("kenitra", r"k[eé]nitra|القنيطرة"),
    ("tetouan", r"t[eé]touan|تطوان"),
    ("nador", r"nador|الناظور"),
    ("laayoune", r"laa?youne|العيون"),
]


def entite(text: str):
    low = text.lower()
    for name, pattern in ENTITES:
        m = re.search(pattern, low)
        if m:
            return name.format(*m.groups()) if m.groups() else name
    return None


def _json(data, status=200):
    return func.HttpResponse(json.dumps(data, ensure_ascii=False, default=str), status_code=status,
                             mimetype="application/json")


def analyze_text(text: str) -> dict:
    req = urllib.request.Request(f"{VM_URL}/analyze", data=json.dumps({"text": text}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-Api-Key": VM_API_KEY})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def cluster_post(post: dict, vec: list[float]) -> dict:
    ent = entite(post["text"])
    with db() as cur:
        cur.execute("SELECT pg_advisory_xact_lock(4242)")
        best, best_sim = None, -1.0
        if ent:
            cur.execute("""SELECT id, centroid, nb_publications FROM rumeurs WHERE entite = %s
                           ORDER BY derniere_maj DESC LIMIT 1""", (ent,))
            best = cur.fetchone()
            best_sim = cosine(vec, best["centroid"]) if best else -1.0
            match = best is not None
        else:
            cur.execute("SELECT id, centroid, nb_publications FROM rumeurs WHERE entite IS NULL")
            for r in cur.fetchall():
                s = cosine(vec, r["centroid"])
                if s > best_sim:
                    best, best_sim = r, s
            match = best is not None and best_sim >= SIM_THRESHOLD
        if match:
            rid, n = best["id"], max(int(best["nb_publications"]), 1)
            c = [(x * n + v) / (n + 1) for x, v in zip(best["centroid"], vec)]
            norm = math.sqrt(sum(x * x for x in c)) or 1.0
            cur.execute("UPDATE rumeurs SET centroid = %s WHERE id = %s", ([x / norm for x in c], rid))
            is_new = False
        else:
            rid = "R-" + uuid.uuid4().hex[:8]
            cur.execute("INSERT INTO rumeurs (id, titre, entite, centroid) VALUES (%s, %s, %s, %s)",
                        (rid, post["text"][:140], ent, vec))
            is_new = True
        cur.execute("""INSERT INTO publications (post_id, rumeur_id, texte, langue, source, url, auteur_pseudo,
                                                 portee, partages, age_compte)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (post_id) DO NOTHING""",
                    (post["post_id"], rid, post["text"], post.get("lang"), post.get("source"), post.get("url"),
                     post.get("author_pseudo"), int(post.get("reach") or 0), int(post.get("shares") or 0),
                     post.get("author_account_age_days")))
        cur.execute("""SELECT count(*) AS n,
                              count(*) FILTER (WHERE recue_le >= now() - interval '1 hour') AS h,
                              coalesce(sum(portee), 0) AS portee,
                              count(*) FILTER (WHERE age_compte IS NOT NULL AND age_compte < %s) AS suspects,
                              array_agg(DISTINCT langue) AS langues
                       FROM publications WHERE rumeur_id = %s""", (SUSPECT_AGE_DAYS, rid))
        st = cur.fetchone()
        cur.execute("""SELECT texte AS text, langue AS lang, source, url, post_id FROM publications
                       WHERE rumeur_id = %s ORDER BY recue_le LIMIT 6""", (rid,))
        samples = cur.fetchall()
        cur.execute("""UPDATE rumeurs SET nb_publications = %s, publications_heure = %s, portee = %s,
                              part_suspects = %s, langues = %s, exemples = %s, derniere_maj = now()
                       WHERE id = %s RETURNING titre, premiere_detection""",
                    (st["n"], st["h"], st["portee"], st["suspects"] / st["n"], [l for l in st["langues"] if l],
                     json.dumps(samples, ensure_ascii=False), rid))
        meta = cur.fetchone()
    return {"cluster_id": rid, "title": meta["titre"], "count": st["n"], "posts_last_hour": st["h"],
            "total_reach": int(st["portee"]), "suspect_ratio": round(st["suspects"] / st["n"], 3),
            "langs": sorted(l for l in st["langues"] if l), "samples": samples, "is_new": is_new,
            "similarity": round(best_sim, 3) if best else None, "entite": ent}


@app.route(route="analyze", methods=["POST"])
@app.queue_output(arg_name="clusters", queue_name="clusters", connection=QUEUE_CONN)
def analyze(req: func.HttpRequest, clusters: func.Out[str]) -> func.HttpResponse:
    post = req.get_json()
    try:
        result = analyze_text(post["text"])
    except Exception as e:
        logging.error(f"Modèle injoignable : {e}")
        return _json({"erreur": "modèle NLP injoignable"}, 503)
    summary = {"post_id": post["post_id"], "lang": post.get("lang"), "label": result["label"],
               "scores": result["scores"]}
    if not result["is_rumor"]:
        logging.info(json.dumps({**summary, "action": "ignorée"}, ensure_ascii=False))
        return _json({**summary, "action": "ignorée (pas une rumeur)"})
    cluster = cluster_post(post, result["embedding"])
    clusters.set(json.dumps(cluster, ensure_ascii=False))
    out = {**summary, "action": "nouvelle rumeur" if cluster["is_new"] else "ajoutée à une rumeur",
           "cluster_id": cluster["cluster_id"], "similarite": cluster["similarity"], "count": cluster["count"]}
    logging.info(json.dumps(out, ensure_ascii=False))
    return _json(out)


def virality(c: dict) -> int:
    speed = min(1.0, c["posts_last_hour"] / 10)
    reach = min(1.0, math.log10(1 + c["total_reach"]) / 5.3)
    return round(100 * (0.4 * speed + 0.3 * reach + 0.3 * float(c["suspect_ratio"])))


@app.queue_trigger(arg_name="msg", queue_name="clusters", connection=QUEUE_CONN)
@app.queue_output(arg_name="alertes", queue_name="alertes", connection=QUEUE_CONN)
def score(msg: func.QueueMessage, alertes: func.Out[str]) -> None:
    c = json.loads(msg.get_body().decode())
    s = virality(c)
    with db() as cur:
        cur.execute("SELECT alerte_envoyee, statut FROM rumeurs WHERE id = %s FOR UPDATE", (c["cluster_id"],))
        row = cur.fetchone()
        alert_needed = s >= SEUIL and not row["alerte_envoyee"] and row["statut"] == "nouvelle"
        cur.execute("UPDATE rumeurs SET score = %s, alerte_envoyee = alerte_envoyee OR %s WHERE id = %s",
                    (s, alert_needed, c["cluster_id"]))
        statut = row["statut"]
    if firestore.enabled():
        now = datetime.now(timezone.utc)
        firestore.set_doc("rumeurs", c["cluster_id"], {
            "titre": c["title"], "score": s, "nb_publications": c["count"],
            "publications_heure": c["posts_last_hour"], "portee": c["total_reach"],
            "part_suspects": float(c["suspect_ratio"]), "langues": c["langs"], "statut": statut, "maj": now})
        firestore.add_doc("scores", {"rumeur_id": c["cluster_id"], "titre": c["title"], "score": s,
                                     "nb_publications": c["count"], "horodatage": now})
    if alert_needed:
        alertes.set(json.dumps({**c, "score": s}, ensure_ascii=False))
    logging.info(json.dumps({"cluster_id": c["cluster_id"], "count": c["count"], "score": s,
                             "firestore": firestore.enabled(), "alerte": alert_needed}, ensure_ascii=False))


@app.queue_trigger(arg_name="msg", queue_name="alertes", connection=QUEUE_CONN)
def alerte(msg: func.QueueMessage) -> None:
    r = json.loads(msg.get_body().decode())
    text = (f"ElectoRadar : rumeur en forte progression (score {r['score']}/100).\n"
            f"« {r['title'][:120]} »\n{r['count']} publications, dont {r['posts_last_hour']} dans la dernière heure.\n"
            f"Langues : {', '.join(r['langs'])}\n{SITE_URL}/fact-checkeur.html?id={r['cluster_id']}")
    samples = "\n".join(f"- [{s.get('lang')}] {s.get('text')}" for s in r.get("samples", [])[:5])
    res = {"cluster_id": r["cluster_id"], "telegram": notif.telegram(text),
           "email": notif.email(f"[ElectoRadar] Alerte rumeur — score {r['score']}/100",
                                f"{text}\n\nExemples de publications :\n{samples}\n")}
    logging.info(json.dumps(res, ensure_ascii=False))


@app.route(route="rapport", methods=["GET", "POST"])
def rapport(req: func.HttpRequest) -> func.HttpResponse:
    import rapport_pdf
    day_arg = req.params.get("jour")
    day = datetime.strptime(day_arg, "%Y-%m-%d").date() if day_arg else datetime.now(rapport_pdf.TZ).date()
    with db() as cur:
        cur.execute("""SELECT id AS cluster_id, titre AS title, score AS score_max, nb_publications AS publications,
                              portee, part_suspects, array_to_string(langues, ',') AS langues
                       FROM rumeurs WHERE (derniere_maj AT TIME ZONE 'Africa/Casablanca')::date = %s
                       ORDER BY score DESC""", (day,))
        rows = cur.fetchall()
    pdf = rapport_pdf.build_pdf(rows, day)
    if req.params.get("telecharger"):
        return func.HttpResponse(pdf, mimetype="application/pdf",
                                 headers={"Content-Disposition": f"inline; filename=electoradar_{day}.pdf"})
    status = notif.email(f"ElectoRadar — rapport du {day.strftime('%d/%m/%Y')} ({len(rows)} rumeur(s))",
                         "Bonjour,\n\nVeuillez trouver ci-joint le rapport quotidien d'ElectoRadar.\n",
                         (f"electoradar_{day.isoformat()}.pdf", pdf), to=os.environ.get("REPORT_EMAIL"))
    out = {"jour": day.isoformat(), "rumeurs": len(rows), "email": status}
    logging.info(json.dumps(out, ensure_ascii=False))
    return _json(out)


@app.route(route="reinitialiser", methods=["POST"])
def reinitialiser(req: func.HttpRequest) -> func.HttpResponse:
    with db() as cur:
        cur.execute("TRUNCATE verdicts, publications, rumeurs")
    out = {"postgresql": "vidée"}
    if firestore.enabled():
        out["firestore"] = {c: firestore.clear(c) for c in ("rumeurs", "scores", "verdicts")}
    logging.info(json.dumps(out, ensure_ascii=False))
    return _json(out)
