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


@router.get("/auth", response_class=HTMLResponse)
async def gmail_auth_status():
    """Página de status e configuração do Gmail."""
    from app.utils.template import render_tech_navbar

    settings = get_settings()
    is_authenticated = service.is_authenticated()
    json_path = settings.gmail_json_path
    delegated_user = settings.gmail_delegated_user
    json_filename = Path(json_path).name if json_path else "N/A"

    navbar_html = render_tech_navbar(
        active_main="gmail-auth",
        show_main=True,
        show_extratos=True,
    )

    status_color = "#10b981" if is_authenticated else "#ef4444"
    status_text = "✓ Sim" if is_authenticated else "✗ Não"

    html = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Gmail Auth Status</title>
        <link rel="stylesheet" href="/static/css/tech-navbar.css">
        <style>
            :root {{
                --bg-primary: #0a0f14;
                --bg-secondary: #0c1622;
                --bg-card: #111b2a;
                --accent-primary: #12c2e9;
                --text-primary: #f8fafc;
                --text-secondary: #94a3b8;
                --border-color: rgba(34, 211, 238, 0.25);
            }}

            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}

            body {{
                font-family: 'Inter', sans-serif;
                background: var(--bg-primary);
                color: var(--text-primary);
                overflow-x: hidden;
            }}

            .page-wrapper {{
                margin-left: 70px;
                transition: margin-left 0.2s ease-out;
                min-height: 100vh;
                padding: 2rem 1rem;
            }}

            .page-wrapper.navbar-expanded {{
                margin-left: 240px;
            }}

            .container {{
                max-width: 900px;
                margin: 0 auto;
            }}

            h1 {{
                color: var(--accent-primary);
                margin-bottom: 2rem;
            }}

            h2 {{
                color: var(--text-primary);
                margin-bottom: 1rem;
                margin-top: 0;
            }}

            .status-card {{
                background: var(--bg-card);
                border: 1px solid var(--border-color);
                border-radius: 0.5rem;
                padding: 1.5rem;
                margin-bottom: 2rem;
            }}

            .status-item {{
                font-size: 1rem;
                margin: 0.75rem 0;
            }}

            .status-item strong {{
                color: var(--text-primary);
            }}

            .status-value {{
                color: {status_color};
                font-weight: bold;
                margin-left: 0.5rem;
            }}

            code {{
                background: var(--bg-secondary);
                padding: 0.25rem 0.5rem;
                border-radius: 0.25rem;
                font-size: 0.9rem;
            }}

            ol, ul {{
                color: var(--text-secondary);
                line-height: 1.8;
                margin-left: 1.5rem;
            }}

            li {{
                margin-bottom: 0.75rem;
            }}

            a {{
                color: var(--accent-primary);
                text-decoration: none;
            }}

            a:hover {{
                text-decoration: underline;
            }}

            .back-link {{
                display: inline-block;
                margin-top: 2rem;
                padding: 0.5rem 1rem;
                border: 1px solid var(--border-color);
                border-radius: 0.3rem;
                color: var(--accent-primary);
                text-decoration: none;
            }}

            .back-link:hover {{
                background: rgba(18, 194, 233, 0.1);
            }}
        </style>
    </head>
    <body>
        {navbar_html}

        <div class="page-wrapper" id="mainContent">
            <div class="container">
                <h1>Gmail Authentication Status</h1>

                <div class="status-card">
                    <h2>Status</h2>
                    <div class="status-item">
                        <strong>Autenticado:</strong>
                        <span class="status-value">{status_text}</span>
                    </div>
                    <div class="status-item">
                        <strong>JSON Path:</strong>
                        <code>{str(json_path) if json_path else 'Não configurado'}</code>
                    </div>
                    <div class="status-item">
                        <strong>Usuário Delegado:</strong>
                        <code>{delegated_user or 'Não configurado'}</code>
                    </div>
                    <div class="status-item" style="margin-top: 1rem; color: var(--text-secondary); font-size: 0.9rem;">
                        Arquivo JSON: <code>{json_filename}</code>
                    </div>
                </div>

                <div class="status-card">
                    <h2>Configuração</h2>
                    <ol>
                        <li>Criar um projeto no <a href="https://console.cloud.google.com" target="_blank">Google Cloud Console</a></li>
                        <li>Ativar Gmail API no projeto</li>
                        <li>Criar Service Account com delegação de domínio</li>
                        <li>Baixar arquivo JSON das credenciais</li>
                        <li>Configurar variáveis no <code>.env</code>:
                            <ul style="margin-top: 0.5rem;">
                                <li><code>GMAIL_JSON_PATH</code>: caminho do arquivo JSON</li>
                                <li><code>GMAIL_DELEGATED_USER</code>: email da conta delegada</li>
                            </ul>
                        </li>
                        <li>Reiniciar a aplicação</li>
                    </ol>
                </div>

                <div class="status-card">
                    <h2>Links Úteis</h2>
                    <ul>
                        <li><a href="https://developers.google.com/gmail/api" target="_blank">Gmail API Documentation</a></li>
                        <li><a href="https://console.cloud.google.com" target="_blank">Google Cloud Console</a></li>
                        <li><a href="/gmail">← Voltar ao Gmail Dashboard</a></li>
                    </ul>
                </div>
            </div>
        </div>

        <script src="/static/js/tech-navbar.js"></script>
    </body>
    </html>
    """

    return html


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
