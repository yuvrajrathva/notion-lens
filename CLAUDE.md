# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Notion Lens is a Chrome extension that provides a side panel UI for RAG-style querying over a user's own Notion workspace. It has two parts:

- **Extension** (repo root): `manifest.json`, `background.js`, `sidepanel.html/css/js`. Pure vanilla JS/HTML/CSS, no build step, no bundler, no package.json.
- **Backend** (`backend/`): a FastAPI service that implements the Notion OAuth 2.0 Authorization Code flow end-to-end (login, callback, token exchange, storage, status) and serves a stub RAG query endpoint.

The side panel UI (`sidepanel.js`) has a placeholder `submitQuery()` that only logs to console — the query flow is not yet wired to the backend. Notion OAuth, however, is fully wired: the "Connect Notion" button drives a real backend-brokered OAuth flow and persists the resulting access token server-side. Sync is fully wired: on click of refresh button of sync it uses notion API to fetch the dayta and sync those part which came after last sync. Chunking the content, embedding those chunked data and storing in relevant Postgres table is done.


## Commands

Run the backend locally (from `backend/`, using the venv): run_backend.sh

## File-by-file reference

Root:
- `manifest.json` — MV3 manifest: side panel entry point, permissions (`scripting`, `sidePanel`, `storage`), and `host_permissions` for the local backend.
- `background.js` — service worker; only sets the side panel to open on toolbar-icon click.
- `sidepanel.html` — side panel markup: topbar, Notion connect bar, empty-state hero, and the query input bar.
- `sidepanel.css` — all side panel styling, including the connect-bar states (disconnected/connecting/connected/error).
- `sidepanel.js` — side panel behavior: Notion connect/status-poll flow, refresh button demo, and the (still-stubbed) query submit handler.
- `.env` — real secrets (gitignored): `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `NOTION_REDIRECT_URI`, `SESSION_SECRET`, `EMBEDDER_MODEL_API_KEY` (present but not yet consumed by any code — reserved for future embedding/RAG work).

`backend/`:
- `main.py` — FastAPI app; defines the `/auth/notion/*` OAuth routes, `/notion/sync` sync new content if any and `/notion/sync/status` sync status.
- `config.py` — loads and validates required env vars (`NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `SESSION_SECRET`, `NOTION_REDIRECT_URI`) from the repo-root `.env`.
- `notion_oauth.py` — builds the Notion authorize URL, signs/verifies the CSRF-safe `state` param (HMAC, 10-min TTL), and exchanges an authorization code for a token.
- `db.py,` `models.py` — SQLAlchemy engine/session + the 4 ORM models (AppUser, NotionConnection, NotionPage, NotionChunk)
- `alembic/` — migration environment wired to Settings.DATABASE_URL
- `repositories.py` — upsert/query helpers
- `notion_client.py` — Search API pagination (with early-stop once last_edited_time <= since), recursive block-to-text extraction, /v1/users/me email lookup
- `chunking.py` — fixed-size character windowing (1000/100 overlap)
- `embeddings.py` — NVIDIA NIM client; verified live against the real API (2048-dim vectors confirmed)
- `sync_service.py` — orchestrates the pipeline; commits each page as it's processed, only advances last_synced_at after the full run succeeds
- `sync_status.py` — in-memory progress tracker for the frontend to poll
- `requirements.txt` — pinned backend dependencies
- `venv/` — local virtualenv (gitignored), not checked in.


## Rules
- Keep all Notion and embedding API secrets/tokens backend-only. Use .env when ever required.