import csv
import json
import os
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

MODEL_NAME = os.environ.get("MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
MODEL_DIR = Path(os.environ.get("MODEL_DIR", "/opt/electoradar/model"))
TRAIN_CSV = Path(os.environ.get("TRAIN_CSV", "/opt/electoradar/data/entrainement.csv"))
API_KEY = os.environ.get("API_KEY", "")
RUMOR_MIN_PROBA = float(os.environ.get("RUMOR_MIN_PROBA", "0.40"))

app = FastAPI(title="ElectoRadar — modèle NLP")

_encoder = None
_classifier = None
_prototypes = None
_labels = None
_started = time.time()


def encoder():
    global _encoder
    if _encoder is None:
        from fastembed import TextEmbedding
        _encoder = TextEmbedding(MODEL_NAME, cache_dir=str(MODEL_DIR / "cache"), threads=2)
    return _encoder


def embed(texts):
    vecs = np.array(list(encoder().embed(list(texts), batch_size=16)), dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def load_classifier():
    global _classifier, _prototypes, _labels
    path = MODEL_DIR / "classifier.joblib"
    if path.exists():
        import joblib
        _classifier = joblib.load(path)
        _labels = list(_classifier.classes_)
        return "classifieur entraîné (train.py)"
    rows = list(csv.DictReader(open(TRAIN_CSV, encoding="utf-8")))
    vecs = embed(r["texte"] for r in rows)
    _labels = sorted({r["label"] for r in rows})
    _prototypes = np.stack([
        vecs[[i for i, r in enumerate(rows) if r["label"] == lab]].mean(axis=0) for lab in _labels
    ])
    _prototypes /= np.linalg.norm(_prototypes, axis=1, keepdims=True)
    return "prototypes (repli, sans entraînement)"


def classify(vec):
    if _classifier is not None:
        proba = _classifier.predict_proba(vec.reshape(1, -1))[0]
    else:
        sims = _prototypes @ vec
        e = np.exp(sims * 10)
        proba = e / e.sum()
    return {lab: float(round(p, 4)) for lab, p in zip(_labels, proba)}


MODE = None


@app.on_event("startup")
def startup():
    global MODE
    MODE = load_classifier()


class AnalyzeIn(BaseModel):
    text: str


def check_key(key):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="clé API invalide")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME, "classification": MODE,
            "labels": _labels, "uptime_s": int(time.time() - _started)}


class SimilarityIn(BaseModel):
    a: str
    b: str


@app.post("/similarity")
def similarity(body: SimilarityIn, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    va, vb = embed([body.a, body.b])
    return {"similarite": round(float(va @ vb), 4)}


@app.post("/analyze")
def analyze(body: AnalyzeIn, x_api_key: str = Header(default="")):
    check_key(x_api_key)
    t0 = time.time()
    vec = embed([body.text])[0]
    scores = classify(vec)
    label = max(scores, key=scores.get)
    return {
        "embedding": [round(float(x), 6) for x in vec],
        "label": label,
        "scores": scores,
        "is_election": label in ("rumeur", "info_electorale"),
        "is_rumor": scores.get("rumeur", 0) >= RUMOR_MIN_PROBA,
        "latency_ms": int((time.time() - t0) * 1000),
    }
