"""
Servico Gmail: autenticação OAuth2, armazenamento de credenciais e download de anexos.
"""
from __future__ import annotations

import json
import logging
import os
import base64
from pathlib import Path
from typing import List

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from app.config import get_settings
from app.services.storage_service import StorageService
from app.schemas.client import MatchResult

logger = logging.getLogger(__name__)

TOKENS_DIR = Path(__file__).parent.parent / "tokens"
TOKENS_DIR.mkdir(parents=True, exist_ok=True)
CREDENTIALS_FILE = TOKENS_DIR / "gmail_credentials.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailService:
    def __init__(self):
        self.settings = get_settings()
        self.storage = StorageService()

    def _client_config(self) -> dict:
        client_id = getattr(self.settings, "gmail_client_id", None)
        client_secret = getattr(self.settings, "gmail_client_secret", None)
        redirect = getattr(self.settings, "gmail_oauth_redirect", None)
        if not client_id or not client_secret:
            raise RuntimeError("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in config/.env")

        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect] if redirect else [],
            }
        }

    def get_auth_url(self, state: str | None = None) -> tuple[str, str]:
        cfg = self._client_config()
        flow = Flow.from_client_config(cfg, scopes=SCOPES)
        redirect = getattr(self.settings, "gmail_oauth_redirect", None)
        if redirect:
            flow.redirect_uri = redirect
        auth_url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent", state=state)
        return auth_url, state

    def fetch_token_from_code(self, code: str) -> dict:
        cfg = self._client_config()
        flow = Flow.from_client_config(cfg, scopes=SCOPES)
        redirect = getattr(self.settings, "gmail_oauth_redirect", None)
        if redirect:
            flow.redirect_uri = redirect
        flow.fetch_token(code=code)
        creds = flow.credentials
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }
        # persist
        with open(CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(token_data, f)
        return token_data

    def load_credentials(self) -> Credentials | None:
        if not CREDENTIALS_FILE.exists():
            return None
        with open(CREDENTIALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        creds = Credentials(
            token=data.get("token"),
            refresh_token=data.get("refresh_token"),
            token_uri=data.get("token_uri"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            scopes=data.get("scopes"),
        )
        return creds

    def _build_service(self, creds: Credentials):
        return build("gmail", "v1", credentials=creds)

    def fetch_and_save_attachments(self, query: str = "has:attachment", max_results: int = 50) -> List[str]:
        """
        Busca mensagens que atendam a query e salva anexos na pasta apropriada.
        Retorna lista de caminhos salvos.
        """
        creds = self.load_credentials()
        if not creds:
            raise RuntimeError("Gmail credentials not found. Authenticate first via /gmail/auth")

        service = self._build_service(creds)
        saved_paths: List[str] = []

        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        messages = results.get("messages", []) or []
        for m in messages:
            try:
                msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
                payload = msg.get("payload", {})
                parts = payload.get("parts", []) or []
                # recursive parts
                stack = list(parts)
                while stack:
                    part = stack.pop()
                    if part.get("parts"):
                        stack.extend(part.get("parts"))
                        continue
                    filename = part.get("filename")
                    body = part.get("body", {})
                    if filename and body.get("attachmentId"):
                        att_id = body.get("attachmentId")
                        att = service.users().messages().attachments().get(userId="me", messageId=m["id"], id=att_id).execute()
                        data = base64.urlsafe_b64decode(att.get("data", ""))
                        # save using storage service; pass a default MatchResult (unidentified)
                        match = MatchResult()
                        saved = self.storage.save_file(data, match, filename, module="extratos")
                        saved_paths.append(saved[0])
            except Exception as e:
                logger.exception(f"Erro ao processar mensagem {m}: {e}")
                continue

        return saved_paths

    def is_authenticated(self) -> bool:
        return CREDENTIALS_FILE.exists()
