# AGENTS.md — Neupen

AI-driven novel writing system. Six specialized agents collaborate across outline, character, and chapter generation, backed by a three-layer memory system.

## Build / Run / Install

```bash
# Install dependencies
pip install -r requirements.txt

# Run the app (Streamlit)
streamlit run app.py

# Docker deployment
docker compose up -d
docker compose down

# Build Mac app
bash scripts/build_mac.sh
bash scripts/create_dmg.sh
```

There are **no tests or linting scripts** in this project. Verification is manual — run the app and exercise the UI.

## Architecture

```
app.py                         # Entry: st.set_page_config + import ui.app.main
└── ui/app.py                  # Session init, identity gate, page routing
    ├── ui/sidebar.py          # Navigation + project status + global chat
    ├── ui/pages/project.py    # Create / open / delete novels
    ├── ui/pages/settings.py   # World-building, characters, foreshadowing, style, models, API keys
    ├── ui/pages/outline.py    # Full outline, volumes, chapter outlines, document import
    ├── ui/pages/writing.py    # Chapter generation, review, polish, reader simulation
    ├── ui/pages/visualization.py  # Character network, foreshadowing map, emotion curve
    ├── ui/pages/export.py     # TXT / Markdown / DOCX / EPUB export
    └── ui/pages/platform_styles.py # Platform-specific writing rules
        └── core/workflow.py   # NovelWorkflow — state machine orchestrating all agents
            ├── core/agents.py     # 6 agent classes, each wraps NovelLLM
            ├── core/memory.py     # 3-layer memory: SQLite (L1+L2) + LanceDB vector (L3)
            ├── core/detector.py   # 5-category conflict detection + ReviewReport
            ├── core/llm.py        # NovelLLM — unified Anthropic / OpenAI-compatible interface
            ├── core/models.py     # SQLAlchemy ORM (Novel, Character, Chapter, Volume, etc.)
            ├── core/config.py     # Env vars, paths, API keys, tuneable constants
            ├── core/permissions.py    # Owner vs reviewer access, invite codes, presence
            └── core/platform_styles.py # Per-platform writing rules injected into prompts
```

### Six Agents (core/agents.py)

| Agent | Class | Role |
|-------|-------|------|
| OutlineAgent | `OutlineAgent` | Generate structured outline JSON from logline; parse imported docs; batch chapter outlines |
| CharacterAgent | `CharacterAgent` | Create character profiles; detect inconsistencies; extract relationships from chapters |
| WriterAgent | `WriterAgent` | Write chapter content using 3-layer memory context; streaming output |
| ReviewerAgent | `ReviewerAgent` | 5-category conflict detection; auto-fix below severity threshold |
| PolisherAgent | `PolisherAgent` | Prose polish; style transfer from reference text |
| ReaderAgent | `ReaderAgent` | Simulate 3 reader types with scored evaluations |

### Three-Layer Memory (core/memory.py)

- **Layer 1 (Global, SQLite)**: World-building, all characters, outlines, foreshadowing, timeline — permanent.
- **Layer 2 (Chapter, SQLite)**: Most recent N chapters' full text + summaries — medium-term.
- **Layer 3 (Fragment, LanceDB + Qwen3-Embedding)**: All chapters chunked at 500 chars, vector-indexed for semantic retrieval across novels.

`MemoryManager.build_writing_context()` assembles all three layers before WriterAgent calls. `save_new_chapter()` syncs all three after writing.

### Data Flow — Writing Pipeline

```
WriterAgent → draft (streaming)
  → ReviewerAgent → conflict report
    → auto-fix minor conflicts (severity < threshold)
      → loop up to MAX_REVIEW_ITERATIONS (5)
        → low-score rewrite up to MAX_TOTAL_ATTEMPTS (10)
          → optional PolisherAgent polish
            → save to SQLite + LanceDB + version history (max MAX_VERSIONS=10)
              → summarize_chapter() for Layer 2 memory
                → set approval_status = "pending"
```

## Key Files & Directories

