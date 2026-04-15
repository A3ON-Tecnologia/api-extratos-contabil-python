from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse

from app.services.gmail_service import GmailService

router = APIRouter(prefix="/gmail", tags=["gmail"])
service = GmailService()


@router.get("/", response_class=HTMLResponse)
async def gmail_index(request: Request):
    auth = service.is_authenticated()
    html = f"""
    <html><body>
      <h1>Gmail Integration</h1>
      <p>Authenticated: {auth}</p>
      <p><a href=\"/gmail/auth\">Authenticate with Google</a></p>
      <p><a href=\"/gmail/poll\">Trigger poll (download attachments)</a></p>
      <p>Configure GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_OAUTH_REDIRECT in .env</p>
    </body></html>
    """
    return HTMLResponse(content=html)


@router.get("/auth")
async def gmail_auth():
    url, state = service.get_auth_url()
    return RedirectResponse(url)


@router.get("/oauth/callback")
async def gmail_callback(code: str = None):
    if not code:
        return JSONResponse({"error": "missing code"}, status_code=400)
    try:
        token_data = service.fetch_token_from_code(code)
        return JSONResponse({"status": "ok", "token_saved": True})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/poll")
async def gmail_poll(q: str = "has:attachment", max: int = 20):
    try:
        saved = service.fetch_and_save_attachments(query=q, max_results=max)
        return JSONResponse({"saved": saved, "count": len(saved)})
    except Exception as e:
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)
