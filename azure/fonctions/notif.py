import base64
import json
import os
import urllib.request


def _post(url, payload, headers=None):
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status


def telegram(text: str):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return "non configuré"
    return _post(f"https://api.telegram.org/bot{token}/sendMessage",
                 {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True})


def email(subject: str, text: str, attachment: tuple[str, bytes] | None = None, to: str | None = None):
    key, sender = os.environ.get("BREVO_API_KEY"), os.environ.get("MAIL_FROM")
    to = to or os.environ.get("ALERT_EMAIL")
    if not (key and sender and to):
        return "non configuré"
    payload = {"sender": {"email": sender, "name": "ElectoRadar"},
               "to": [{"email": a.strip()} for a in to.split(",")],
               "subject": subject, "textContent": text}
    if attachment:
        payload["attachment"] = [{"name": attachment[0], "content": base64.b64encode(attachment[1]).decode()}]
    return _post("https://api.brevo.com/v3/smtp/email", payload, {"api-key": key, "accept": "application/json"})
