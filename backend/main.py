import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, Optional

import httpx
from fastapi import BackgroundTasks, Body, Depends, FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

import chat_service
import notion_client
import notion_oauth
import repositories
import scheduler
import sync_service
import sync_status
from db import get_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.start_scheduler()
    yield
    scheduler.shutdown_scheduler()


app = FastAPI(lifespan=lifespan)

# The side panel runs as an extension page (chrome-extension://<id>), and the
# extension ID varies per install/dev-load, so we can't allowlist one origin.
# No cookies/credentials are used (the app_user_id is passed explicitly), so
# a wildcard origin doesn't expose anything sensitive.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Transient, in-memory record of failed OAuth attempts, keyed by app_user_id,
# so the frontend's status poll can surface an error immediately instead of
# waiting for its timeout. This is fine to lose on server restart.
_pending_errors: Dict[str, str] = {}

ERROR_MESSAGES = {
    "access_denied": "You declined the Notion connection request.",
    "invalid_state": "The connection request expired or was invalid. Please try again.",
    "missing_code": "Notion did not return an authorization code.",
    "token_exchange_failed": "Notion rejected the authorization code.",
    "network_error": "Could not reach Notion. Check your connection and try again.",
    "missing_email": (
        "Couldn't read your email from Notion. Enable \"Read user information including "
        "email addresses\" for this integration in the Notion Developer Portal and try again."
    ),
    "email_already_linked": "This Notion account is already connected from a different Notion Lens installation.",
}


def _parse_app_user_id(app_user_id: str) -> Optional[uuid.UUID]:
    if not notion_oauth.is_valid_app_user_id(app_user_id):
        return None
    try:
        return uuid.UUID(app_user_id)
    except ValueError:
        return None


@app.get("/auth/notion/login")
def notion_login(app_user_id: str = Query(..., min_length=1, max_length=128)):
    if not notion_oauth.is_valid_app_user_id(app_user_id):
        return JSONResponse({"error": "invalid_app_user_id"}, status_code=400)

    state = notion_oauth.create_state(app_user_id)
    return {"authorize_url": notion_oauth.build_authorize_url(state)}


@app.get("/auth/notion/callback")
async def notion_callback(request: Request, session: Session = Depends(get_session)):
    params = request.query_params
    error = params.get("error")
    state = params.get("state")
    code = params.get("code")

    app_user_id: Optional[str] = notion_oauth.verify_state(state) if state else None

    if error:
        if app_user_id:
            _pending_errors[app_user_id] = "access_denied"
        return RedirectResponse("/auth/notion/complete?status=error&reason=access_denied")

    if not app_user_id:
        return RedirectResponse("/auth/notion/complete?status=error&reason=invalid_state")

    user_uuid = _parse_app_user_id(app_user_id)
    if not user_uuid:
        return RedirectResponse("/auth/notion/complete?status=error&reason=invalid_state")

    if not code:
        _pending_errors[app_user_id] = "missing_code"
        return RedirectResponse("/auth/notion/complete?status=error&reason=missing_code")

    try:
        token_data = await notion_oauth.exchange_code(code)
    except notion_oauth.NotionOAuthError as exc:
        reason = str(exc) or "token_exchange_failed"
        _pending_errors[app_user_id] = reason
        return RedirectResponse(f"/auth/notion/complete?status=error&reason={reason}")

    access_token = token_data["access_token"]

    try:
        async with httpx.AsyncClient() as client:
            email = await notion_client.get_owner_email(client, access_token)
    except notion_client.NotionAPIError:
        email = None

    if not email:
        _pending_errors[app_user_id] = "missing_email"
        return RedirectResponse("/auth/notion/complete?status=error&reason=missing_email")

    try:
        repositories.upsert_app_user(session, user_uuid, email)
        repositories.upsert_connection(
            session,
            app_user_id=user_uuid,
            workspace_id=token_data.get("workspace_id"),
            workspace_name=token_data.get("workspace_name"),
            workspace_icon=token_data.get("workspace_icon"),
            access_token=access_token,
        )
        session.commit()
    except repositories.EmailAlreadyLinkedError:
        session.rollback()
        _pending_errors[app_user_id] = "email_already_linked"
        return RedirectResponse("/auth/notion/complete?status=error&reason=email_already_linked")

    _pending_errors.pop(app_user_id, None)
    return RedirectResponse("/auth/notion/complete?status=success")


