# Setup

Local setup has three parts: a Postgres database with `pgvector`, a Notion
integration + API keys in `.env`, and loading the extension into Chrome.

## Prerequisites

- Python 3.10+
- Postgres 14+ with the [pgvector](https://github.com/pgvector/pgvector)
  extension installed (`CREATE EXTENSION vector` must succeed — pgvector
  ships as a package on most distros, e.g. `postgresql-XX-pgvector`, or via
  `brew install pgvector` on macOS)
- Google Chrome (or any Chromium-based browser with Manifest V3 side
  panel support)
- A [Notion integration](https://www.notion.so/my-integrations) with the
  **public OAuth** capability enabled (not an internal integration — this
  project uses the Authorization Code flow)
- An [NVIDIA NIM](https://build.nvidia.com/) API key (used for both the
  embedding and chat-completion models)

## 1. Database

Create a database and enable pgvector:

```bash
createdb notion_lens
psql notion_lens -c "CREATE EXTENSION IF NOT EXISTS vector"
```

(The migrations also run `CREATE EXTENSION IF NOT EXISTS vector`
themselves, so this step is really just a sanity check that your Postgres
build has the extension available.)

## 2. Notion integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
   and create a new integration.
2. Under **OAuth Domain & URIs**, add a redirect URI:
   `http://localhost:8000/auth/notion/callback` (must match
   `NOTION_REDIRECT_URI` below exactly).
3. Copy the **OAuth client ID** and **client secret** — these become
   `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET`.

## 3. Environment variables

Copy the template and fill it in:

```bash
cp .env_copy .env
```

`.env` (repo root — the backend loads it from `backend/config.py` via a
path relative to the backend directory):

| Variable | Where it comes from |
|---|---|
| `NOTION_CLIENT_ID` / `NOTION_CLIENT_SECRET` | The Notion integration created above |
| `NOTION_REDIRECT_URI` | Must exactly match the redirect URI registered on the integration (default `http://localhost:8000/auth/notion/callback` works for local dev) |
| `SESSION_SECRET` | Any long random string you generate yourself, e.g. `openssl rand -hex 32` — signs the OAuth `state` param |
| `EMBEDDER_MODEL_API_KEY` | Your NVIDIA NIM API key |
| `GENERATION_MODEL_API_KEY` | Your NVIDIA NIM API key (can be the same key as above) |
| `DATABASE_URL` | `postgresql+psycopg://<user>:<password>@<host>:<port>/notion_lens` |

## 4. Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

Then, from the repo root:

```bash
./run_backend.sh
```

This starts `uvicorn main:app --reload` on `http://localhost:8000`. Leave
it running.

To confirm it's up: `curl http://localhost:8000/auth/notion/status` should
return JSON (not a connection error).

## 5. Load the extension in Chrome

1. Go to `chrome://extensions`.
2. Enable **Developer mode** (top right).
3. Click **Load unpacked** and select the repo root (the folder containing
   `manifest.json`).
4. Click the Notion Lens toolbar icon to open the side panel.
5. Click **Connect Notion**, complete the OAuth consent screen, and select
   which pages/databases to share with the integration.

The first sync runs automatically once connected. Subsequent syncs are
incremental — only new or edited pages get re-embedded.

## Running tests

```bash
cd backend
source venv/bin/activate
pytest
```

Tests hit a real Postgres database via `DATABASE_URL` (there's no
in-memory/mock DB fallback), so Postgres must be running and migrated
(`alembic upgrade head`) before running the suite.

## Troubleshooting

- **`Missing required environment variable: ...`** — `config.py` validates
  required `.env` vars at import time; check `.env` is in the repo root
  and every required key from the table above is set.
- **`relation "notion_chunks" does not exist`** — migrations haven't been
  run; run `alembic upgrade head` from `backend/` with `DATABASE_URL`
  pointing at a running Postgres instance.
- **Side panel shows a connection error to `localhost:8000`** — the
  backend isn't running, or `manifest.json`'s `host_permissions` doesn't
  match the port you're running it on.
- **OAuth callback fails with a redirect URI mismatch** — the URI in
  `.env`'s `NOTION_REDIRECT_URI` must be byte-for-byte identical to the
  one registered on the Notion integration.
