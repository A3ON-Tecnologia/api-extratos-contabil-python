"""
Servico Gmail: autenticação via Service Account com delegação e download de anexos.
"""
from __future__ import annotations

import logging
import base64
import re
from pathlib import Path
from typing import List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.config import get_settings
from app.services.storage_service import StorageService
from app.services.pdf_service import PDFService
from app.services.llm_service import LLMService
from app.services.matching_service import MatchingService
from app.services.client_service import ClientService
from app.schemas.client import MatchResult

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailService:
    def __init__(self):
        self.settings = get_settings()
        self.storage = StorageService()
        self._pdf_service = PDFService()
        self._llm_service = LLMService()
        self._matching_service = MatchingService(ClientService())

    def load_credentials(self) -> service_account.Credentials:
        json_path = self.settings.gmail_json_path
        delegated_user = self.settings.gmail_delegated_user

        if not json_path or not Path(json_path).exists():
            raise RuntimeError(f"GMAIL_JSON_PATH not found or not set: {json_path}")

        creds = service_account.Credentials.from_service_account_file(
            str(json_path),
            scopes=SCOPES,
            subject=delegated_user
        )
        return creds

    def _build_service(self, creds: service_account.Credentials):
        return build("gmail", "v1", credentials=creds)

    def list_labels(self) -> List[Dict[str, Any]]:
        """Lista todos os marcadores da conta."""
        creds = self.load_credentials()
        service = self._build_service(creds)
        results = service.users().labels().list(userId="me").execute()
        return results.get("labels", [])

    def list_messages(self, label_id: str, max_results: int = 50) -> List[Dict[str, Any]]:
        """Lista mensagens de um marcador específico."""
        creds = self.load_credentials()
        service = self._build_service(creds)
        
        results = service.users().messages().list(
            userId="me", labelIds=[label_id], maxResults=max_results
        ).execute()
        
        messages = []
        for m_info in results.get("messages", []):
            msg = service.users().messages().get(userId="me", id=m_info["id"], format="metadata", metadataHeaders=["Subject", "From", "Date"]).execute()
            
            headers = msg.get("payload", {}).get("headers", [])
            subject = next((h["value"] for h in headers if h["name"] == "Subject"), "(Sem Assunto)")
            sender = next((h["value"] for h in headers if h["name"] == "From"), "(Desconhecido)")
            date = next((h["value"] for h in headers if h["name"] == "Date"), "")
            
            messages.append({
                "id": msg["id"],
                "subject": subject,
                "from": sender,
                "date": date,
                "snippet": msg.get("snippet", "")
            })
            
        return messages

    def get_message_attachments(self, message_id: str) -> List[Dict[str, Any]]:
        """Lista detalhes dos anexos de uma mensagem."""
        creds = self.load_credentials()
        service = self._build_service(creds)
        
        msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = msg.get("payload", {})
        attachments = []
        
        def find_attachments(parts):
            for part in parts:
                if part.get("parts"):
                    find_attachments(part["parts"])
                if part.get("filename") and part.get("body", {}).get("attachmentId"):
                    attachments.append({
                        "message_id": message_id,
                        "attachment_id": part["body"]["attachmentId"],
                        "filename": part["filename"],
                        "mimeType": part.get("mimeType"),
                        "size": part.get("body", {}).get("size")
                    })
        
        if "parts" in payload:
            find_attachments(payload["parts"])
            
        return attachments

    def process_specific_attachment(self, message_id: str, attachment_id: str, filename: str, ano: int | None = None, mes: int | None = None) -> Dict[str, Any]:
        """Baixa e processa um anexo específico."""
        creds = self.load_credentials()
        service = self._build_service(creds)
        
        att = service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        
        data = base64.urlsafe_b64decode(att.get("data", ""))
        
        # Processamento Inteligente
        text = self._pdf_service.extract_text(data, filename, 1)
        extraction = self._llm_service.extract_info_with_fallback(text, data)
        match_result = self._matching_service.match(extraction)
        
        saved_path, saved_ano, saved_mes = self.storage.save_file(
            pdf_data=data,
            match_result=match_result,
            original_filename=filename,
            tipo_documento=extraction.tipo_documento,
            banco=extraction.banco,
            conta_extrato=extraction.conta,
            module="extratos",
            ano=ano,
            mes=mes,
            source="gmail"
        )
        
        return {
            "filename": filename,
            "path": saved_path,
            "ano": saved_ano,
            "mes": saved_mes,
            "cliente": match_result.cliente.nome if match_result.identificado else "NAO IDENTIFICADO",
            "status": "sucesso" if match_result.identificado else "nao_identificado"
        }

    def fetch_and_save_attachments(self, query: str = "has:attachment", max_results: int = 50) -> List[str]:
        """Mantido para compatibilidade com outros endpoints."""
        # Logica original simplificada...
        creds = self.load_credentials()
        service = self._build_service(creds)
        saved_paths = []
        results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
        for m in results.get("messages", []):
            atts = self.get_message_attachments(m["id"])
            for a in atts:
                res = self.process_specific_attachment(m["id"], a["attachment_id"], a["filename"])
                saved_paths.append(res["path"])
        return saved_paths

    def poll_fluxo_pdf(self, max_messages_per_label: int = 50) -> List[Dict[str, Any]]:
        """Mantido e otimizado para usar os novos metodos granulares."""
        labels = self.list_labels()
        pattern = re.compile(r"FLUXO PDF/(\d{2})-(\d{4})", re.IGNORECASE)
        results = []
        
        for label in labels:
            match = pattern.match(label["name"])
            if match:
                mes, ano = int(match.group(1)), int(match.group(2))
                msgs = self.list_messages(label["id"], max_results=max_messages_per_label)
                for msg in msgs:
                    atts = self.get_message_attachments(msg["id"])
                    for att in atts:
                        res = self.process_specific_attachment(msg["id"], att["attachment_id"], att["filename"], ano=ano, mes=mes)
                        res["label"] = label["name"]
                        results.append(res)
        return results

    def download_attachment_to_folder(
        self,
        message_id: str,
        attachment_id: str,
        filename: str,
        pasta_destino: str,
    ) -> dict:
        """Baixa anexo diretamente para subpasta de EXTRATOS (sem LLM/matching)."""
        # Baixa o arquivo do Gmail
        creds = self.load_credentials()
        svc = self._build_service(creds)
        att = svc.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
        data = base64.urlsafe_b64decode(att.get("data", ""))

        # Valida pasta destino (deve ser subpasta de watch_folder_path)
        watch_path = Path(self.settings.watch_folder_path)
        target_dir = watch_path / pasta_destino
        if not target_dir.exists():
            target_dir.mkdir(parents=True, exist_ok=True)

        # Salva na pasta escolhida, garantindo nome único
        dest_file = target_dir / filename
        if dest_file.exists():
            stem = dest_file.stem
            suffix = dest_file.suffix
            i = 1
            while dest_file.exists():
                dest_file = target_dir / f"{stem}_{i}{suffix}"
                i += 1

        dest_file.write_bytes(data)
        logger.info(f"Anexo salvo: {dest_file}")
        return {
            "path": str(dest_file),
            "pasta": pasta_destino,
            "filename": dest_file.name
        }

    def is_authenticated(self) -> bool:
        json_path = self.settings.gmail_json_path
        return bool(json_path and Path(json_path).exists())
