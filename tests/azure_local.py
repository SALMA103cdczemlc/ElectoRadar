"""
Simulation locale de la partie Azure, pour tester la chaîne sans compte cloud.
  - la VM du modèle tourne en mémoire (FastAPI TestClient) avec un embedding FACTICE ;
  - les Azure Functions sont appelées directement ; les files Queue Storage sont simulées ;
  - Firestore, Telegram et Brevo sont simulés (enregistrés dans un journal) ;
  - PostgreSQL est une vraie base locale (DATABASE_URL).
Expose : POST /api/analyze et GET /api/rapport sur le port 7071 (comme Azure Functions en local),
         et l'API App Service sur le port 8000.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("SIM_OUT", tempfile.mkdtemp()))
os.environ.update({"VM_URL": "http://vm-simulee:8000", "VM_API_KEY": "cle-vm", "SEUIL": "70",
                   "SIM_THRESHOLD": "0.70", "SITE_URL": "https://site-simule.web.app",
                   "MODEL_DIR": str(OUT / "model"), "TRAIN_CSV": str(ROOT / "data/entrainement.csv"),
                   "FIREBASE_SA_JSON": "simule", "FIREBASE_PROJECT": "projet-simule"})
LOG = {"firestore": [], "telegram": [], "email": [], "analyses": []}

TOPICS = [r"tanger|طنجة|tanja|tng", r"hassani|الحسني|battouta|بطوطة", r"liste|اللائحة|lista", r"f[eè]s\b|فاس"]
RUMOR = r"fraude|tzwir|تزوير|fermé|مغلق|msdod|retir|انسحب|nsse7b|rempli|pleine|عامرة|3amrin|rangé|مرتبة|m9adin|inutile|choc|abandonn|plus de candidat|مطوية|مدبر|annul|truqu|flous|الفلوس|dirham|معبأة|deux fois|pile|ne vous|ma tmchiw|ما تمشيوش|بلاصة|tdi3o|sanadi9|machi normal|معبأ|مسبقا|ma tsawtou|organisée|enquêter|kay3tiw|كيعطيو|تأجلت"
INFO = r"taux|participation|horaire|ouvert|نسبة|مفتوحة|résultat|النتائج|النتيجة|dépouillement|فرز|farz|natija|carte|البطاقة|observateur|listes électorales|débat|meeting|m7loul|mocharaka|tnsawch|اللجنة"


def fake_embed(texts):
    import numpy as np
    out = []
    for t in texts:
        low = t.lower()
        v = np.zeros(384)
        v[0] = 3.0 if re.search(RUMOR, low) else 0
        v[1] = 3.0 if re.search(INFO, low) and not v[0] else 0
        v[2] = 3.0 if not (v[0] or v[1]) else 0
        for i, pat in enumerate(TOPICS):
            if re.search(pat, low):
                v[3 + i] = 4.0
        h = hashlib.sha256(t.encode()).digest()
        v[10:10 + len(h)] = [b / 255 * 0.6 for b in h]
        out.append(v / np.linalg.norm(v))
    return np.stack(out)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Out:
    def __init__(self):
        self.value = None

    def set(self, v):
        self.value = v


def main():
    import azure.functions as func
    from fastapi.testclient import TestClient

    # --- VM du modèle
    sys.path.insert(0, str(ROOT / "azure/vm-modele"))
    import server
    server.embed = fake_embed
    server.API_KEY = os.environ["VM_API_KEY"]
    import train
    train.embed = fake_embed
    train.main()
    server.startup()
    vm = TestClient(server.app)

    # --- Azure Functions
    sys.path.insert(0, str(ROOT / "azure/fonctions"))
    import firestore
    import notif
    firestore.set_doc = lambda c, i, d: LOG["firestore"].append(("set", c, i, d))
    firestore.add_doc = lambda c, d: LOG["firestore"].append(("add", c, d))
    notif.telegram = lambda text: LOG["telegram"].append(text) or 200
    notif.email = lambda subject, text, attachment=None, to=None: LOG["email"].append((subject, attachment and attachment[0])) or 201
    fa = load("function_app", ROOT / "azure/fonctions/function_app.py")
    fa.analyze_text = lambda text: vm.post("/analyze", json={"text": text},
                                           headers={"X-Api-Key": os.environ["VM_API_KEY"]}).json()
    from db import db
    with db() as cur:
        cur.execute("TRUNCATE verdicts, publications, rumeurs")
    f_analyze = fa.analyze.build().get_user_function()
    f_score = fa.score.build().get_user_function()
    f_alerte = fa.alerte.build().get_user_function()
    f_rapport = fa.rapport.build().get_user_function()
    lock = threading.Lock()

    def run_analyze(body: bytes):
        with lock:
            clusters = Out()
            res = f_analyze(func.HttpRequest("POST", "/api/analyze", body=body, headers={}), clusters)
            LOG["analyses"].append(json.loads(res.get_body()))
            if clusters.value:                                  # file « clusters » -> score
                alertes = Out()
                f_score(func.QueueMessage(body=clusters.value.encode()), alertes)
                if alertes.value:                               # file « alertes » -> alerte
                    f_alerte(func.QueueMessage(body=alertes.value.encode()))
            return res

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, res):
            self.send_response(res.status_code)
            self.send_header("Content-Type", res.mimetype or "application/json")
            self.end_headers()
            self.wfile.write(res.get_body())

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path.startswith("/api/analyze"):
                self._send(run_analyze(body))
            elif self.path.startswith("/__log"):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps(LOG, ensure_ascii=False, default=str).encode())

        def do_GET(self):
            if self.path.startswith("/api/rapport"):
                q = dict(p.split("=") for p in self.path.split("?", 1)[1].split("&")) if "?" in self.path else {}
                with lock:
                    self._send(f_rapport(func.HttpRequest("GET", "/api/rapport", body=b"", params=q)))
            elif self.path.startswith("/__log"):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps(LOG, ensure_ascii=False, default=str).encode())

    # --- API App Service (port 8000), jeton Firebase simulé
    api = load("api_app", ROOT / "azure/api/app.py")
    api.verify_fact_checker = lambda: ("fact.checkeur@test.ma"
                                       if api.request.headers.get("Authorization") == "Bearer jeton-test" else None)
    threading.Thread(target=lambda: api.app.run(port=8000, use_reloader=False), daemon=True).start()
    print("Azure simulé prêt : fonctions :7071, API :8000", flush=True)
    HTTPServer(("127.0.0.1", 7071), H).serve_forever()


if __name__ == "__main__":
    main()
