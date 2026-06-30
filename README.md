# Neupen

> AI 驱动的中长篇小说创作系统。从一句话灵感出发，八个专职 Agent 协同完成大纲、人物、正文全流程创作，三层记忆系统保障跨章节叙事连贯。内置冲突检测、风格迁移、伏笔调度、读者模拟与平台风格适配，支持多模型供应商、协同写作与四种格式导出。

---

## 目录

- [快速开始](#快速开始)
- [功能使用指南](#功能使用指南)
- [多模型分工](#多模型分工)
- [功能特性](#功能特性)
  - [风格迁移](#风格迁移)
  - [平台风格适配](#平台风格适配)
  - [智能伏笔调度](#智能伏笔调度)
  - [读者模拟](#读者模拟)
  - [结构可视化](#结构可视化)
  - [协同写作](#协同写作)
  - [AI 创作助手](#ai-创作助手)
  - [去 AI 味写作](#去-ai-味写作)
- [导出格式](#导出格式)
- [配置项说明](#配置项说明)
- [常见问题](#常见问题)
- [系统架构](#系统架构)
- [核心模块详解](#核心模块详解)
  - [八大 Agent](#八大-agent)
  - [三层记忆系统](#三层记忆系统)
  - [冲突检测与变更同步](#冲突检测与变更同步)
  - [工作流编排](#工作流编排)
- [数据模型](#数据模型)
- [未来方向](#未来方向)

---

## 快速开始

提供三种部署方式，选择最适合你的：

### 方式一：Docker 一键部署（推荐）

无需安装 Python 环境，一条命令启动。

**前提**：需要先安装并启动 [Docker Desktop](https://www.docker.com/products/docker-desktop/)。Mac 用户可通过 Homebrew 安装：

```bash
brew install --cask docker
```

安装完成后打开 Docker Desktop，等待菜单栏鲸鱼图标变为 Running 状态，再执行：

```bash
git clone https://github.com/FantasyLu/neupen.git && cd neupen
docker compose up -d
```

访问 `http://localhost:8501`，首次启动在页面内配置 API Key 即可。

**环境变量注入（可选）**：也可通过 docker-compose 预设 Key：

```bash
ANTHROPIC_API_KEY=sk-xxx docker compose up -d
```

**数据持久化**：数据存储在 Docker volume `neupen_data` 中，包含数据库、向量库和导出文件。重建容器不会丢失数据。

### 方式二：Mac 客户端

1. 从 [GitHub Releases](https://github.com/FantasyLu/neupen/releases) 下载 `Neupen.dmg`
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
GEMINI_API_KEY=AIzaxxxxxxxx
```

#### 4. 启动

```bash
streamlit run app.py
```

访问 `http://localhost:8501`，输入显示名称进入系统。

### 典型工作流

```
① 项目管理页 → 新建项目，填写标题 + 作者 + 一句话灵感 + 平台/标签，勾选「立即生成大纲」
                 ↓ 自动生成邀请码，可分享给协作者
② 大纲管理页 → 检查/微调章纲，管理卷大纲（排序/新建/删除），查看伏笔调度警告
                 ↓ 侧边栏 AI 创作助手常驻，讨论大纲/人物/设定/章节均可一键写入
③ 设定管理页 → 查看 AI 生成的人物档案（支持手动编辑所有字段），补充世界观细节
               → 配置文档设定（世界观背景、力量体系、社会架构等 Markdown 文档）
               → 模型设置 tab 配置各 Agent 分工
               → 风格迁移 tab 上传参考文本提取风格
               → 平台风格 tab 选择目标发布平台和标签（与新建项目互通）
               → 写作质量 tab 调整自动审核阈值等参数
                 ↓
④ 写作页     → 逐章生成，查看审核报告，运行读者模拟
               → 编辑章节摘要 / 手动调整审核评分和读者评分
               → 章节状态回退（单章或批量）
               → 审阅者可添加评论、审批章节
                 ↓
⑤ 可视化页   → 人物关系网络 / 伏笔分布 / 情感曲线
               → AI 同步人物关系（从已写章节自动提取）
                 ↓
⑥ 导出页     → 选择 TXT / Markdown / Word / EPUB 格式导出
```

---

## 功能使用指南

### 项目管理

- **我的项目**：展示所有项目的字数/章数进度，点击「打开」进入
- **新建项目**：填写标题 + 作者/笔名 + 灵感 + 平台/标签（可选）+ 选择模型 + 勾选「立即生成大纲」→ 自动生成邀请码
- **灵感对话**：与 AI 多轮聊天理清思路，聊完后一键提取项目配置并创建
- **加入项目**：输入邀请码以审阅者身份加入他人项目
- **删除项目**：两步确认，级联删除所有关联数据

### 设定管理（9 个 tab）

| Tab | 功能 |
|-----|------|
| 文档设定 | Markdown 格式的世界观背景文档，支持多类型（背景/力量体系/人物/自定义），AI 建议一键应用 |
| 世界观 | 结构化键值编辑（世界规则、力量体系、社会结构、地理等），保存时自动触发影响分析 |
| 人物档案 | AI 生成 / 手动新建 / 完整编辑所有 16 个字段，保存时触发设定冲突检测 |
| 伏笔 | 手动记录 / AI 分配截止章节 / 同步大纲伏笔 / 完整编辑已有伏笔，按紧急度排序 |
| 模型 | 项目默认模型 + 各 Agent 分工配置 + 一键推荐配置 |
| 风格 | 粘贴/上传参考文本 → 分析 → 编辑 10 维特征 → 保存；或从已写章节自动提取风格 |
| 平台 | 选择目标发布平台和标签，平台特有写作规则自动注入写作 prompt |
| 写作质量 | 自动审核阈值、评分标准、最大修复轮次、低分重写阈值等参数 |
| API Key | 在页面内管理各提供商的 API Key，保存后立即生效 |

### 大纲管理

- **AI 大纲助手**（左侧栏）：已整合至侧边栏全局 AI 创作助手，支持讨论大纲并一键写入
- **总体大纲**：Markdown 编辑器，保存时自动解析为结构化字段
- **卷大纲管理**：新建/编辑/删除/排序（上移/下移），设置起止章节和剧情概要
- **章节大纲**：
  - 批量 AI 生成，支持从卷大纲自动填充起止章节和剧情描述
  - 状态和关键词筛选
  - 完整编辑（核心事件、冲突、场景、情感弧、登场人物、伏笔埋下/回收、章尾）
  - 修改核心字段触发下游影响分析
- **文档导入**：自由格式文本导入，AI 解析为结构化大纲/世界观/人物/章纲，预览确认后写入
- 伏笔调度警告面板（过期/即将到期）

### 写作

- **AI 写作助手**（左侧栏）：已整合至侧边栏全局 AI 创作助手，支持对话式辅助修改，输出的章节代码块可一键保存
- **生成流水线**：写作 → 四审核**并行**（剧情对齐 / 人设世界观 / 时空状态 / 文风去AI，各权重 25%，已通过的关卡下轮跳过，最多 2 轮）→ 可选润色 → 摘要，一键完成
- **正文编辑**：手动改正文，保存时触发同步检测（新人物/人物变化/大纲偏离/世界观变更），逐项确认或跳过
- **章节摘要**：查看和编辑 AI 生成的摘要和关键事件
- **审核报告**：四审核得分及反馈逐项展示，支持逐项 AI 讨论和一键修复，手动编辑审核评分
- **读者模拟**：三种读者视角评分卡片 + 亮点/建议，手动编辑读者评分
- **审批状态**：通过 / 需修改 / 驳回，重新生成自动重置
- **章节评论**：主笔和审阅者均可评论
- **版本历史**：最近 10 个版本（草稿/审核/润色/用户编辑）
- **批量写作**：选择章节范围一键按顺序生成，单章失败不中断，完成后汇总报告
- **状态回退**：单章回退（已发布 → 待审核）和批量回退

### 可视化

- **人物关系网络**：力导向图（streamlit-agraph），节点按角色着色/大小区分主次，边标签精简为 4 字，hover 显示完整双向关系
- **AI 同步人物关系**：一键从已写章节中提取人物关系并写入人物档案，逐章分析带进度条
- **伏笔分布甘特图**：横向条形图，颜色编码重要度，截止章节用菱形标记，支持筛选已回收
- **情感曲线**：多折线图（审核评分 / 各类型读者评分），可选指标筛选，附字数柱状图和各章情感基调

---

## 多模型分工

支持 5 家提供商 12 个模型，可为每个 Agent 单独配置：

| 提供商 | 模型 | 上下文 | 速度 | 成本 | 特色 |
|-------|------|--------|------|------|------|
| Anthropic | Claude Opus 4.6 | 200K | 慢 | 高 | 最高质量，支持 prompt caching |
| Anthropic | Claude Sonnet 4.6 | 200K | 中 | 中 | 性价比最优 |
| Anthropic | Claude Haiku 4.5 | 200K | 快 | 低 | 快速任务 |
| DeepSeek | DeepSeek V3 (chat) | 64K | 快 | 极低 | 中文网文优化 |
| DeepSeek | DeepSeek R1 (reasoner) | 64K | 中 | 低 | 推理能力突出 |
| 字节豆包 | Doubao Pro 32K | 32K | 快 | 低 | 都市言情优化 |
| 字节豆包 | Doubao Lite 32K | 32K | 极快 | 极低 | 快速草稿 |
| 阿里通义千问 | Qwen Max | 32K | 中 | 中 | 东方文化理解 |
| 阿里通义千问 | Qwen Plus | 131K | 快 | 低 | 均衡之选 |
| 阿里通义千问 | Qwen Turbo | 1M | 极快 | 极低 | 超长上下文 |
| Google | Gemini 2.0 Flash | 1M | 快 | 低 | 创意世界观构建 |
| Google | Gemini 1.5 Pro | 1M | 中 | 中 | 多线叙事 |

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

上传喜欢的作家作品片段（500-3000 字），AI 自动提取 10 维风格特征，润色时忠实复现该风格。也可从已完成的章节中自动提取写作风格。

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

### 平台风格适配

针对不同网文发布平台的读者口味，内置平台和标签风格描述，写作时自动注入对应的风格规则。

#### 内置平台

| 平台 | 内置标签 |
|------|---------|
| 起点中文网 | 系统流、无敌流、种田流、无限流、诸天流 |
| 晋江文学城 | 甜宠、年代文、宫斗、仙侠 |
| 番茄小说 | 赘婿逆袭、神医、战神归来 |
| 掌阅 | 都市异能、末世求生 |

支持自定义平台和标签，每个标签可配置独立的写作风格描述。在设定管理的「平台」tab 中选择目标平台和标签后，对应的风格规则会自动注入写作 prompt。

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

- **人物关系网络**：力导向图，节点按角色着色，主要人物节点更大，边标签精简显示，hover 查看完整双向关系。支持一键 AI 同步（从已写章节提取人物关系）
- **伏笔分布甘特图**：横向条形图，颜色编码重要度，截止章节用菱形标记
- **情感曲线**：多折线图（审核评分 / 读者评分），附字数柱状图

### 协同写作

邀请码制的权限分离，适合主笔 + 审阅者的小团队协作。

| 操作 | 主笔（owner） | 审阅者（reviewer） |
|------|:---:|:---:|
| 查看所有内容 | ✓ | ✓ |
| 编辑世界观/人物/章纲/内容 | ✓ | - |
| AI 生成大纲/人物/章节 | ✓ | - |
| 伏笔管理（增删改） | ✓ | - |
| 模型/风格/质量设置 | ✓ | - |
| 运行读者模拟 | ✓ | ✓ |
| 添加章节评论 | ✓ | ✓ |
| 审批章节（通过/需修改/驳回） | ✓ | ✓ |
| 查看可视化 / 导出 | ✓ | ✓ |

**在线状态**：侧边栏实时显示在线协作者及其所在页面（10 分钟活动超时）。

工作流：主笔创建项目 → 生成邀请码 → 协作者输入邀请码加入 → 主笔写作，审阅者评论和审批 → 重新生成章节时审批状态自动重置。

### AI 创作助手

侧边栏常驻统一的 **AI 创作助手**（CanvasAgent），贯穿所有页面，支持自然语言对话辅助创作。不再有页面内的独立聊天框——全局助手感知当前页面上下文，自动注入大纲、章节、设定等关联信息。

AI 生成的内容通过 **类型化代码块** 一键写入对应位置，支持 8 种目标类型：

| 代码块类型 | 写入目标 | 行为 |
|-----------|---------|------|
| `outline` | 整体大纲 | 写入编辑器并自动保存到数据库 |
| `settings` | 设定文档 | 写入后自动持久化 |
| `world` | 世界观键值对 | JSON 格式，合并到现有世界观 |
| `characters` | 人物档案 | JSON 数组，支持增删改（`action: "delete"` 删除） |
| `chapter` | 章节正文 | 自动写入编辑器并保存 |
| `volume` | 卷大纲 | JSON 格式，保存后跳转大纲管理页 |
| `foreshadowing` | 伏笔库 | JSON 数组，批量添加伏笔条目 |
| `style` | 写作风格 | 自动提炼用户风格偏好并同步到 style_profile |

若 AI 未使用代码块格式，对话下方会自动出现 **「应用到…」兜底选择器**，用户手动选择写入目标（自动检测 / 大纲 / 设定 / 世界观 / 人物 / 章节），确认后自动剥离对话前缀，只保留实质性内容写入。

### 去 AI 味写作

WriterAgent 系统提示词内置去 AI 味规则，从生成阶段即避免 AI 痕迹：

1. **禁用通感式滥用比喻**：禁止"仿佛"、"如同"开头的段落，禁止连续使用两个以上比喻
2. **消灭总结式段尾**：禁止段末出现"他知道…"、"这一刻他明白了…"等心理总结句
3. **打破三段式节奏**：禁止连续出现"描写-动作-感悟"的固定循环
4. **去除虚假的优美**：禁止空洞的抒情描写，一切描写须服务于叙事推进或人物塑造

---

## 导出格式

| 格式 | 适用场景 | 特色选项 |
|------|---------|---------|
| TXT | 任意设备阅读 | 可附大纲摘要，按卷分组 |
| Markdown | Obsidian / Typora | YAML Front Matter，含目录、人物档案、伏笔追踪表 |
| Word (.docx) | 出版投稿 | 规范版式（宋体/黑体），自动处理段落缩进和对话格式 |
| EPUB | Kindle / Apple Books | 电子书元数据（作者/语言），CSS 排版，按卷分组目录，可附人物档案和伏笔追踪 |

导出页面顶部显示可导出章节数、总字数、小说名称和作者信息。已导出的文件列表（最近 10 个）支持直接下载。

---

## 配置项说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|-------|-------|------|
| `ANTHROPIC_API_KEY` | — | Anthropic API 密钥 |
| `DEEPSEEK_API_KEY` | — | DeepSeek API 密钥 |
| `DOUBAO_API_KEY` | — | 豆包 API 密钥 |
| `QWEN_API_KEY` | — | 通义千问 API 密钥 |
| `GEMINI_API_KEY` | — | Google Gemini API 密钥 |
| `DEFAULT_MODEL` | `claude-opus-4-6` | 全局默认模型 |
| `DATA_DIR` | `./data` | 数据存储根目录 |
| `LANCEDB_DIR` | `./data/lancedb` | LanceDB 持久化目录 |
| `EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | 向量化使用的 Embedding 模型 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `DEBUG_PROMPTS` | `0` | 设为 `1` 时在每次 LLM 调用时将 system/user prompt 打印到 stderr |
| `LLM_RETRY_MAX_ATTEMPTS` | `4` | LLM 请求失败后的最大重试次数（含首次），429/5xx/超时自动重试，401 不重试 |
| `LLM_RETRY_BASE_DELAY` | `2.0` | 首次重试等待秒数（指数退避，每次翻倍） |
| `LLM_RETRY_MAX_DELAY` | `60.0` | 单次重试等待上限（秒） |

### 记忆系统参数

| 变量名 | 默认值 | 说明 |
|-------|-------|------|
| `RECENT_CHAPTERS_COUNT` | `5` | Layer 2 记忆注入的近期章节数 |
| `VECTOR_TOP_K` | `10` | Layer 3 语义检索返回的片段数 |
| `CHUNK_SIZE` | `500` | 章节向量化分块大小（字符数） |
| `COMPRESS_WORLD_THRESHOLD` | `300` | 世界观单条 value 超过此字符数时触发 LLM 压缩 |
| `COMPRESS_WORLD_TARGET_MAX` | `400` | 世界观压缩后的目标字符上限（LLM 参考值，不硬截断） |
| `COMPRESS_OUTLINE_THRESHOLD` | `150` | 大纲字段超过此字符数时触发 LLM 压缩 |
| `COMPRESS_OUTLINE_THEME_TARGET` | `100` | 大纲「核心主题」字段压缩目标字符数 |
| `COMPRESS_OUTLINE_CONFLICT_TARGET` | `300` | 大纲「主要矛盾」字段压缩目标字符数 |
| `COMPRESS_OUTLINE_ARC_TARGET` | `500` | 大纲「主角弧光」字段压缩目标字符数 |
| `COMPRESS_OUTLINE_ENDING_TARGET` | `500` | 大纲「结局概要」字段压缩目标字符数 |

### 写作质量参数

| 变量名 | 默认值 | 说明 |
|-------|-------|------|
| `DEFAULT_CHAPTER_WORDS` | `3000` | 默认目标字数 |
| `WORD_COUNT_TOLERANCE` | `0.30` | 字数容差比例（30%） |
| `GATE_CONTEXT_THRESHOLD` | `8.5` | 关卡1（局部校对）熔断阈值 |
| `GATE_CONTINUITY_THRESHOLD` | `9.0` | 关卡2（全局场记）熔断阈值 |
| `GATE_STYLISTIC_THRESHOLD` | `8.0` | 关卡3（文风打磨）熔断阈值 |
| `MAX_GATE_RETRIES` | `2` | 每个关卡的最大重试次数 |
| `AUTO_APPROVE_THRESHOLD` | `6` | 自动通过的审核评分阈值 |
| `REVIEW_SCORE_THRESHOLD` | `7.0` | 审核通过的最低评分 |
| `LOW_SCORE_REWRITE_THRESHOLD` | `7.0` | 触发全文重写的评分阈值 |
| `MAX_REVIEW_ITERATIONS` | `5` | 单次尝试中的最大修复轮次 |
| `MAX_TOTAL_ATTEMPTS` | `10` | 最大重写尝试次数 |
| `MAX_VERSIONS` | `10` | 每章保留的最大历史版本数 |

写作质量参数也支持在设定管理的「写作质量」tab 中按项目单独配置。

---

## 常见问题

**Q: 生成速度很慢？**

写作 + 审核修复循环 + 润色共需多次 API 调用，对 Opus 模型来说每章约 1-3 分钟。可以关闭「自动润色」跳过润色步骤，或将写手部模型切换为 Sonnet/Haiku 提速。

**Q: 提示 "rate limit exceeded"？**

触发了 API 频率限制。系统已对长系统提示词启用 prompt caching，且内置指数退避重试（最多 4 次，首次等 2 秒，上限 60 秒），偶发性限流会自动恢复。持续触发说明调用频率过高，稍等 1-2 分钟后重试，或调低并发写作频率。

**Q: 审核报告冲突太多？**

审核师采用"零容忍主义"。可以：① 在设定管理「写作质量」tab 调高自动通过阈值；② 手动编辑修正；③ 直接继续写作。

**Q: LanceDB 初始化报错？**

确认 `data/lancedb` 目录有写权限，或指定其他路径：`LANCEDB_DIR=/tmp/lancedb`。首次启动会自动下载 Qwen3-Embedding 模型（约 1.2 GB），请确保网络畅通。

**Q: Word / EPUB 导出提示缺少模块？**

```bash
pip install python-docx EbookLib
```

**Q: 如何完整备份？**

复制 `data/` 目录即可。SQLite 在 `data/novels.db`，向量库在 `data/lancedb/`（Lance 格式，支持版本快照），导出文件在 `data/exports/`，API Key 在 `data/api_keys.json`。

**Q: 修改世界观/人物后要全部重写吗？**

不一定。影响分析会按严重度排列受影响章节，低严重度可忽略，高严重度可单章重新生成或手动编辑。

**Q: 小说写到后期（100 章以上），API 调用越来越慢或报 context limit 错误？**

系统已内置多层上下文精简策略：世界观/大纲字段超长时由 LLM 自动压缩并缓存精简版（首次调用时压缩，后续直接读缓存）、世界观按本章关键词过滤、人物档案区分出场/未出场、审核正文截断至 6000 字、向量检索最多 3 片段。若仍超限，可将模型切换为 Qwen Turbo / Gemini 等 1M 长上下文模型。

**Q: 审阅者看不到编辑按钮？**

正常行为。审阅者所有写操作按钮都处于 disabled 状态，仅可查看内容、添加评论和审批章节。

**Q: 邀请码在哪里查看？**

主笔（owner）的侧边栏会显示邀请码。分享给协作者后，协作者在「项目管理 → 通过邀请码加入项目」输入即可。

**Q: Docker 部署后数据在哪里？**

数据保存在 Docker volume `neupen_data` 中。删除容器不会丢失数据，但 `docker compose down -v` 会删除 volume。备份可用 `docker cp` 导出 `/app/data` 目录。

**Q: API Key 在哪里配置？**

三种方式均可：① 启动后在页面内配置（推荐，保存在 `data/api_keys.json`）；② 编辑 `.env` 文件；③ Docker 环境变量注入。应用内配置优先级最高。

**Q: 章节写完后发现不满意，如何回退？**

写作页提供章节状态回退功能。已发布的章节可以回退到「待审核」状态重新生成，也支持批量回退多章。

**Q: 人物关系图为空？**

需要先在人物档案的 relationships 字段中添加关系描述（JSON 格式），或在可视化页面点击「AI 同步人物关系」从已写章节中自动提取。

---

## 系统架构

### 整体分层

```
┌────────────────────────────────────────────────────────────┐
│                    表现层  ui/                              │
│          Streamlit Web UI（7 个功能页面）                    │
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
│ IdeaAgent   │  │               │  │                  │
│ CanvasAgent │  │               │  │                  │
└──────┬──────┘  └───────┬───────┘  └────────┬─────────┘
       │                 │                    │
┌──────▼─────────────────▼────────────────────▼─────────┐
│                    基础设施层  core/                     │
│                                                        │
│   models.py (SQLAlchemy ORM)    config.py              │
│   llm.py (多提供商 LLM 接口)    platform_styles.py     │
│   SQLite: novels.db              LanceDB: data/lancedb/ │
└────────────────────────────────────────────────────────┘
```

### 目录结构

```
neupen/
├── app.py                    # 入口
├── core/                     # 后端核心
│   ├── config.py             # 环境配置 + API Key 持久化
│   ├── models.py             # SQLAlchemy ORM + 自动迁移
│   ├── llm.py                # 多提供商 LLM 接口 + MODEL_REGISTRY
│   ├── memory.py             # 三层记忆系统
│   ├── detector.py           # 冲突检测 + 影响分析
│   ├── agents.py             # 八大 Agent
│   ├── workflow.py           # 工作流编排
│   ├── permissions.py        # 权限管理
│   └── platform_styles.py    # 平台/标签风格系统
├── ui/                       # 前端 Streamlit
│   ├── app.py                # 路由 + 会话状态
│   ├── sidebar.py            # 侧边栏导航 + 在线状态 + 全局 AI 助手
│   ├── helpers.py            # 格式化工具函数
│   ├── pages/                # 功能页面
│   │   ├── project.py        # 项目管理 + 灵感对话
│   │   ├── settings.py       # 设定管理（9 个 tab）
│   │   ├── outline.py        # 大纲管理 + 文档导入
│   │   ├── writing.py        # 写作 + 批量写作 + 状态回退
│   │   ├── visualization.py  # 可视化（关系图/甘特图/曲线）
│   │   ├── export.py         # 导出（TXT/MD/DOCX/EPUB）
│   │   └── platform_styles.py # 平台风格管理
│   └── components/           # 共用 UI 组件
│       ├── model_selector.py # 模型选择器 + 模型卡片
│       ├── api_key.py        # API Key 配置 + 首次引导
│       ├── collaboration.py  # 评论/审批/身份对话
│       ├── alerts.py         # 影响报告/伏笔警告
│       └── global_chat.py    # AI 创作助手侧栏
├── utils/
│   └── export.py             # 多格式导出（NovelExporter）
├── scripts/                  # 构建脚本
│   ├── build_mac.sh          # PyInstaller 打包 .app
│   ├── create_dmg.sh         # 封装 .dmg 安装包
│   └── launcher.py           # macOS 启动器
├── Dockerfile                # Docker 镜像构建
├── docker-compose.yml        # Docker Compose 编排
├── requirements.txt          # Python 依赖
└── .env.example              # 环境变量模板
```

---

## 核心模块详解

### 八大 Agent

每个 Agent 有独立的系统提示词和明确的职责边界，互相之间不直接通信，全部通过 `NovelWorkflow` 编排。底层使用 `NovelLLM` 封装，对超过 1000 字符的系统提示词自动启用 Anthropic 的提示词缓存（`cache_control: ephemeral`），降低重复调用成本。

每个 Agent 可单独指定模型（三级回退：per-agent → 项目默认 → 全局默认），实现按需配置。

```
┌─────────────────────────────────────────────────────────┐
│                      NovelLLM                            │
│  generate(system, user)         generate_stream()        │
│  generate_chat()                                         │
│                                                          │
│  provider=="anthropic" → anthropic.Anthropic             │
│  其他提供商 → openai.OpenAI(base_url=...)                │
└───┬──────┬──────┬──────┬──────┬──────┬──────┬──────┬────┘
    │      │      │      │      │      │      │      │
 Outline Char Writer Review Polish Reader Idea Canvas
 Agent   Agent Agent  Agent  Agent  Agent Agent Agent
```

| Agent | 角色 | 关键方法 |
|-------|------|---------|
| 大纲师 `OutlineAgent` | 生成结构化大纲 JSON（总纲 + 卷纲 + 章纲） | `generate_full_outline()`, `generate_chapter_range_outlines()`, `refine_chapter_outline()`, `analyze_chapter_consistency()`, `extract_relationships()`, `parse_document()` |
| 人设师 `CharacterAgent` | 生成人物档案，检测人物矛盾（分批处理，每批最多 10 人） | `generate_characters()`, `check_character_consistency()`, `update_character_state()` |
| 写手部 `WriterAgent` | 基于三层记忆 + 章纲生成正文，支持流式输出 | `write_chapter()`, `summarize_chapter()`, `regenerate_section()` |
| 审核师 `ReviewerAgent` | 四审核**并行**流水线（剧情对齐 / 人设世界观 / 时空状态 / 文风去AI，各 25% 权重），并行执行后合并 REJECT feedback 由 WriterAgent 统一修正，已通过关卡下轮跳过（最多 2 轮）；旧版串行三关卡保留兼容 | `parallel_pipeline_review()`, `pipeline_review()`（旧版兼容）, `fix_all_issues()`, `auto_fix_minor_issues()`, `review_chapter()`（旧版兼容） |
| 润色师 `PolisherAgent` | 消除 AI 痕迹、增强文学性，支持风格迁移 | `polish_chapter()`, `apply_style_to_selection()`, `analyze_style()` |
| 读者模拟 `ReaderAgent` | 三种读者视角的体验评分 | `evaluate_chapter()` |
| 灵感师 `IdeaAgent` | 多轮创意对话，提取项目配置 | `chat()`, `extract_project_config()` |
| 画布助手 `CanvasAgent` | 侧边栏全局 AI 助手，感知小说上下文（按章节关键词过滤世界观，文档截断至 6000 字）；system prompt 按当前页面动态裁剪（只注入本页用得上的代码块模板）；意图分类器区分「目标章节」和「参考章节」，支持注入参考章节摘要后调用 `refine_chapter_outline()` 精准修改 | `chat()` — 输出 8 种类型化代码块，一键应用到对应位置 |

### 三层记忆系统

长篇小说的核心难点是长程上下文管理。将记忆分为三层，针对不同时效和检索需求分别处理：

```
┌─────────────────────────────────────────────────────────┐
│                    MemoryManager                         │
│         build_writing_context()  ← 写作前调用            │
│         build_review_context()   ← 审核前调用            │
│         save_new_chapter()       ← 写完后同步三层         │
│         get_status_report()      ← 项目状态报告          │
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

- **Layer 1 — 全局记忆（永久，SQLite）**：世界观、人物档案、大纲、伏笔库、时间线。全量 CRUD 接口，支持伏笔截止管理。
- **Layer 2 — 章节记忆（中期，SQLite）**：最近 5 章的正文和摘要。版本历史管理（最多 10 个版本）。
- **Layer 3 — 碎片化记忆（向量，LanceDB + Qwen3-Embedding）**：按 500 字分块向量化，中文语义检索。单表多小说，支持跨小说检索和 Lance 原生版本快照。

**写作上下文构建顺序**：全局设定（L1，按出场人物和关键词过滤）+ 近期章节（L2，自适应章数）+ 语义相关片段（L3，最多 3 片段，相关度 ≥ 0.5）+ 当前章纲 → WriterAgent 输入。

#### 上下文精简策略

为避免长篇小说后期上下文爆炸，各 Agent 均采用主动截断策略：

| 位置 | 策略 |
|------|------|
| 人物档案 | 本章出场人物给完整档案，其余人物仅注入单行简介（姓名+角色+核心特征） |
| 世界观设定 | 按章纲关键词过滤，只注入与本章相关的条目；超过 300 字的条目在首次读取时由 LLM 压缩为精简版并缓存，后续调用直接读缓存 |
| 大纲字段 | theme/main_conflict/protagonist_arc/ending_summary 超过阈值时由 LLM 按字段语义压缩并缓存，后续调用直接读缓存 |
| 章节摘要 | L2 层按总章数自适应：50 章以内取 5 章，50-100 章取 4 章，100 章以上取 3 章 |
| 向量检索 | L3 层最多返回 3 个片段，相关度低于 0.5 自动丢弃 |
| 待审正文 | 所有关卡截断至 6000 字 |
| 润色正文 | 截断至 8000 字 |
| Canvas 文档 | 截断至 6000 字，从 system prompt 移至 user 消息注入 |
| 人设一致性检测 | 每批最多 10 人，单人档案截断至 800 字 |

### 冲突检测与变更同步

**四审核并行流水线**（主流程）四个 Reviewer 同时执行，每个专注单一维度：

| Reviewer | 检测维度 | 示例 |
|----------|---------|------|
| `plot_aligner` 剧情对齐 | 大纲偏离、情节一致性 | 章纲要求决战，正文写的是郊游 |
| `character_guard` 人设世界观 | 人物 OOC、世界观冲突 | 冷漠型角色突然热情；角色掌握了设定中不存在的能力 |
| `continuity_tracker` 时空状态 | 状态连续性、时空矛盾 | 上章左臂废了这章用左手攀爬；角色无理由跨城瞬移 |
| `style_refiner` 文风去AI | 去 AI 痕迹、写作风格 | 连续碎句、总结式段尾、上帝视角滥用 |

四审全 PASS → 加权最终得分（各 25%）；有任意 REJECT → 合并所有 feedback 交 WriterAgent **一次性修正** → 仅未通过的关卡重审，已通过的跳过，最多 2 轮。

**旧版串行三关卡**（`pipeline_review()`，保留兼容）依次通过局部校对 → 全局场记 → 文风打磨三关，任一关卡熔断即精准反馈重写。

**旧版单通道审核**（`review_chapter()`，保留兼容）检测五类冲突：设定冲突、OOC、大纲冲突、前后矛盾、逻辑漏洞，每个冲突项含类型、严重度（1-10）、引用原文、修复方案列表。

**变更同步**：章节写作完成后自动检测新引入的人物、人物状态变化、大纲偏离和世界观新增内容，逐项确认后同步到设定库。修改世界观/章纲后自动触发影响分析，返回受影响章节列表及严重度排序。

### 工作流编排

采用**四审核并行**流水线，四个 Reviewer 同时跑，合并 REJECT feedback 后 WriterAgent 一次性修正，已通过关卡下轮跳过：

```
write_and_review_chapter(chapter_number, word_target, auto_polish)
│
├── 1. WriterAgent.write_chapter()            → 生成草稿（流式输出）
├── 2. 四审核并行流水线（最多 2 轮）
│      ├── plot_aligner       (25%) — 剧情对齐（大纲+情节一致性）
│      ├── character_guard    (25%) — 人设/世界观守护
│      ├── continuity_tracker (25%) — 时空状态连续性
│      └── style_refiner      (25%) — 文风去AI痕迹
│      ↓ 并行执行，合并所有 REJECT feedback
│      有未通过 → WriterAgent 一次性统筹修正 → 仅未通过关卡重审（已通过跳过）
│      全部通过 → 最终得分 = 各关卡得分 × 0.25 加权平均
├── 3. PolisherAgent.polish_chapter()         → 润色收尾（全部通过后可选）
├── 4. save → SQLite + LanceDB + 版本历史
├── 5. summarize_chapter()                    → 摘要供后续记忆注入
└── 6. 返回同步检测结果（新人物/人物变化/大纲偏离/世界观变更）
```

批量写作：选择章节范围后一键按顺序执行，单章失败不中断后续，完成后汇总报告（成功/失败/用时/字数/评分）。

---

## 数据模型

所有数据存储在 `data/novels.db`（SQLite），使用 SQLAlchemy ORM 管理。新增字段通过 `_migrate_add_columns()` 自动迁移，无需手动操作。

```
Novel (小说项目)
│  id, title, author, logline, genre, world_setting(JSON), writing_style
│  status, llm_model, model_outline/.../model_reader
│  temp_outline/.../temp_canvas, deai_rules
│  style_profile(JSON), style_reference_text, quality_config(JSON)
│  target_platform, target_tags(JSON), invite_code
│  total_input_tokens, total_output_tokens
│
├── NovelOutline (总大纲)
│     premise, theme, main_conflict, story_structure(JSON)
│     protagonist_arc, ending_summary, total_chapters
│
├── NovelDocument (设定文档) [多个]
│     doc_type(background/system/characters/custom), title, content(MD)
│
├── Volume (卷) [多个]
│     volume_number, title, summary, main_conflict, arc_goal
│     start_chapter, end_chapter
│
├── Chapter (章节) [多个]
│   │  chapter_number, title, status(7种状态), approval_status
│   │  outline_core_event, outline_conflict, outline_scene, outline_emotion
│   │  outline_characters(JSON), outline_foreshadowing_set/collect(JSON)
│   │  content, content_draft, word_count, summary, key_events(JSON)
│   │  review_report(JSON), review_score, reader_feedback(JSON), reader_score
│   │
│   ├── ContentVersion (版本历史) [多个]
│   │     version_number, content, version_type(draft/reviewed/polished/user_edit)
│   │
│   └── Comment (章节评论) [多个]
│         author_name, content
│
├── Character (人物档案) [多个]
│     name, aliases(JSON), role, age, gender, appearance
│     personality, background, abilities(JSON), relationships(JSON)
│     growth_arc, current_state, motivations, secrets
│     speech_patterns, behavioral_patterns, is_main
│
├── Foreshadowing (伏笔) [多个]
│     name, description, set_chapter, set_content
│     collect_chapter, collect_content, collect_by_chapter(截止)
│     importance(high/medium/low), status(active/collected/abandoned), notes
│
├── TimelineEvent (时间线) [多个]
│     chapter_number, in_story_time, event_name, characters_involved(JSON)
│
└── Collaborator (协作者) [多个]
      display_name, role(owner/reviewer), last_seen_at, current_page
```

**章节状态流转**：`outline_pending → outlined → writing → review_pending → reviewed → polished → published`

**审批状态**：`pending → approved / needs_revision / rejected`

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 前端框架 | Streamlit |
| 关系型存储 | SQLite + SQLAlchemy ORM |
| 向量存储 | LanceDB（Lance 格式，支持版本快照） |
| Embedding 模型 | Qwen3-Embedding-0.6B（sentence-transformers） |
| LLM SDK | Anthropic SDK（prompt caching）+ OpenAI SDK（兼容接口） |
| 可视化 | streamlit-agraph（力导向图）、Altair（图表） |
| 导出 | python-docx（Word）、EbookLib（EPUB） |
| 部署 | Docker / PyInstaller macOS .app / 源码运行 |
| JSON 容错 | json-repair（处理 LLM 输出格式瑕疵） |

---

## 未来方向

1. **API 服务化**：将 `NovelWorkflow` 封装为 FastAPI 接口，解耦前端，支持移动端接入
2. **增量大纲扩展**：智能分析已有章节密度，100 章扩展至 200 章时自动在情节稀疏处插入新章纲
3. **写作分析面板**：统计用户修改频次最高的问题类型，自动调整 Agent 生成偏好
4. **多语言支持**：支持英文、日文等语言的小说创作
5. **移动端适配**：响应式 UI 优化，支持平板/手机端操作
