# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Neupen is an AI-driven long-form novel writing system. Six specialized Agents collaborate to handle outline, character, and chapter generation. A three-layer memory system maintains cross-chapter narrative consistency.

## Common Commands

```bash
# Run the app
streamlit run app.py

# Install dependencies
pip install -r requirements.txt

# Copy env config
cp .env.example .env

# Docker deployment
docker compose up -d
docker compose down

# Build Mac app
bash scripts/build_mac.sh
bash scripts/create_dmg.sh
```

There are no tests or linting scripts in this project currently.

## Architecture

### Layer Structure

```
app.py (entry, sets st.set_page_config first)
  └── ui/app.py (routing + session state + page dispatch)
        └── ui/pages/*.py (6 functional pages)
              └── core/workflow.py (NovelWorkflow — state machine for creation pipeline)
                    ├── core/agents.py (6 Agent classes, each wraps NovelLLM)
                    ├── core/memory.py (MemoryManager, 3-layer memory)
                    ├── core/detector.py (conflict detection + impact analysis)
                    └── core/permissions.py (owner vs reviewer access control)
```

### LLM Interface (`core/llm.py`)

`NovelLLM` is the single LLM abstraction. It auto-selects backend:
- `provider == "anthropic"` → uses `anthropic` SDK directly; enables `cache_control: ephemeral` on system prompts > 1000 chars
- `provider == "openai_compatible"` → uses `openai` SDK with custom `base_url` (DeepSeek, Doubao, Qwen, Gemini all use this path)

Model capabilities, API key env vars, and base URLs are all declared in `MODEL_REGISTRY` in `core/llm.py`. Add new models there.

### Three-Layer Memory (`core/memory.py`)

- **Layer 1 (Global, SQLite)**: World-building, all character profiles, outlines, foreshadowing library, timeline — permanent
- **Layer 2 (Chapter, SQLite)**: Most recent N chapters' full text + summaries — medium-term
- **Layer 3 (Fragment, LanceDB + Qwen3-Embedding)**: All chapters chunked (500 chars) and vector-indexed for semantic retrieval — single table across all novels, supports Lance time-travel

`MemoryManager.build_writing_context()` assembles all three layers before each WriterAgent call. `save_new_chapter()` syncs all three layers after writing.

### Six Agents (`core/agents.py`)

Each Agent has a fixed system prompt and calls `NovelLLM.generate()` or `generate_stream()`. They don't communicate directly — all orchestration is in `NovelWorkflow`. Agent model selection uses a 3-level fallback: per-agent config → project default → global `DEFAULT_MODEL` env var.

| Agent | Role |
|---|---|
| `OutlineAgent` | Generates structured outline JSON from a one-line premise |
| `CharacterAgent` | Creates character profiles from outline; detects character inconsistencies |
| `WriterAgent` | Writes chapter content using 3-layer memory context; supports streaming |
| `ReviewerAgent` | 5-category conflict detection; auto-fixes issues below severity threshold |
| `PolisherAgent` | Improves prose; applies style transfer profile if set |
| `ReaderAgent` | Simulates 3 reader types with scored evaluations |

### Data Layer (`core/models.py` + `core/config.py`)

- SQLite at `data/novels.db` via SQLAlchemy ORM
- LanceDB at `data/lancedb/` for vector storage
- API keys have three priority levels: `data/api_keys.json` (in-app, highest) > `.env` file > environment variables
- `config.py` calls `apply_saved_keys()` at module load to inject saved keys into `os.environ`

### UI (`ui/`)

Pure Streamlit. `ui/app.py` manages routing via `st.session_state.page`. Key session state keys: `novel_id`, `page`, `collab_display_name`, `collab_identity` (owner vs reviewer), `is_writing`, `batch_writing`.

Permissions are enforced in page code via `core/permissions.py` — reviewers see disabled buttons, cannot write/generate, but can comment and approve chapters.

### Writing Pipeline (`core/workflow.py`)

`NovelWorkflow.write_and_review_chapter()` runs sequentially:
1. `WriterAgent` → draft (streaming)
2. `ReviewerAgent` → conflict report
3. Auto-fix minor conflicts (severity < `AUTO_APPROVE_THRESHOLD`)
4. `PolisherAgent` → optional prose polish
5. Save to SQLite + LanceDB + version history (max `MAX_VERSIONS`)
6. `summarize_chapter()` → summary for Layer 2 memory
7. Set `approval_status = "pending"`

## Key Configuration

All tuneable constants are in `core/config.py` and override-able via environment variables:

| Var | Default | Effect |
|---|---|---|
| `DEFAULT_MODEL` | `claude-opus-4-6` | Global fallback model |
| `DATA_DIR` | `./data` | All persistent data root |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | Vector embedding (~1.2 GB, auto-downloaded on first use) |
| `RECENT_CHAPTERS_COUNT` | `5` | Layer 2 memory window |
| `VECTOR_TOP_K` | `10` | Layer 3 semantic retrieval count |
| `AUTO_APPROVE_THRESHOLD` | `3` | Max conflict severity for auto-fix |
| `MAX_VERSIONS` | `10` | Version history per chapter |
