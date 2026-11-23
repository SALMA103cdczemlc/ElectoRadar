import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

import google_auth

SCOPE = "https://www.googleapis.com/auth/datastore"


def _value(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, datetime):
        return {"timestampValue": v.isoformat().replace("+00:00", "Z")}
    if isinstance(v, (list, tuple)):
        return {"arrayValue": {"values": [_value(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: _value(x) for k, x in v.items()}}}
    return {"stringValue": str(v)}


def _base():
    project = os.environ.get("FIREBASE_PROJECT") or google_auth.service_account()["project_id"]
    return f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"


def _request(method, url, body):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {google_auth.access_token(SCOPE)}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def set_doc(collection: str, doc_id: str, data: dict):
    return _request("PATCH", f"{_base()}/{collection}/{urllib.parse.quote(doc_id)}",
                    {"fields": {k: _value(v) for k, v in data.items()}})


def add_doc(collection: str, data: dict):
    return _request("POST", f"{_base()}/{collection}", {"fields": {k: _value(v) for k, v in data.items()}})


def enabled() -> bool:
    return bool(os.environ.get("FIREBASE_SA_JSON"))


def clear(collection: str):
    n = 0
    while True:
        req = urllib.request.Request(f"{_base()}/{collection}?pageSize=300",
                                     headers={"Authorization": f"Bearer {google_auth.access_token(SCOPE)}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            docs = json.loads(resp.read()).get("documents", [])
        if not docs:
            return n
        for d in docs:
            dreq = urllib.request.Request(f"https://firestore.googleapis.com/v1/{d['name']}", method="DELETE",
                                          headers={"Authorization": f"Bearer {google_auth.access_token(SCOPE)}"})
            urllib.request.urlopen(dreq, timeout=15).read()
            n += 1
