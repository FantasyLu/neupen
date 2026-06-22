# AGENTS.md — Neupen

AI-driven novel writing system. Eight specialized agents collaborate across outline, character, chapter generation, and polish, backed by a three-layer memory system.

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
    ├── ui/pages/project.py    # Create / open / delete novels, idea chat
    ├── ui/pages/settings.py   # World-building, characters, foreshadowing, style, models, API keys
    ├── ui/pages/outline.py    # Full outline, volumes, chapter outlines, document import
    ├── ui/pages/writing.py    # Chapter generation, review, polish, reader simulation
    ├── ui/pages/visualization.py  # Character network, foreshadowing map, emotion curve
    ├── ui/pages/export.py     # TXT / Markdown / DOCX / EPUB export
    └── ui/pages/platform_styles.py # Platform-specific writing rules
    └── ui/components/global_chat.py # Sidebar CanvasAgent chat with typed code block parsing
        └── core/workflow.py   # NovelWorkflow — state machine orchestrating all agents
            ├── core/agents.py     # 8 agent classes, each wraps NovelLLM
            ├── core/memory.py     # 3-layer memory: SQLite (L1+L2) + LanceDB vector (L3)
            ├── core/detector.py   # 5-category conflict detection + ReviewReport
            ├── core/llm.py        # NovelLLM — unified Anthropic / OpenAI-compatible interface
            ├── core/models.py     # SQLAlchemy ORM (Novel, Character, Chapter, Volume, etc.)
            ├── core/config.py     # Env vars, paths, API keys, temperature defaults
            ├── core/permissions.py    # Owner vs reviewer access, invite codes, presence
            └── core/platform_styles.py # Per-platform writing rules injected into prompts
```

## Eight Agents (core/agents.py)

| Agent | Class | Role |
|-------|-------|------|
| OutlineAgent | `OutlineAgent` | Generate structured outline JSON from logline; parse imported docs; batch chapter outlines; foreshadowing scheduling |
| CharacterAgent | `CharacterAgent` | Create character profiles; detect inconsistencies; extract relationships from chapters |
| WriterAgent | `WriterAgent` | Write chapter content using 3-layer memory context; streaming output; de-AI-ification injection |
| ReviewerAgent | `ReviewerAgent` | 5-category conflict detection + AI-trace detection; auto-fix below severity threshold |
| PolisherAgent | `PolisherAgent` | Prose polish; style transfer from reference text; project-level de-AI rules injection |
| ReaderAgent | `ReaderAgent` | Simulate 3 reader types (avid/editor/casual) with scored evaluations |
| IdeaAgent | `IdeaAgent` | Multi-turn idea-chat to refine logline; extract structured project config from conversations |
| CanvasAgent | `CanvasAgent` | Sidebar global chat; typed code blocks (`outline`, `settings`, `world`, `characters`, `chapter`, `volume`, `foreshadowing`); one-click apply to target pages |

## Three-Layer Memory (core/memory.py)

- **Layer 1 (Global, SQLite)**: World-building, all characters, outlines, foreshadowing, timeline — permanent.
- **Layer 2 (Chapter, SQLite)**: Most recent N chapters' full text + summaries — medium-term.
- **Layer 3 (Fragment, LanceDB + Qwen3-Embedding)**: All chapters chunked at 500 chars, vector-indexed for semantic retrieval across novels.

`MemoryManager.build_writing_context()` assembles all three layers before WriterAgent calls. `save_new_chapter()` syncs all three after writing.

## Data Flow — Writing Pipeline

```
WriterAgent → draft (streaming)
  → ReviewerAgent → conflict + AI-trace report
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
| `app.py` | Streamlit entry (must call `st.set_page_config` before any other Streamlit call) |
| `core/llm.py` | `NovelLLM` class + `MODEL_REGISTRY` (Anthropic, DeepSeek, Doubao, Qwen, Gemini). Auto-selects SDK based on provider. |
| `core/agents.py` | 1536 lines — all 8 agent classes with system prompts and generation methods |
| `core/config.py` | All tuneable constants + 7 per-agent temperature defaults + API key persistence |
| `core/models.py` | SQLAlchemy ORM — `Novel`, `Character`, `Volume`, `Chapter`, `Foreshadowing`, `TimelineEvent`, `Collaborator`, `NovelDocument` (plus per-agent model/temperature columns on `Novel`) |
| `core/memory.py` | `MemoryManager`, `GlobalMemory` — 3-layer read/write with LanceDB chunking |
| `core/workflow.py` | `NovelWorkflow` — lazy agent init, per-agent model + temperature selection (3-level fallback), full pipeline orchestration |
| `core/detector.py` | `ConflictDetector`, `ConflictItem`, `ReviewReport` — 5 conflict categories + AI-trace detection |
| `core/permissions.py` | `is_owner()`, `can_edit()`, `can_approve()`, invite code generation, online presence |
| `ui/components/global_chat.py` | Sidebar CanvasAgent UI — parses 7 typed code block types, one-click apply to pages |
| `ui/app.py` | Session init, routing — all 7 pages + identity gate + API key check |
| `.streamlit/config.toml` | Streamlit server config (`headless = true`) |
| `requirements.txt` | Python dependencies (crewai, anthropic, openai, lancedb, streamlit, json-repair, etc.) |
| `docker-compose.yml` | Single-service deployment with `neupen_data` volume |
| `core/data/` | Live data store: `novels.db`, `lancedb/`, `api_keys.json`, `exports/`, `platform_styles.json`. Do not delete casually. |

