# Decisions

Brief log of the technical/architectural decisions made on this project so far.

## Storage & ingestion

- **Postgres + pgvector**, replacing the earlier SQLite token store, so embeddings and
  relational data (users, connections, pages, chunks) live in one place.
- **Manual + incremental sync**: pages are fetched via Notion's Search API sorted by
  `last_edited_time` desc. The full page list is scanned every run, but only pages that are
  new or whose `last_edited_time` changed since the stored checkpoint get re-chunked and
  re-embedded — unchanged pages are never re-fetched.
- `notion_connections.last_synced_at` is only advanced after a run completes successfully
  (including a no-op run, so the auto-sync throttle still works); a failed run leaves it
  untouched so nothing is silently skipped next time.
- Chunking started as fixed-size character windowing (1000 chars / 100 overlap) and was
  later upgraded to **structure-aware chunking**: chunks split along Notion block/heading
  boundaries instead of raw character counts (an oversized section still sub-splits with the
  original character window, but a sub-split can never cross into the next heading's content),
  and each chunk is prefixed with a **contextual breadcrumb** (`Page: X > Section: Y`) before
  embedding so it's self-contained for retrieval.
- Per-chunk **metadata** (heading path, block types, source block ids) is now populated on
  `NotionChunk.metadata_` — a JSONB column that already existed but was previously unused.
- Embeddings use **`nvidia/nemotron-3-embed-1b`, 2048-dim**, via NVIDIA NIM.
  Native pgvector `VECTOR` ANN indexes cap at 2000 dims, so the HNSW index is built over a
  `halfvec(2048)` cast instead.

## Auto-sync

- Auto-sync runs **at most once per day per connection**, triggered by whichever happens
  first: the user opening the side panel (`GET /auth/notion/status`) or a periodic
  APScheduler sweep — added as a safety net for users who leave the panel closed.
- Both trigger paths share one throttle (`sync_status.should_auto_sync`) and both call the
  same incremental `sync_service.run_sync` — neither ever forces a full re-index.
- **"+ Add pages"** re-opens the same OAuth authorize URL (Notion's picker keeps prior
  selections). The backend detects this as a reconnect and kicks off an immediate sync for
  fast feedback; no special-casing is needed to pick up the newly-shared pages since a normal
  sync already isn't date-filtered on the scan itself.

## Retrieval & chat

- Retrieval is **hybrid**: pgvector cosine similarity (semantic) merged with Postgres
  full-text `tsvector`/GIN search (keyword) via **Reciprocal Rank Fusion (k=60)**, so both
  paraphrased questions and exact-term lookups work well.
- `search_similar_chunks` uses a `strict_order` iterative HNSW scan so the per-user `WHERE`
  filter can't silently starve the ANN index of results.
- The keyword `tsvector` is a generated column over the **full `content` column** (breadcrumb +
  body, not body-only), so page titles/section headings are keyword-searchable too. Keyword
  search parses the question with `websearch_to_tsquery` (tolerates free-form natural-language
  input without erroring) and ranks with `ts_rank_cd` (rewards query-term proximity).
- Each retriever pulls a wider pool (`HYBRID_CANDIDATE_POOL = 20`) before RRF fuses them down to
  the final `TOP_K_CHUNKS` (5), so a chunk strong on only one signal still gets a fair chance to
  be fused in.
- **Citations are grouped per page, not per chunk.** RRF can surface several chunks from the
  same page; these are grouped into one numbered source/citation before the prompt and API
  response are built. This closes a real bug: numbering sources per chunk let the model cite,
  e.g., `[4]`, while the frontend (which deduped citation links by page without renumbering)
  showed no matching link. Doing the grouping once, backend-side, keeps prompt sources /
  `citations[]` / rendered links consistent by construction; the frontend's now-redundant
  per-page dedup was removed rather than kept as a "harmless" no-op, since two independent
  dedup mechanisms silently drifting apart is exactly what caused the bug.
- **Chat is stateless server-side**: no conversations/messages table. The frontend resends
  trimmed recent history each turn.
- `nvidia/llama-3.3-nemotron-super-49b-v1.5` (original generation model) hit end-of-life on
  NVIDIA's API and was swapped for its direct successor, `nvidia/nemotron-3-super-120b-a12b`.
  `generation.py` retries transient 502/503/504s observed live on that hosted tier, and strips
  any leaked `<think>` reasoning trace.

## Security

- Notion and embedding/generation API secrets/tokens stay **backend-only**, loaded from a
  gitignored root `.env` — never exposed to the extension.

## Docs

- Added `README.md` (project overview, architecture diagram, tech stack) and `SETUP.md`
  (step-by-step local setup: Postgres/pgvector, Notion integration, env vars, running the
  backend, loading the extension) to make local onboarding self-contained instead of only
  living in `CLAUDE.md`.
