import csv
import json
import os
import time
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import cross_val_score

from server import MODEL_DIR, MODEL_NAME, TRAIN_CSV, embed


def main():
    rows = list(csv.DictReader(open(TRAIN_CSV, encoding="utf-8")))
    texts = [r["texte"] for r in rows]
    y = np.array([r["label"] for r in rows])
    print(f"{len(rows)} exemples, classes : {sorted(set(y))}")

    t0 = time.time()
    X = embed(texts)
    print(f"Vecteurs calculés en {time.time() - t0:.1f} s, dimension {X.shape[1]}")

    clf = LogisticRegression(max_iter=2000, C=4.0)
    acc = cross_val_score(clf, X, y, cv=5).mean()
    print(f"Précision moyenne en validation croisée (5 plis) : {acc:.0%}")

    clf.fit(X, y)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(clf, MODEL_DIR / "classifier.joblib")
    (MODEL_DIR / "metadata.json").write_text(json.dumps({
        "model": MODEL_NAME, "exemples": len(rows), "precision_cv": round(float(acc), 3),
        "entraine_le": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=2))
    print(f"Classifieur enregistré dans {MODEL_DIR / 'classifier.joblib'}")


if __name__ == "__main__":
    main()
