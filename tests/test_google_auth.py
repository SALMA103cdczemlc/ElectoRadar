"""Vérifie la signature JWT (google_auth.py) et la requête Firestore (firestore.py) avec une clé RSA locale."""
import base64, io, json, os, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "azure/fonctions"))
from cryptography.hazmat.primitives.asymmetric import rsa as crsa, padding
from cryptography.hazmat.primitives import serialization, hashes
key = crsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
os.environ["FIREBASE_SA_JSON"] = base64.b64encode(json.dumps({"client_email": "sa@projet.iam.gserviceaccount.com",
    "private_key": pem, "private_key_id": "k1", "project_id": "projet-test"}).encode()).decode()
import firestore
from datetime import datetime, timezone
class R(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): pass
calls = []
def urlopen(req, timeout=None):
    calls.append(req)
    if "oauth2" in req.full_url:
        a = dict(x.split("=") for x in req.data.decode().split("&"))["assertion"]
        h, c, s = a.split("."); pad = lambda x: x + "=" * (-len(x) % 4)
        key.public_key().verify(base64.urlsafe_b64decode(pad(s)), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())
        assert "datastore" in json.loads(base64.urlsafe_b64decode(pad(c)))["scope"]
        return R(b'{"access_token":"t","expires_in":3600}')
    body = json.loads(req.data)
    assert req.full_url.endswith("/projects/projet-test/databases/(default)/documents/rumeurs/R-1"), req.full_url
    assert body["fields"]["score"] == {"integerValue": "93"} and body["fields"]["langues"]["arrayValue"]["values"][0] == {"stringValue": "ar"}
    assert body["fields"]["maj"]["timestampValue"].endswith("Z")
    return R(b"{}")
with mock.patch("urllib.request.urlopen", urlopen):
    firestore.set_doc("rumeurs", "R-1", {"score": 93, "langues": ["ar"], "maj": datetime.now(timezone.utc), "part": 0.5})
print("OK : JWT RS256 vérifié, requête Firestore conforme")
