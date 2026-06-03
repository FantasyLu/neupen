# Neupen

> AI 驱动的中长篇小说创作系统。从一句话灵感出发，六个专职 Agent 协同完成大纲、人物、正文全流程创作，三层记忆系统保障跨章节叙事连贯。内置冲突检测、风格迁移、伏笔调度与读者模拟，支持多模型供应商与协同写作。

---

## 目录

- [快速开始](#快速开始)
- [功能使用指南](#功能使用指南)
- [多模型分工](#多模型分工)
- [功能特性](#功能特性)
  - [风格迁移](#风格迁移)
  - [智能伏笔调度](#智能伏笔调度)
  - [读者模拟](#读者模拟)
  - [结构可视化](#结构可视化)
  - [协同写作](#协同写作)
- [配置项说明](#配置项说明)
- [常见问题](#常见问题)
- [系统架构](#系统架构)
- [核心模块详解](#核心模块详解)
  - [六大 Agent](#六大-agent)
  - [三层记忆系统](#三层记忆系统)
  - [冲突检测与变更同步](#冲突检测与变更同步)
  - [工作流编排](#工作流编排)
- [数据模型](#数据模型)
- [未来方向](#未来方向)

---

## 快速开始

提供三种部署方式，选择最适合你的：

### 方式一：Docker 一键部署（推荐）

无需安装 Python 环境，一条命令启动：

```bash
git clone <repo-url> && cd neupen
docker compose up -d
```

访问 `http://localhost:8501`，首次启动在页面内配置 API Key 即可。

**环境变量注入（可选）**：也可通过 docker-compose 预设 Key：

```bash
ANTHROPIC_API_KEY=sk-xxx docker compose up -d
```

**数据持久化**：数据存储在 Docker volume `neupen_data` 中，包含数据库、向量库和导出文件。重建容器不会丢失数据。

### 方式二：Mac 客户端

1. 下载 `AI小说创作助手.dmg`
2. 双击打开，将应用拖入 Applications
3. 点击应用图标启动，浏览器自动打开

首次启动时在页面内配置 API Key。数据保存在 `~/Library/Application Support/AINovelWriter/`。

**从源码构建**：

```bash
bash scripts/build_mac.sh    # 生成 .app
bash scripts/create_dmg.sh   # 封装为 .dmg
```

### 方式三：源码运行

#### 1. 环境要求

- Python 3.11+
- pip

#### 2. 安装依赖

```bash
cd neupen
pip install -r requirements.txt
```

#### 3. 配置 API Key

**方式 A（推荐）**：启动后在页面内配置，无需编辑文件。

**方式 B**：通过 `.env` 文件配置：

```bash
cp .env.example .env
```

编辑 `.env`，至少配置一个提供商的 API Key：

```ini
# 必选其一（推荐 Anthropic）
ANTHROPIC_API_KEY=sk-ant-api03-xxxxxxxxxxxxxxxx

# 可选（用哪个配哪个）
DEEPSEEK_API_KEY=sk-xxxxxxxx
DOUBAO_API_KEY=xxxxxxxx
QWEN_API_KEY=sk-xxxxxxxx
GOOGLE_API_KEY=AIzaxxxxxxxx
```

#### 4. 启动

```bash
streamlit run app.py
```

访问 `http://localhost:8501`，输入显示名称进入系统。

### 典型工作流

```
① 项目管理页 → 新建项目，填写标题 + 一句话灵感，勾选「立即生成大纲」
                 ↓ 自动生成邀请码，可分享给协作者
② 大纲管理页 → 检查/微调章纲，查看伏笔调度警告
                 ↓
③ 设定管理页 → 查看 AI 生成的人物档案，补充世界观细节
               → 模型设置 tab 配置各 Agent 分工
               → 风格迁移 tab 上传参考文本提取风格
                 ↓
④ 写作页     → 逐章生成，查看审核报告，运行读者模拟
               → 审阅者可添加评论、审批章节
                 ↓
⑤ 可视化页   → 人物关系网络 / 伏笔分布 / 情感曲线
                 ↓
⑥ 导出页     → 选择格式导出
```

---

## 功能使用指南

### 项目管理

- **我的项目**：展示所有项目的字数/章数进度，点击「打开」进入
- **新建项目**：填写灵感 + 选择模型 + 勾选「立即生成大纲」→ 自动生成邀请码
- **加入项目**：输入邀请码以审阅者身份加入他人项目

### 设定管理（6 个 tab）

- **世界观设定**：修改后自动触发影响分析，报告受影响章节
- **人物档案**：AI 生成 / 手动编辑，保存时触发设定冲突检测
- **伏笔管理**：手动记录 / AI 分配截止章节 / 同步大纲伏笔，按紧急度排序
- **模型设置**：项目默认模型 + 各 Agent 分工配置 + 一键推荐配置
- **风格迁移**：粘贴/上传参考文本 → 分析 → 编辑 10 维特征 → 保存
- **API Key**：在页面内管理各提供商的 API Key，保存后立即生效

### 大纲管理

- 总览进度 + 伏笔调度警告面板
- 章纲编辑支持伏笔埋下/回收的逗号分隔输入
- 修改核心字段触发下游影响分析
- 审批状态 badge 显示在章节标题旁

### 写作

- **生成流水线**：写作 → 审核 → 自动修复 → 润色 → 摘要，一键完成
- **编辑模式**：手动改正文，保存时触发冲突检测（仅主笔）
- **审批状态**：三按钮（通过 / 需修改 / 驳回），重新生成自动重置
- **章节评论**：评论列表 + 发表评论（主笔和审阅者均可）
- **读者模拟**：一键运行，三种读者视角的评分卡片 + 亮点/建议
- **批量写作**：选择章节范围，一键按顺序生成多章，实时展示进度，完成后汇总报告
- **版本历史**：最近 5 个版本（草稿/润色/用户编辑）

### 可视化

- 人物关系力导向图（可筛选主要人物）
- 伏笔甘特图（跨度 + 颜色 + 截止菱形）
- 情感多折线（可选指标 + 字数柱状图）

### 导出

| 格式 | 适用场景 | 特色选项 |
|------|---------|---------|
| TXT | 任意设备阅读 | 可附大纲摘要 |
| Markdown | Obsidian / Typora | 含目录、人物档案、伏笔追踪表 |
| Word | 出版投稿 | 规范版式，自动处理段落缩进 |

---

## 多模型分工

支持 5 家提供商 12 个模型，可为每个 Agent 单独配置：

| 提供商 | 模型 | 特色 |
|-------|------|------|
| Anthropic | Claude Opus 4.6, Sonnet 4.6, Haiku 4.5 | 支持 prompt caching，中文写作质量最高 |
| DeepSeek | deepseek-chat, deepseek-reasoner | 成本极低，推理能力突出 |
| 字节豆包 | doubao-pro-32k, doubao-lite-32k | 中文优化，速度快 |
| 阿里通义千问 | qwen-max, qwen-plus, qwen-turbo | 中文理解能力强 |
| Google | gemini-2.0-flash, gemini-1.5-pro | 长上下文，速度快 |

### 推荐配置

| Agent | 推荐模型 | 理由 |
|-------|---------|------|
| 大纲师 | Opus | 奠定全书结构，一次性任务，质量优先 |
| 人设师 | Sonnet | 生成人物档案，Sonnet 足够 |
| 写手部 | Sonnet | 逐章写作，最高频调用，成本主体 |
| 审核师 | Opus | 质量门控，需要最强推理 |
| 润色师 | Opus | 文学质量优先 |
| 读者模拟 | Sonnet | 按需调用，Sonnet 足够 |

三级回退链：Agent 独立配置 → 项目默认模型 → `.env` 全局默认。

---

## 功能特性

### 风格迁移

上传喜欢的作家作品片段（500-3000 字），AI 自动提取 10 维风格特征，润色时忠实复现该风格。

#### 10 个风格维度

| 维度 | 说明 |
|------|------|
| 总体风格定位 | 一句话概括整体风格 |
| 句式特征 | 长短句比例、句式结构偏好 |
| 词汇风格 | 雅俗程度、文白比例 |
| 叙述风格 | 叙述距离、视角特点 |
| 对话特点 | 频率、口语化程度 |
| 描写特点 | 感官偏好、比喻手法 |
| 节奏特征 | 段落疏密、快慢切换 |
| 情感表达 | 直抒胸臆 vs 含蓄克制 |
| 标志性手法 | 该作者特有的技巧和意象 |
| 润色指令 | 直接告诉润色者该做什么（核心字段） |

风格档案可手动微调后保存，清除后恢复默认润色风格。

### 智能伏笔调度

#### 自动截止分配

用 LLM 根据故事结构为活跃伏笔批量分配最晚回收章节：

- 高重要度：全书后 30% 前回收
- 中重要度：第二幕结束前回收
- 低重要度：结局前 5 章回收

#### 实时警告

| 状态 | 表现 |
|------|------|
| 过期（超过截止章节未回收） | 大纲页/伏笔管理页显示红色警告 |
| 即将到期（10 章以内） | 显示黄色提醒 |

#### 大纲联动

将章纲中的伏笔名称自动同步到伏笔库（只新增不覆盖）。大纲师在生成/细化章纲时，若有活跃伏笔会自动注入调度表到 prompt，引导 AI 合理安排回收时机。

### 读者模拟

模拟三种读者类型阅读已完成章节，给出结构化评分和改进建议。

| 读者类型 | 评分维度 |
|---------|---------|
| 爽文读者 | 爽感、节奏感、悬念感、代入感、升级感 |
| 文学爱好者 | 文笔质感、人物深度、主题表达、情感共鸣、叙事技巧 |
| 轻小说读者 | 趣味性、角色魅力、对话质感、画面感、轻松度 |

每种读者各维度 0-10 分 + 平均分 + 文字评语，另有亮点摘录和改进建议列表。按需调用，不嵌入写作流水线。

### 结构可视化

独立「可视化」页面，三个 tab，纯前端渲染，零 LLM 成本：

- **人物关系网络**：力导向图，节点按角色着色，边从人物关系 JSON 生成
- **伏笔分布甘特图**：横向条形图，颜色编码重要度，截止章节用菱形标记
- **情感曲线**：多折线图（审核评分 / 读者评分），附字数柱状图

### 协同写作

邀请码制的权限分离，适合主笔 + 审阅者的小团队协作。

| 操作 | 主笔（owner） | 审阅者（reviewer） |
|------|:---:|:---:|
| 查看所有内容 | Y | Y |
| 编辑世界观/人物/章纲/内容 | Y | - |
| AI 生成大纲/人物/章节 | Y | - |
| 伏笔管理（增删改） | Y | - |
| 模型/风格设置 | Y | - |
| 运行读者模拟 | Y | Y |
| 添加章节评论 | Y | Y |
| 审批章节（通过/需修改/驳回） | Y | Y |
| 查看可视化 / 导出 | Y | Y |

工作流：主笔创建项目 → 生成邀请码 → 协作者输入邀请码加入 → 主笔写作，审阅者评论和审批 → 重新生成章节时审批状态自动重置。

---

## 配置项说明

| 变量名 | 默认值 | 说明 |
|-------|-------|------|
| `ANTHROPIC_API_KEY` | — | Anthropic API 密钥 |
| `DEEPSEEK_API_KEY` | — | DeepSeek API 密钥 |
| `DOUBAO_API_KEY` | — | 豆包 API 密钥 |
| `QWEN_API_KEY` | — | 通义千问 API 密钥 |
| `GOOGLE_API_KEY` | — | Google AI API 密钥 |
| `DEFAULT_MODEL` | `claude-sonnet-4-6` | 全局默认模型 |
| `DATA_DIR` | `./data` | 数据存储根目录 |
| `LANCEDB_DIR` | `./data/lancedb` | LanceDB 持久化目录 |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | 向量化使用的 Embedding 模型 |
| `RECENT_CHAPTERS_COUNT` | `5` | Layer 2 记忆注入的近期章节数 |
| `VECTOR_TOP_K` | `10` | Layer 3 语义检索返回的片段数 |
| `CHUNK_SIZE` | `500` | 章节向量化分块大小（字符数） |
| `DEFAULT_CHAPTER_WORDS` | `3000` | 默认目标字数 |
| `AUTO_APPROVE_THRESHOLD` | `3` | 冲突严重度 < 此值时自动修复 |
| `MAX_VERSIONS` | `10` | 每章保留的最大历史版本数 |

---

## 常见问题

**Q: 生成速度很慢？**

写作 + 审核 + 润色三步共需 3 次 API 调用，对 Opus 模型来说每章约 1-3 分钟。可以关闭「自动润色」跳过第三步，或将写手部模型切换为 Sonnet/Haiku 提速。

**Q: 提示 "rate limit exceeded"？**

触发了 API 频率限制。系统已对长系统提示词启用 prompt caching，但高频连续写作仍可能触发限额。稍等 1-2 分钟后重试。

**Q: 审核报告冲突太多？**

审核师采用"零容忍主义"。可以：① 调高 `AUTO_APPROVE_THRESHOLD`（更多问题自动修复）；② 手动编辑修正；③ 直接继续写作。

**Q: LanceDB 初始化报错？**

确认 `data/lancedb` 目录有写权限，或指定其他路径：`LANCEDB_DIR=/tmp/lancedb`。首次启动会自动下载 Qwen3-Embedding 模型（约 1.2 GB），请确保网络畅通。

**Q: Word 导出提示缺少模块？**

```bash
pip install python-docx
```

**Q: 如何完整备份？**

复制 `data/` 目录即可。SQLite 在 `data/novels.db`，向量库在 `data/lancedb/`（Lance 格式，支持版本快照），导出文件在 `data/exports/`。

**Q: 修改世界观/人物后要全部重写吗？**

不一定。影响分析会按严重度排列受影响章节，低严重度可忽略，高严重度可单章重新生成或手动编辑。

**Q: 审阅者看不到编辑按钮？**

正常行为。审阅者所有写操作按钮都处于 disabled 状态，仅可查看内容、添加评论和审批章节。

**Q: 邀请码在哪里查看？**

主笔（owner）的侧边栏会显示邀请码。分享给协作者后，协作者在「项目管理 → 通过邀请码加入项目」输入即可。

**Q: Docker 部署后数据在哪里？**

数据保存在 Docker volume `neupen_data` 中。删除容器不会丢失数据，但 `docker compose down -v` 会删除 volume。备份可用 `docker cp` 导出 `/app/data` 目录。

**Q: API Key 在哪里配置？**

三种方式均可：① 启动后在页面内配置（推荐，保存在 `data/api_keys.json`）；② 编辑 `.env` 文件；③ Docker 环境变量注入。应用内配置优先级最高。

---

## 系统架构

### 整体分层

```
┌────────────────────────────────────────────────────────────┐
│                    表现层  ui/                             │
│          Streamlit Web UI（6 个功能页面）                    │
│  ui/pages/  · ui/components/  · ui/sidebar.py              │
└────────────────────────┬───────────────────────────────────┘
                         │ 调用
┌────────────────────────▼───────────────────────────────────┐
│            编排层  core/workflow.py + core/permissions.py   │
│   NovelWorkflow — 管理创作全流程的状态机                     │
│   permissions — 协同写作的身份验证与权限检查                  │
└──────┬─────────────────┬────────────────────┬──────────────┘
       │                 │                    │
┌──────▼──────┐  ┌───────▼───────┐  ┌────────▼────────┐
│  Agent 层   │  │   检测层       │  │   记忆层         │
│core/agents  │  │core/detector  │  │  core/memory     │
│             │  │               │  │                  │
│ OutlineAgent│  │ConflictDetect │  │ GlobalMemory     │
│ CharAgent   │  │               │  │ ChapterMemory    │
│ WriterAgent │  │ 设定冲突检测   │  │ FragmentMemory   │
│ ReviewAgent │  │ OOC 检测      │  │                  │
│ PolishAgent │  │ 影响范围分析   │  │ MemoryManager    │
│ ReaderAgent │  │               │  │                  │
└──────┬──────┘  └───────┬───────┘  └────────┬─────────┘
       │                 │                    │
┌──────▼─────────────────▼────────────────────▼─────────┐
│                    基础设施层  core/                     │
│                                                        │
│   models.py (SQLAlchemy ORM)    config.py              │
│   llm.py (多提供商 LLM 接口)                            │
│   SQLite: novels.db              LanceDB: data/lancedb/ │
└────────────────────────────────────────────────────────┘
```

### 目录结构

```
neupen/
├── app.py                    # 入口（~15 行）
├── core/                     # 后端核心包
│   ├── config.py             # 环境配置 + API Key 持久化
│   ├── models.py             # SQLAlchemy ORM
│   ├── llm.py                # 多提供商 LLM 接口
│   ├── memory.py             # 三层记忆系统
│   ├── detector.py           # 冲突检测
│   ├── agents.py             # 六大 Agent
│   ├── workflow.py           # 工作流编排
│   └── permissions.py        # 权限管理
├── ui/                       # 前端 Streamlit 包
│   ├── app.py                # 路由 + 会话状态
│   ├── sidebar.py            # 侧边栏导航
│   ├── helpers.py            # 格式化工具函数
│   ├── pages/                # 各功能页面
│   │   ├── project.py        # 项目管理
│   │   ├── settings.py       # 设定管理（6 个 tab）
│   │   ├── outline.py        # 大纲管理
│   │   ├── writing.py        # 写作 + 批量写作
│   │   ├── visualization.py  # 可视化
│   │   └── export.py         # 导出
│   └── components/           # 共用 UI 组件
│       ├── model_selector.py # 模型选择器
│       ├── api_key.py        # API Key 配置
│       ├── collaboration.py  # 评论/审批
│       └── alerts.py         # 影响报告/伏笔警告
├── utils/
│   └── export.py             # 多格式导出
└── scripts/                  # 构建脚本
```

### 模块职责速查

| 模块 | 职责 | 关键类/函数 |
|------|------|------------|
| `core/workflow.py` | 创作流程编排，对外统一入口 | `NovelWorkflow` |
| `core/agents.py` | 六大专职 Agent，封装 LLM 调用 | `*Agent` |
| `core/llm.py` | 多提供商 LLM 统一接口 | `NovelLLM`, `MODEL_REGISTRY` |
| `core/detector.py` | 冲突检测与变更影响分析 | `ConflictDetector`, `ReviewReport` |
| `core/memory.py` | 三层记忆系统，上下文构建 | `MemoryManager`, `GlobalMemory`, `ChapterMemory`, `FragmentMemory` |
| `core/models.py` | SQLAlchemy 数据模型 | `Novel`, `Chapter`, `Character`, `Collaborator`, `Comment`, ... |
| `core/permissions.py` | 协同写作权限管理 | `can_edit()`, `can_comment()`, `can_approve()` |
| `core/config.py` | 环境变量加载、常量定义、API Key 持久化 | `save_api_keys()`, `apply_saved_keys()` |
| `utils/export.py` | 多格式导出 | `NovelExporter` |

---

## 核心模块详解

### 六大 Agent

每个 Agent 有独立的系统提示词和明确的职责边界，互相之间不直接通信，全部通过 `NovelWorkflow` 编排。底层使用 `NovelLLM` 封装，对超过 1000 字符的系统提示词自动启用 Anthropic 的提示词缓存（`cache_control: ephemeral`），降低重复调用成本。

每个 Agent 可单独指定模型（三级回退：per-agent → 项目默认 → 全局默认），实现按需配置。

```
┌─────────────────────────────────────────────────────────┐
│                      NovelLLM                            │
│  generate(system, user, cache_system=True)               │
│  generate_stream(system, user)  ← 流式输出              │
│                                                          │
│  provider=="anthropic" → anthropic.Anthropic             │
│  其他提供商 → openai.OpenAI(base_url=...)                │
└──────┬──────┬──────┬──────┬──────┬──────┬───────────────┘
       │      │      │      │      │      │
   Outline Char  Writer Review Polish Reader
   Agent   Agent  Agent  Agent  Agent  Agent
```

- **大纲师** `OutlineAgent`：输入一句话灵感，输出结构化大纲 JSON（总纲 + 卷纲 + 章纲）。有活跃伏笔时自动注入调度表。
- **人设师** `CharacterAgent`：根据大纲生成结构化人物档案，可批量检测人物设定矛盾。
- **写手部** `WriterAgent`：基于三层记忆整合的上下文 + 章纲生成正文，支持流式输出。
- **审核师** `ReviewerAgent`：五类冲突检测（设定/OOC/大纲/矛盾/逻辑），严重度 < 3 自动修复。
- **润色师** `PolisherAgent`：消除 AI 痕迹、增强文学性，支持风格迁移。
- **读者模拟** `ReaderAgent`：三种读者视角的体验评分，按需调用。

### 三层记忆系统

长篇小说的核心难点是长程上下文管理。将记忆分为三层，针对不同时效和检索需求分别处理：

```
┌─────────────────────────────────────────────────────────┐
│                    MemoryManager                         │
│         build_writing_context()  ← 写作前调用            │
│         build_review_context()   ← 审核前调用            │
│         save_new_chapter()       ← 写完后同步三层         │
└───────────┬───────────────┬─────────────────┬───────────┘
            │               │                 │
   ┌────────▼──────┐ ┌──────▼──────┐ ┌────────▼────────┐
   │ Layer 1       │ │ Layer 2     │ │ Layer 3          │
   │ GlobalMemory  │ │ChapterMemory│ │ FragmentMemory   │
   │               │ │             │ │                  │
   │ SQLite 永久   │ │ SQLite 中期 │ │ LanceDB 向量    │
   │               │ │             │ │                  │
   │ · 世界观设定  │ │ · 最近N章   │ │ · 全量章节分块   │
   │ · 人物档案    │ │   正文+摘要 │ │   向量化存储     │
   │ · 总/卷大纲   │ │ · 版本历史  │ │                  │
   │ · 所有章纲    │ │             │ │ 按语义相似度检索 │
   │ · 伏笔库      │ │ 时序检索    │ │ （处理远距离     │
   │ · 时间线      │ │             │ │  细节呼应）      │
   └───────────────┘ └─────────────┘ └──────────────────┘
```

- **Layer 1 — 全局记忆（永久，SQLite）**：世界观、人物档案、大纲、伏笔库、时间线。
- **Layer 2 — 章节记忆（中期，SQLite）**：最近 5 章的正文和摘要。
- **Layer 3 — 碎片化记忆（向量，LanceDB + Qwen3-Embedding）**：按 500 字分块向量化，中文语义检索。单表多小说，支持跨小说检索和 Lance 原生版本快照。

**写作上下文构建顺序**：全局设定（L1）+ 近期章节（L2）+ 语义相关片段（L3）+ 当前章纲 → WriterAgent 输入。

### 冲突检测与变更同步

| 检测类型 | 示例 |
|---------|------|
| 设定冲突 | 无魔力体质的角色突然施法 |
| OOC | 冷漠型角色突然变得热情 |
| 大纲冲突 | 章纲要求决战，正文写的是郊游 |
| 前后矛盾 | 角色昨天在A城，今天无理由出现在B城 |
| 逻辑漏洞 | 锁着的门没人开但角色进去了 |

每个冲突项含：类型、严重度（1-10）、引用原文、修复方案。修改世界观/章纲后自动触发影响分析，返回受影响章节列表。

### 工作流编排

```
write_and_review_chapter(chapter_number, word_target, auto_polish)
│
├── 1. WriterAgent.write_chapter()        → 生成草稿（流式输出）
├── 2. ReviewerAgent.review_chapter()     → 冲突检测
├── 3. ReviewerAgent.auto_fix_minor()     → 自动修复轻微问题
├── 4. PolisherAgent.polish_chapter()     → 文笔润色（可选）
├── 5. save → SQLite + LanceDB + 版本历史
├── 6. summarize_chapter()                → 摘要供后续记忆注入
└── 7. chapter.approval_status = "pending"
```

批量写作：选择章节范围后一键按顺序执行，单章失败不中断后续，完成后汇总报告。

---

## 数据模型

所有数据存储在 `data/novels.db`（SQLite），使用 SQLAlchemy ORM 管理。

```
Novel (小说项目)
│  id, title, logline, genre, world_setting(JSON), writing_style, status
│  llm_model, model_outline/character/writer/reviewer/polisher/reader
│  style_profile(JSON), style_reference_text, invite_code
│
├── NovelOutline (总大纲)
├── Volume (卷) [多个]
├── Chapter (章节) [多个]
│   ├── ContentVersion (版本历史) [多个]
│   └── Comment (章节评论) [多个]
├── Character (人物档案) [多个]
├── Foreshadowing (伏笔) [多个]
├── TimelineEvent (时间线) [多个]
└── Collaborator (协作者) [多个]
```

**章节状态流转**：`outline_pending → outlined → writing → review_pending → reviewed → polished → published`

---

## 未来方向

1. **API 服务化**：将 `NovelWorkflow` 封装为 FastAPI 接口，解耦前端，支持移动端接入
2. **增量大纲扩展**：智能分析已有章节密度，100 章扩展至 200 章时自动在情节稀疏处插入新章纲
3. **写作分析面板**：统计用户修改频次最高的问题类型，自动调整 Agent 生成偏好
4. **多语言支持**：支持英文、日文等语言的小说创作
5. **移动端适配**：响应式 UI 优化，支持平板/手机端操作
