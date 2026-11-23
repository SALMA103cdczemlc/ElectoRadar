import base64
import json
import os
import time
import urllib.parse
import urllib.request

import rsa
from pyasn1.codec.der import decoder as der_decoder

_SA = None
_TOKENS = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def service_account() -> dict:
    global _SA
    if _SA is None:
        _SA = json.loads(base64.b64decode(os.environ["FIREBASE_SA_JSON"]).decode())
    return _SA


def _private_key(pem: str) -> rsa.PrivateKey:
    body = "".join(l for l in pem.strip().splitlines() if "PRIVATE KEY" not in l)
    info, _ = der_decoder.decode(base64.b64decode(body))
    return rsa.PrivateKey.load_pkcs1(bytes(info[2]), format="DER")


def access_token(scope: str) -> str:
    cached = _TOKENS.get(scope)
    if cached and time.time() < cached[1] - 60:
        return cached[0]
    sa = service_account()
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT", "kid": sa.get("private_key_id", "")}
    claims = {"iss": sa["client_email"], "scope": scope, "aud": "https://oauth2.googleapis.com/token",
              "iat": now, "exp": now + 3600}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}"
    sig = rsa.sign(signing_input.encode(), _private_key(sa["private_key"]), "SHA-256")
    data = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                   "assertion": f"{signing_input}.{_b64url(sig)}"}).encode()
    with urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data),
                                timeout=15) as r:
        tok = json.loads(r.read())
    _TOKENS[scope] = (tok["access_token"], now + int(tok.get("expires_in", 3600)))
    return tok["access_token"]
