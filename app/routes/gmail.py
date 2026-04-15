from __future__ import annotations

from fastapi import APIRouter, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.services.gmail_service import GmailService
from app.config import get_settings

router = APIRouter(prefix="/gmail", tags=["gmail"])
service = GmailService()

class ProcessAttachmentRequest(BaseModel):
    message_id: str
    attachment_id: str
    filename: str
    mes: int | None = None
    ano: int | None = None

@router.get("/", response_class=HTMLResponse)
async def gmail_dashboard(request: Request):
    """Serve o novo Dashboard do Gmail com navbar injetada."""
    from pathlib import Path
    import re

    template_path = Path(__file__).parent.parent / "templates" / "gmail_dashboard.html"

    if not template_path.exists():
        return HTMLResponse(
            content=f"<html><body><h1>Erro</h1><p>Template não encontrado em {template_path}</p></body></html>",
            status_code=500
        )

    # Injeta navbar usando a função helper do main.py
    from app.main import _render_template_with_navbar

    return HTMLResponse(
        content=_render_template_with_navbar(
            template_path,
            active_main="gmail",
            show_main=True,
            show_extratos=True
        )
    )


# ============================================================
# API ENDPOINTS PARA O DASHBOARD
# ============================================================

@router.get("/api/labels")
async def get_labels():
    try:
        return service.list_labels()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/api/messages")
async def get_messages(label_id: str = Query(...)):
    try:
        return service.list_messages(label_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/api/attachments")
async def get_attachments(message_id: str = Query(...)):
    try:
        return service.get_message_attachments(message_id)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/api/process-attachment")
async def process_attachment(req: ProcessAttachmentRequest):
    try:
        result = service.process_specific_attachment(
            message_id=req.message_id,
            attachment_id=req.attachment_id,
            filename=req.filename,
            ano=req.ano,
            mes=req.mes
        )
        return {"status": "success", "result": result}
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


# ============================================================
# ENDPOINTS LEGADOS / UTILITARIOS
# ============================================================

@router.get("/status")
async def gmail_status():
    settings = get_settings()
    return {
        "authenticated": service.is_authenticated(),
        "delegated_user": settings.gmail_delegated_user,
        "json_path": str(settings.gmail_json_path),
        "json_exists": settings.gmail_json_path.exists() if settings.gmail_json_path else False
    }

@router.get("/poll")
async def gmail_poll(
    q: str = Query("has:attachment", description="Gmail search query"),
    max_results: int = Query(20, alias="max", description="Max messages to check")
):
    try:
        saved = service.fetch_and_save_attachments(query=q, max_results=max_results)
        return JSONResponse({
            "status": "success",
            "saved_count": len(saved),
            "files": saved
        })
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@router.get("/poll/fluxo")
async def gmail_poll_fluxo(
    max_per_label: int = Query(50, alias="max", description="Max messages to check per sub-label")
):
    try:
        results = service.poll_fluxo_pdf(max_messages_per_label=max_per_label)
        return JSONResponse({
            "status": "success",
            "processed_count": len(results),
            "results": results
        })
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/api/dashboard-stats")
async def get_dashboard_stats():
    """Retorna estatísticas do dashboard para display."""
    from datetime import datetime
    try:
        labels = service.list_labels()
        fluxo_labels = [l for l in labels if l.get("name", "").startswith("FLUXO PDF")]

        return {
            "status": "success",
            "total_labels": len(labels),
            "fluxo_pdf_labels": len(fluxo_labels),
            "authenticated": service.is_authenticated(),
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
