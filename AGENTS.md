# AGENTS.md — Neupen

AI-driven novel writing system (Streamlit). Eight agents collaborate across outline, character, chapter generation, and polish, backed by a three-layer memory system (SQLite + LanceDB vectors).

## Commands

```bash
pip install -r requirements.txt
streamlit run app.py

# Docker
docker compose up -d

# Mac app build
bash scripts/build_mac.sh
bash scripts/create_dmg.sh
```

There are **no tests, linting, or type-checking** in this project. Verification is manual via the UI.

## Critical Gotchas

- **`app.py` must call `st.set_page_config` before any other Streamlit call.** The `from ui.app import main` deliberately happens after `set_page_config`. Never add Streamlit imports or calls above it.
- **Data directory default is `core/data/`** (resolved from `Path(__file__).parent / "data"` in `core/config.py`). Setting `DATA_DIR=./data` in `.env` moves it to project root. Both `novels.db` (SQLite), `lancedb/` (vectors), `api_keys.json`, and `exports/` live here. Don't delete casually.
- **Embedding model auto-downloads on first use** (~1.2 GB for `Qwen/Qwen3-Embedding-0.6B`). First LanceDB query will be slow.
- **Database sessions are NOT thread-safe.** `get_db()` creates a new session each call — callers must open and close their own.
- **API key priority**: `data/api_keys.json` (in-app, highest) > `.env` > environment variables. `config.py` calls `apply_saved_keys()` at module load to inject saved keys into `os.environ`.
- **No async.** Everything is synchronous. Streaming uses Python generators (`yield`).

## Architecture — What's Non-Obvious

### Entry Flow
```
app.py → st.set_page_config → ui/app.py:main() → session init + identity gate + page routing
  ├── ui/sidebar.py (navigation + global CanvasAgent chat)
  └── ui/pages/*.py (7 pages: project, settings, outline, writing, visualization, export, platform_styles)
```

### 8 Agents (`core/agents.py`, 1772 lines)

All defined in one file. Each wraps `NovelLLM`. They don't communicate directly — `core/workflow.py` orchestrates.

| Agent | Key Behavior |
|-------|-------------|
| `OutlineAgent` | Outputs structured JSON; uses `_safe_json_loads()` with `json-repair` fallback |
| `CharacterAgent` | Profile generation + inconsistency detection |
| `WriterAgent` | Streaming chapter content; injects de-AI rules + platform style constraints |
| `ReviewerAgent` | `pipeline_review()` = 3-gate funnel (context → continuity → stylistic); `review_chapter()` = legacy single-pass |
| `PolisherAgent` | Style transfer from reference text; injects project-level de-AI rules |
| `ReaderAgent` | Simulates 3 reader types with scored evaluations |
| `IdeaAgent` | Multi-turn chat to refine logline; extracts structured project config |
| `CanvasAgent` | Sidebar chat; routes intents to specialized agents; parses 8 typed code blocks |

### CanvasAgent Typed Code Blocks

Parsed by `_TYPED_BLOCK_RE` in `ui/components/global_chat.py`. 8 types: `outline`, `settings`, `world`, `characters`, `chapter`, `volume`, `foreshadowing`, `style`. Changing formats requires updating both `_TYPED_BLOCK_RE` and `_APPLY_LABELS` in that file.

### 3-Gate Review Pipeline (`core/workflow.py`)

The writing pipeline uses a funnel of 3 review gates via `ReviewerAgent.pipeline_review()`:
1. **Gate 1 — Context** (outline + character consistency) — threshold `GATE_CONTEXT_THRESHOLD` (8.5)
2. **Gate 2 — Continuity** (state + worldview + spatiotemporal logic) — threshold `GATE_CONTINUITY_THRESHOLD` (9.0)
3. **Gate 3 — Stylistic** (de-AI + writing style) — threshold `GATE_STYLISTIC_THRESHOLD` (8.0)

Each gate retries up to `MAX_GATE_RETRIES` (2). Final score = weighted sum (0.3, 0.4, 0.3). All thresholds configurable via env vars in `core/config.py`.

### 3-Layer Memory (`core/memory.py`)

- **L1 (Global, SQLite)**: World-building, characters, outlines, foreshadowing, timeline — permanent
- **L2 (Chapter, SQLite)**: Recent N chapters' full text + summaries (N = `RECENT_CHAPTERS_COUNT`, default 5)
- **L3 (Fragment, LanceDB)**: All chapters chunked at 500 chars, vector-indexed for semantic retrieval

`MemoryManager.build_writing_context()` assembles all three layers before WriterAgent calls. If writer output seems disconnected from prior chapters, check this function.

### Model Selection (3-Level Fallback)

1. Per-agent config (`model_outline`, `model_writer`, etc.) on the `Novel` row
2. Project default (`llm_model` on `Novel`)
3. Global `DEFAULT_MODEL` env var (default: `claude-opus-4-6`)

Temperature uses the same fallback: per-agent Novel column → per-agent config default → model default.

`MODEL_REGISTRY` in `core/llm.py` is the single source of truth. To add a model, add an entry there only.

## Conventions

- **Language**: Chinese system prompts inside agents; English function/variable names.
- **Error messages**: Chinese, raised as `ValueError` / `RuntimeError`. UI catches via `st.error()`.
- **JSON from LLMs**: Always parse with `_safe_json_loads()` (in `core/agents.py`) which falls back to `json-repair`.
- **Streamlit state**: Pages use `st.session_state` for `novel_id`, `page`, `collab_identity`. Permissions enforced in page code.
- **De-AI rules**: Injected at two levels — `WriterAgent` (from `Novel.deai_rules`) and `PolisherAgent` (`DEFAULT_DEAI_RULES` in config). Check both if output feels too AI-like.

## Git

- Branch: `feature/` prefix
- Commits: Chinese, prefixed with type (`feat:`, `fix:`, `refactor:`, `docs:`, `update:`)
