import os
import smtplib
from email.message import EmailMessage
from typing import Optional
import ssl

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


app = FastAPI()


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name, default)
    return v

# === Mesh TLS настройки для исходящих запросов mailer -> notes-services ===
MESH_CA = _env("MESH_CA", "/mesh/certs/ca.crt")
MESH_CERT = _env("MESH_CERT", "/mesh/certs/mailer.crt")
MESH_KEY = _env("MESH_KEY", "/mesh/certs/mailer.key")

def _mesh_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=MESH_CA)
    ctx.load_cert_chain(MESH_CERT, MESH_KEY)
    return ctx

_MESH_CTX = _mesh_ssl_context()
_NOTES_TRANSPORT = httpx.AsyncHTTPTransport(verify=_MESH_CTX)


# NOTES_BACKENDS: "https://service1:9443,https://service2:9443"
NOTES_BACKENDS = [x.strip() for x in _env("NOTES_BACKENDS", "").split(",") if x.strip()]

# === SMTP настройки ===
SMTP_HOST = _env("SMTP_HOST", "mailhog")
SMTP_PORT = int(_env("SMTP_PORT", "1025"))
SMTP_FROM = _env("SMTP_FROM", "notes@local")
DEFAULT_TO = _env("DEFAULT_TO", "test@local")
SMTP_USER = _env("SMTP_USER", "")
SMTP_PASS = _env("SMTP_PASS", "")
SMTP_STARTTLS = _env("SMTP_STARTTLS", "0") == "1"


class SendRequest(BaseModel):
    to: Optional[str] = None


@app.get("/health")
def health():
    return {"status": "ok"}

async def fetch_note(note_id: str) -> dict:
    if not NOTES_BACKENDS:
        raise RuntimeError("NOTES_BACKENDS is empty")

    last_err: Exception | None = None

    async with httpx.AsyncClient(
        transport=_NOTES_TRANSPORT,
        timeout=5.0,
        trust_env=False,
    ) as client:
        for base in NOTES_BACKENDS:
            try:
                r = await client.get(f"{base}/notes/{note_id}")
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    raise HTTPException(status_code=404, detail="note not found")
                last_err = RuntimeError(f"unexpected status {r.status_code}: {r.text}")
            except HTTPException:
                raise
            except Exception as e:
                last_err = e

    raise HTTPException(status_code=502, detail=f"cannot reach notes backends: {last_err}")


def send_email(to_addr: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = SMTP_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
        if SMTP_STARTTLS:
            s.starttls()
        if SMTP_USER and SMTP_PASS:
            s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)


@app.post("/mail/send/{note_id}")
async def send_note(note_id: str, payload: SendRequest):
    note = await fetch_note(note_id)

    to_addr = payload.to or DEFAULT_TO
    subject = f"Note: {note.get('title', '')}".strip() or "Note"
    body = (
        f"ID: {note.get('id')}\n"
        f"Title: {note.get('title')}\n"
        f"Description: {note.get('description')}\n"
        f"CreatedAt: {note.get('created_at')}\n"
        f"UpdatedAt: {note.get('updated_at')}\n"
    )

    try:
        send_email(to_addr, subject, body)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"smtp error: {e}")

    return {"status": "sent", "to": to_addr, "note_id": note_id}
