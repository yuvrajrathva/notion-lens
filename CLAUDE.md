# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Notion Lens is a Chrome extension that provides a side panel UI for RAG-style querying over a user's own Notion workspace. It has two parts:

- **Extension** (repo root): `manifest.json`, `background.js`, `sidepanel.html/css/js`. Pure vanilla JS/HTML/CSS, no build step, no bundler, no package.json.
- **Backend** (`backend/`): a FastAPI service that implements the Notion OAuth 2.0 Authorization Code flow end-to-end (login, callback, token exchange, storage, status) and serves a stub RAG query endpoint.

Notion OAuth is fully wired: the "Connect Notion" button drives a real backend-brokered OAuth flow and persists the resulting access token server-side. Sync is fully wired, both manually (refresh button) and automatically: it scans Notion's full page list every run but only fetches/re-embeds pages that are new or have a newer `last_edited_time` than what's already stored — never a full re-index of unchanged content — then chunks, embeds, and stores it in Postgres. Auto-sync fires at most once per day per connection, triggered either when the user opens the side panel or by a periodic APScheduler sweep (`backend/scheduler.py`), whichever comes first.

An "+ Add pages" button (visible once connected) lets the user grant the integration access to additional Notion pages without disconnecting: it re-opens the same OAuth authorize URL, whose consent screen re-shows Notion's page picker with prior selections kept. The backend detects this as a reconnect and kicks off an immediate sync for fast feedback — a normal sync already correctly picks up newly-shared pages regardless of how recently they were edited (see `sync_service.py` below), so nothing needs to be forced.

The side panel's query flow (`sidepanel.js`) is fully wired to a real RAG pipeline: the user's question is embedded, the top-5 most similar chunks from their own workspace are retrieved via pgvector cosine similarity, and an LLM generates a grounded, cited answer. Multi-turn conversation is supported by resending trimmed history each turn (no server-side chat persistence).


## Commands

Run the backend locally (from `backend/`, using the venv): run_backend.sh

## File-by-file reference

Root:
- `manifest.json` — MV3 manifest: side panel entry point, permissions (`scripting`, `sidePanel`, `storage`), and `host_permissions` for the local backend.
- `background.js` — service worker; only sets the side panel to open on toolbar-icon click.
- `sidepanel.html` — side panel markup: topbar, Notion connect bar, empty-state hero, and the query input bar.
- `sidepanel.css` — all side panel styling, including the connect-bar states (disconnected/connecting/connected/error).
- `sidepanel.js` — side panel behavior: Notion connect/status-poll flow, manual + auto sync status polling with a relative "last synced" label, and the chat query flow (multi-turn thread UI, citations).
- `.env` — real secrets (gitignored): `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `NOTION_REDIRECT_URI`, `SESSION_SECRET`, `EMBEDDER_MODEL_API_KEY`, `GENERATION_MODEL_API_KEY`.

`backend/`:
- `main.py` — FastAPI app; defines the `/auth/notion/*` OAuth routes (status check also triggers due auto-sync; callback kicks off an immediate sync when it detects a reconnect, i.e. an "Add pages" grant), `/notion/sync` + `/notion/sync/status`, and `/notion/query` (RAG chat). Starts/stops the APScheduler via a `lifespan` hook.
- `config.py` — loads and validates required env vars from the repo-root `.env`, plus model names/URLs and auto-sync tuning constants.
- `notion_oauth.py` — builds the Notion authorize URL, signs/verifies the CSRF-safe `state` param (HMAC, 10-min TTL), and exchanges an authorization code for a token.
- `db.py,` `models.py` — SQLAlchemy engine/session + the 4 ORM models (AppUser, NotionConnection, NotionPage, NotionChunk)
- `alembic/` — migration environment wired to Settings.DATABASE_URL
- `repositories.py` — upsert/query helpers, plus `search_similar_chunks` (raw-SQL pgvector cosine search scoped to one user's connection, using `strict_order` iterative HNSW scan so the user-scoped WHERE filter can't silently starve the index of results), `get_last_edited_map` (id -> last_edited_time for a connection's already-indexed pages, used by `sync_service` to skip unchanged pages), and `list_all_connections` (for the scheduler sweep)
- `notion_client.py` — Search API pagination (always yields the full result set — see `sync_service.py` for why), recursive block-to-text extraction, /v1/users/me email lookup
- `chunking.py` — fixed-size character windowing (1000/100 overlap)
- `embeddings.py` — NVIDIA NIM embeddings client (`nvidia/nemotron-3-embed-1b`, 2048-dim); `passage` mode for sync, `query` mode for chat questions
- `generation.py` — NVIDIA chat-completions client for answer generation; strips any leaked `<think>` reasoning trace, retries transient 502/503/504s (observed live on NVIDIA's hosted "super" tier model)
- `chat_service.py` — RAG orchestration: embeds the question, retrieves top-K chunks, builds a grounded prompt with numbered sources, calls generation, returns `{answer, citations}`; short-circuits with a fixed "nothing indexed" answer when retrieval finds nothing (no LLM call)
- `sync_service.py` — orchestrates the pipeline; scans Notion's full page list every run (via `repositories.get_last_edited_map`) and skips any page that's already indexed with an unchanged `last_edited_time`, so unchanged content never gets re-fetched or re-embedded even though the scan itself isn't date-filtered — this is what lets a newly-shared-but-old page (e.g. rows of a database the user just connected) get picked up without a forced full re-index. Commits each processed page as it goes; always advances `last_synced_at` after a successful run (even a no-op one, so auto-sync's once-a-day throttle works), only skips advancing it on failure.
- `sync_status.py` — in-memory progress tracker for the frontend to poll; also `should_auto_sync()`, the once-per-day-per-connection throttle (with a cooldown after failures) shared by both auto-sync trigger paths
- `scheduler.py` — APScheduler `AsyncIOScheduler`; periodic sweep that auto-syncs any connection due per `should_auto_sync`, independent of whether the user opens the side panel
- `requirements.txt` — pinned backend dependencies
- `venv/` — local virtualenv (gitignored), not checked in.

Auto-sync has two trigger paths sharing `sync_status.should_auto_sync()`: (1) `GET /auth/notion/status`, hit every time the side panel opens; (2) the APScheduler sweep, as a safety net for users who leave the panel closed. Neither ever forces a full re-index — both call the same incremental `sync_service.run_sync`.

Chat is stateless server-side: the frontend resends trimmed conversation history (last few turns) with each question; there is no conversations/messages table. Citations are page-level, not block-level, since chunking loses block boundaries.

`nvidia/llama-3.3-nemotron-super-49b-v1.5` (originally specified for generation) reached end-of-life on NVIDIA's API; `config.py` uses `nvidia/nemotron-3-super-120b-a12b` instead, its direct successor in the current model catalog.


## Rules
- Keep all Notion and embedding API secrets/tokens backend-only. Use .env when ever required.