| Path | Purpose |
|------|---------|
| `app.py` | Streamlit entry (must be first — sets `st.set_page_config`) |
| `core/llm.py` | `NovelLLM` class + `MODEL_REGISTRY` (all supported models). Auto-selects Anthropic SDK vs OpenAI-compatible SDK based on provider. |
| `core/agents.py` | 1507 lines — all 6 agent classes with their system prompts and generation methods |
| `core/config.py` | All tuneable constants: `DEFAULT_MODEL`, `DATA_DIR`, `EMBEDDING_MODEL`, `AUTO_APPROVE_THRESHOLD`, etc. Plus API key persistence logic. |
| `core/models.py` | SQLAlchemy ORM — `Novel`, `Character`, `Volume`, `Chapter`, `Foreshadowing`, `TimelineEvent`, `Collaborator`, `NovelDocument` |
| `core/memory.py` | `MemoryManager`, `GlobalMemory` — 3-layer read/write with LanceDB chunking |
| `core/workflow.py` | `NovelWorkflow` — lazy agent init, per-agent model selection (3-level fallback), full pipeline orchestration |
| `core/detector.py` | `ConflictDetector`, `ConflictItem`, `ReviewReport` — 5 detection categories |
| `core/permissions.py` | `is_owner()`, `can_edit()`, `can_approve()`, invite code generation, online presence |
| `.streamlit/config.toml` | Streamlit server config (`headless = true`) |
| `requirements.txt` | Python dependencies (crewai, anthropic, openai, lancedb, streamlit, etc.) |
| `docker-compose.yml` | Single-service deployment with `neupen_data` volume |
| `Dockerfile` | Container image definition |

## Model Selection (3-Level Fallback)

Each agent's model is resolved in this order:
1. **Per-agent config** (`model_outline`, `model_writer`, etc.) stored on the `Novel` row — set in Settings UI
2. **Project default** (`llm_model` on the `Novel` row)
3. **Global default** (`DEFAULT_MODEL` env var, falls back to `claude-opus-4-6`)

The `MODEL_REGISTRY` in `core/llm.py` is the single source of truth for all models. To add a new model, add an entry there — no other files need changes.

## Coding Conventions

- **Language**: Chinese system prompts inside agent classes; English function/variable names and docstrings.
- **Error handling**: Agents raise `ValueError` or `RuntimeError` with Chinese messages. UI pages catch and display via `st.error()`.
- **JSON from LLMs**: Uses `json-repair` library as fallback when `json.loads` fails on LLM output. The `_safe_json_loads()` helper is in `core/agents.py`.
- **Database sessions**: Obtained via `get_db()` (creates new session). Sessions are NOT thread-safe — each call site opens and closes its own. `MemoryManager.global_mem.db` is the shared session for workflow operations.
- **Streamlit patterns**: Pages access `st.session_state` for `novel_id`, `page`, `collab_identity`. Permissions are enforced in page code — reviewers see disabled buttons.
- **No async**: Everything is synchronous. Streaming uses Python generators (`yield`).

## Git Workflow

- Branch naming: `feature/` prefix for feature branches (e.g., `feature/page-content-update`)
- Commit style: Chinese prefixed with type (`feat:`, `docs:`, `update:`)
- PR-based workflow with merge commits

## Tips for AI Agents

- **`app.py` must call `st.set_page_config` before any other Streamlit call.** It's intentionally the first import chain.
- **The `core/data/` directory is the live data store.** Contains `novels.db` (SQLite), `lancedb/` (vectors), `api_keys.json` (in-app keys), `exports/`, and `platform_styles.json`. Don't delete it casually.
- **Embedding model downloads on first use** (~1.2 GB for Qwen3-Embedding-0.6B). First LanceDB query will be slow.
- **`NovelLLM` is the single LLM interface.** All agents go through it. Provider routing (`anthropic` vs `openai_compatible`) is transparent to callers.
- **`MemoryManager.build_writing_context()` is the critical pre-writing step** — assembles all three memory layers. If writer output seems disconnected from prior chapters, check this function.
- **ReviewerAgent and ConflictDetector overlap** — the agent wraps the detector and adds auto-fix logic. For standalone conflict detection without fixes, use `ConflictDetector` directly.
- **Version history** is stored in the `chapter_versions` table (JSON dump of chapter state). Max versions per chapter controlled by `MAX_VERSIONS` config.