@app.get("/auth/notion/complete", response_class=HTMLResponse)
def notion_complete(status: str = "success", reason: str = ""):
    if status == "success":
        title = "Notion connected"
        message = "You can go back to the Notion Lens side panel now."
    else:
        title = "Connection failed"
        message = ERROR_MESSAGES.get(reason, "Something went wrong while connecting to Notion.")

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Notion Lens</title>
<style>
  body {{
    font-family: -apple-system, "Inter", sans-serif;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100vh;
    margin: 0;
    background: #f6f5f1;
    color: #1b1a18;
  }}
  .card {{ text-align: center; padding: 32px; max-width: 320px; }}
  h1 {{ font-size: 19px; margin: 0 0 8px; }}
  p {{ color: #726c63; font-size: 14px; margin: 0; }}
  .hint {{ font-size: 12px; color: #b4afa5; margin-top: 16px; }}
</style>
</head>
<body>
  <div class="card">
    <h1>{title}</h1>
    <p>{message}</p>
    <p class="hint">You can close this tab.</p>
  </div>
  <script>
    setTimeout(function () {{
      try {{ window.close(); }} catch (e) {{}}
    }}, 1500);
  </script>
</body>
</html>"""


@app.get("/auth/notion/status")
def notion_status(
    background_tasks: BackgroundTasks,
    app_user_id: str = Query(..., min_length=1, max_length=128),
    session: Session = Depends(get_session),
):
    error = _pending_errors.pop(app_user_id, None)
    if error:
        return {"connected": False, "error": error}

    user_uuid = _parse_app_user_id(app_user_id)
    if not user_uuid:
        return {"connected": False}

    connection = repositories.get_connection_for_user(session, user_uuid)
    if not connection:
        return {"connected": False}

    # The side panel calls this endpoint on every open, so it's the natural
    # trigger for "sync automatically once per day when the user opens the app."
    # The APScheduler sweep in scheduler.py covers users who leave it closed.
    now = datetime.now(timezone.utc)
    if sync_status.should_auto_sync(app_user_id, connection.last_synced_at, now):
        background_tasks.add_task(sync_service.run_sync, user_uuid)

    return {"connected": True, **repositories.get_public_status(session, user_uuid)}


@app.post("/notion/sync", status_code=202)
async def start_notion_sync(
    background_tasks: BackgroundTasks,
    app_user_id: str = Query(..., min_length=1, max_length=128),
    session: Session = Depends(get_session),
):
    user_uuid = _parse_app_user_id(app_user_id)
    if not user_uuid:
        return JSONResponse({"error": "invalid_app_user_id"}, status_code=400)

    connection = repositories.get_connection_for_user(session, user_uuid)
    if not connection:
        return JSONResponse({"error": "not_connected"}, status_code=409)

    if sync_status.is_running(app_user_id):
        return {"status": "already_running"}

    background_tasks.add_task(sync_service.run_sync, user_uuid)
    return {"status": "started"}


@app.get("/notion/sync/status")
def notion_sync_status(app_user_id: str = Query(..., min_length=1, max_length=128)):
    return sync_status.get_status(app_user_id)


MAX_QUESTION_LENGTH = 2000


@app.post("/notion/query")
async def notion_query(
    app_user_id: str = Query(..., min_length=1, max_length=128),
    body: dict = Body(...),
):
    user_uuid = _parse_app_user_id(app_user_id)
    if not user_uuid:
        return JSONResponse({"error": "invalid_app_user_id"}, status_code=400)

    question = (body.get("question") or "").strip()
    if not question:
        return JSONResponse({"error": "missing_question"}, status_code=400)
    if len(question) > MAX_QUESTION_LENGTH:
        return JSONResponse({"error": "question_too_long"}, status_code=400)

    history = body.get("history") or []

    try:
        result = await chat_service.answer_question(user_uuid, question, history)
    except chat_service.NotConnectedError:
        return JSONResponse({"error": "not_connected"}, status_code=409)
    except chat_service.ChatError as exc:
        return JSONResponse({"error": "generation_failed", "message": str(exc)}, status_code=502)

    return result