## Model Selection (3-Level Fallback)

Each agent's model is resolved in this order:
1. **Per-agent config** (`model_outline`, `model_writer`, etc.) stored on the `Novel` row — set in Settings UI
2. **Project default** (`llm_model` on the `Novel` row)
3. **Global default** (`DEFAULT_MODEL` env var, falls back to `claude-opus-4-6`)

Temperature follows the same 3-level fallback: per-agent Novel column → config default → model default.

The `MODEL_REGISTRY` in `core/llm.py` is the single source of truth. To add a model, add an entry there — no other files need changes.

## Coding Conventions

- **Language**: Chinese system prompts inside agent classes; English function/variable names and docstrings.
- **Error handling**: Agents raise `ValueError` or `RuntimeError` with Chinese messages. UI pages catch and display via `st.error()`.
- **JSON from LLMs**: Uses `json-repair` library as fallback when `json.loads` fails. `_safe_json_loads()` is in `core/agents.py`.
- **Database sessions**: Obtained via `get_db()` (creates new session). Sessions are NOT thread-safe — each call site opens and closes its own.
- **Streamlit patterns**: Pages access `st.session_state` for `novel_id`, `page`, `collab_identity`. Permissions enforced in page code — reviewers see disabled buttons.
- **No async**: Everything is synchronous. Streaming uses Python generators (`yield`).

## Git Workflow

- Branch naming: `feature/` prefix (e.g., `feature/page-content-update`)
- Commit style: Chinese prefixed with type (`feat:`, `docs:`, `update:`, `refactor:`)
- PR-based workflow with merge commits

## Tips for AI Agents

- **`app.py` must call `st.set_page_config` before any other Streamlit call.** It is intentionally the first import chain.
- **CanvasAgent is the entry point for all sidebar AI chat.** It uses typed code blocks (7 types: `outline`, `settings`, `world`, `characters`, `chapter`, `volume`, `foreshadowing`). Parsing logic is in `ui/components/global_chat.py`. Changing block formats requires updating both `_TYPED_BLOCK_RE` and `_APPLY_LABELS`.
- **The `core/data/` directory is the live data store.** Contains `novels.db` (SQLite), `lancedb/` (vectors), `api_keys.json` (in-app keys), `exports/`, and `platform_styles.json`. Don't delete it casually.
- **Embedding model downloads on first use** (~1.2 GB for Qwen3-Embedding-0.6B). First LanceDB query will be slow.
- **`NovelLLM` is the single LLM interface.** All agents go through it. Provider routing (`anthropic` vs `openai_compatible`) is transparent to callers. The three main methods are `generate()`, `generate_stream()`, and `generate_chat()` — all accept `temperature`.
- **`MemoryManager.build_writing_context()` is the critical pre-writing step.** If writer output seems disconnected from prior chapters, check this function.
- **ReviewerAgent and ConflictDetector overlap** — the agent wraps the detector and adds auto-fix + AI-trace detection. For standalone conflict detection, use `ConflictDetector` directly.
- **De-AI-ification rules** are injected at two levels: WriterAgent (`deai_rules` field on Novel) and PolisherAgent (`DEFAULT_DEAI_RULES` in config). Check both if output feels too AI-like.
- **Version history** is stored in `chapter_versions` table (JSON dump). Max versions controlled by `MAX_VERSIONS` (10). Auto-cleanup on new version save.
- **Temperature defaults** are configured per-agent in `core/config.py`: Outline 0.6, Character 0.7, Writer 0.8, Reviewer 0.2, Polisher 0.5, Reader 0.5, Canvas 0.7. Can be overridden per-project in Settings UI.
