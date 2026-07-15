# Neupen -- AI 长篇小说写作系统：技术解析与面试指南

## 一、项目概述

Neupen 是一个 AI 驱动的长篇小说协作写作系统。系统通过 **8 个专业化 Agent 协同** 完成从一句话灵感到完整章节的全流程：大纲生成、角色设计、章节撰写、质量审查、文风润色、读者评价、灵感孵化和全局 AI 助手。核心创新在于一套 **三层记忆架构**，解决了 LLM 在长篇叙事中最棘手的问题——跨章节一致性维护。

**技术栈：** Python / Streamlit / SQLAlchemy / LanceDB / Sentence-Transformers / Anthropic SDK / OpenAI SDK

---

## 二、技术选型与依据

### 2.1 LLM 接入层：双 SDK 统一抽象

| 选型 | 备选方案 | 选择理由 |
|------|----------|----------|
| 自建 `NovelLLM` 类 | LangChain / CrewAI 封装 | 长篇写作对 prompt 结构、token 控制、缓存策略有精细需求；框架封装会限制对底层 API 特性（如 Anthropic prompt caching）的直接控制 |
| 双 SDK（anthropic + openai） | 纯 OpenAI 兼容层 | Anthropic 原生 SDK 支持 `cache_control: ephemeral`，可大幅降低重复系统提示词的计算成本；OpenAI SDK 的 `base_url` 机制天然兼容 DeepSeek、通义千问、Gemini 等 |
| `MODEL_REGISTRY` 静态注册表 | 数据库存储 / 配置文件 | 模型元数据（窗口大小、定价等级、擅长风格）是编译期常量，注册表模式简洁且类型安全 |

**关键设计决策：** 没有使用 LangChain 或 CrewAI 的 Agent 框架，而是让每个 Agent 以纯 Python 类的形式直接调用 `NovelLLM`。原因是：

1. 长篇写作中每个 Agent 的 system prompt 高度定制化（如 WriterAgent 有详细的"反 AI 味"写作规则），框架的抽象层会增加调试成本
2. Agent 间不需要自主通信——所有编排逻辑收归 `NovelWorkflow` 状态机，更易于控制执行顺序和错误恢复
3. 需要精细的 token 预算管理（如 `max_tokens` 随章节数量动态计算），框架难以满足

### 2.2 记忆系统：SQLite + LanceDB 混合存储

| 层级 | 存储引擎 | 选择理由 |
|------|----------|----------|
| 全局记忆（世界观/角色/大纲） | SQLite + SQLAlchemy | 结构化数据，需要精确查询（如"获取所有主角"），关系型数据库最合适 |
| 章节记忆（近 N 章内容） | SQLite | 滑动窗口读取，顺序访问模式适合 B-tree 索引 |
| 片段记忆（全文语义检索） | LanceDB + Qwen3-Embedding | 需要跨章节语义相似度搜索（如"找到之前提到过的类似场景"），向量数据库是唯一选择 |

**为什么不用 Chroma/Pinecone/Weaviate？**
- LanceDB 是嵌入式向量数据库（零服务端依赖），与 SQLite 的哲学一致——单文件部署，适合桌面应用和 Docker 单容器场景
- 原生支持 Lance 格式的时间旅行（version checkout），可回溯到任意历史版本的向量索引
- 与 sentence-transformers 的集成最为原生（`lancedb.embeddings` 注册表直接管理模型生命周期）

### 2.3 前端：Streamlit

| 选型 | 备选方案 | 选择理由 |
|------|----------|----------|
| Streamlit | Gradio / Next.js + FastAPI | 纯 Python 全栈，快速迭代；原生支持 streaming（`st.write_stream`）；session state 机制天然适配多页面 SPA；目标用户是作家而非开发者，不需要复杂前端 |

### 2.4 向量嵌入：Qwen3-Embedding-0.6B

| 选型 | 备选方案 | 选择理由 |
|------|----------|----------|
| Qwen3-Embedding-0.6B（本地） | OpenAI text-embedding / BGE / Jina | 中文小说语料的语义理解需要中文优化模型；0.6B 参数量在 CPU 可接受推理速度内（~500ms/chunk）；离线运行不依赖外部 API，降低延迟和成本 |

---

## 三、系统架构

### 3.1 整体分层

```
┌─────────────────────────────────────────────────────┐
│                    Streamlit UI                      │
│  ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┐ │
│  │项目  │设定  │大纲  │写作  │可视化│导出  │风格  │ │
│  │管理  │管理  │编辑  │工坊  │分析  │中心  │管理  │ │
│  └──┬───┴──┬───┴──┬───┴──┬───┴──┬───┴──┬───┴──┬───┘ │
│     │      │      │      │      │      │      │     │
├─────┼──────┼──────┼──────┼──────┼──────┼──────┼─────┤
│     │      │   NovelWorkflow（状态机 + 管线编排）    │
│     └──────┼──────┼──────┼──────┼──────┼──────┘     │
│            │      │      │      │      │            │
│  ┌─────────┴──────┴──────┴──────┴──────┴─────────┐  │
│  │               8 Specialized Agents             │  │
│  │  Outline · Character · Writer · Reviewer ·     │  │
│  │  Polisher · Reader · Idea · Canvas             │  │
│  └───────────────────┬───────────────────────────┘  │
│                      │                              │
│  ┌───────────────────┴───────────────────────────┐  │
│  │              NovelLLM（统一 LLM 抽象）         │  │
│  │    ┌─────────────┐    ┌──────────────────┐    │  │
│  │    │ Anthropic SDK│    │ OpenAI SDK       │    │  │
│  │    │ (+ caching) │    │ (多 base_url)    │    │  │
│  │    └─────────────┘    └──────────────────┘    │  │
│  └───────────────────────────────────────────────┘  │
│                                                     │
├─────────────────────────────────────────────────────┤
│  ┌──────────────┐ ┌──────────────┐ ┌─────────────┐ │
│  │  SQLite      │ │  LanceDB     │ │ JSON Files  │ │
│  │  (10 tables) │ │  (vectors)   │ │ (API keys,  │ │
│  │  novels.db   │ │  chapter_    │ │  styles)    │ │
│  │              │ │  chunks.lance│ │             │ │
│  └──────────────┘ └──────────────┘ └─────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 3.2 三层记忆架构（核心创新）

```
写作请求
    │
    ▼
MemoryManager.build_writing_context()
    │
    ├── Layer 1: GlobalMemory (SQLite)
    │   全局 & 永久
    │   ├── 世界观设定（magic system, geography...）
    │   ├── 全部角色档案（含当前状态）
    │   ├── 小说大纲（主线/卷/章）
    │   ├── 伏笔库（含收回截止期）
    │   └── 时间线事件
    │
    ├── Layer 2: ChapterMemory (SQLite)
    │   中期 & 滑动窗口
    │   ├── 最近 N 章摘要（默认 N=5）
    │   └── 最近 N 章内容（截断至前 1500 字）
    │
    └── Layer 3: FragmentMemory (LanceDB + Qwen3-Embedding)
        长期 & 语义检索
        ├── 全部章节按 500 字分块
        ├── 向量化存储（余弦相似度检索）
        └── 查询 = 当前章核心事件 + 涉及角色 + 场景
            │
            ▼
      Top-K 最相关片段（默认 K=10, 展示 5）
            │
            ▼
    组装为完整写作上下文 → WriterAgent
```

**记忆写入时序：** 每章完成后 `save_new_chapter()` 同步写入三层：
1. SQLite 更新章节内容与字数
2. 生成章节摘要（WriterAgent.summarize_chapter）存入 Layer 2
3. 异步线程将内容分块+嵌入写入 LanceDB（不阻塞 UI）

### 3.3 写作管线（Pipeline）

```
write_and_review_chapter()
    │
    ▼
Phase 1: 写作
    WriterAgent.write_chapter()
        · 章纲强制执行清单置顶注入（核心事件/冲突/场景/情感/出场人物）
        · 三层记忆上下文 + 风格档案 + 平台风格
        · 流式输出（可选 stream_callback）
        · 字数偏差时最多重试 2 次
    │
    ▼
Phase 2: 润色（可关闭）
    PolisherAgent.polish_chapter()
        · 预处理：比喻密度检测精简（阈值 3.0/千字，避免润色 LLM 重新引入无效比喻）
        · 去AI润色 LLM 调用（消除 AI 痕迹 + 风格迁移）
        · 后处理（_ContentPostProcessMixin）：破折号修复 → 禁止句式 LLM 修正 → 比喻密度精简
    │
    ▼
Phase 3: 四审核并行循环（最多 5 轮，全通过提前退出）
    ┌─────────────────────────────────────────────────────┐
    │  四个 Reviewer 并行执行                              │
    │                                                     │
    │  🎯 plot_aligner      (40%) 阈值 8.0               │
    │     7项扣分：核心事件(-2)、冲突(-1.5)、场景(-1)、  │
    │     情感基调(-1)、出场人物缺席(-1)、伏笔各(-0.5)   │
    │  🛡️ character_guard   (20%) 阈值 8.0               │
    │  🔗 continuity_tracker(20%) 阈值 8.5               │
    │  ✨ style_refiner     (20%) 阈值 8.0               │
    └───────────────┬─────────────────────────────────────┘
                    │
        ┌───────────┴───────────┐
        │ 全部 PASS             │ 有 REJECT
        ▼                       ▼
    加权最终得分           合并所有 REJECT feedback
    (提前退出)             WriterAgent 一次性统筹修正
                           全量重审全部 4 个关卡（含已通过项）
                           ↑────── 最多 5 轮 ────────┘
    │
    ▼
Phase 4: 持久化
    ├── SQLite: 保存最终内容, 更新状态
    ├── LanceDB: 异步守护线程重建向量索引
    └── 版本历史: 保存草稿 + 定稿（上限 10 版）
    │
    ▼
Phase 5: 后处理
    ├── 生成章节摘要 → Layer 2
    └── 一致性分析 → 检测是否需要更新大纲/设定/人物状态
```

### 3.4 冲突检测（四关卡并行）

`ReviewerAgent.parallel_pipeline_review()` 并行运行四个专职 Reviewer，每个输出 `PASS/REJECT` + 分数（0-10）+ feedback：

| Reviewer | 权重 | 阈值 | 检测维度 | 示例 |
|----------|------|------|----------|------|
| `plot_aligner` 剧情对齐 | 40% | 8.0 | 大纲偏离、情节一致性；7项结构化扣分 | 章纲要求决战，正文写的是郊游 |
| `character_guard` 人设世界观 | 20% | 8.0 | 人物 OOC、世界观冲突 | 冷漠型角色突然热情 |
| `continuity_tracker` 时空状态 | 20% | 8.5 | 状态连续性、时空矛盾 | 上章左臂废了这章用左手攀爬 |
| `style_refiner` 文风去AI | 20% | 8.0 | 去 AI 痕迹、写作风格 | 连续碎句、总结式段尾 |

**最终得分** = `plot_aligner×0.4 + character_guard×0.2 + continuity_tracker×0.2 + style_refiner×0.2`

有任意 REJECT → 合并 feedback 交 WriterAgent 一次性统筹修正 → **全量重审所有4个关卡**（含已通过项），循环直到全部通过或达最大轮数（默认5轮）。

> **为什么全量重审而非跳过已通过关卡**：修正 B 时可能连带破坏 A，若 A 被跳过则该破坏无法被发现，导致虚假通过。

旧版串行三关卡（`pipeline_review()`）和单通道审核（`review_chapter()`）保留兼容。

---

## 四、技术亮点与深度学习方向

> 🎯 本节标注了每个亮点的"深入探讨要点"——这些是面试中值得展开的话题，也是工程中最有学习价值的地方。

### 4.1 Anthropic Prompt Caching 策略

WriterAgent 的系统提示词固定且较长（>1000 字），而用户提示词随章节变化。通过 Anthropic SDK 的 `cache_control: {"type": "ephemeral"}` 标记系统提示词：

```python
# 当 system_prompt > 1000 字符时自动启用
system = [{"type": "text", "text": system_prompt,
           "cache_control": {"type": "ephemeral"}}]
```

效果：相同 system prompt 的后续调用命中服务端缓存，**减少约 90% 的 input token 计费**，同时降低首 token 延迟。这在批量写作模式（连续写 10+ 章）中收益显著。

**🔬 深入探讨要点：**

**Q: Anthropic 缓存的 5 分钟 TTL 意味着什么？为什么不是 1 小时或无限期？**
A: 5 分钟 TTL 是服务端 KV cache 的内存管理策略——缓存驻留在 GPU 显存中，成本极高。TTL 过长会挤占其他请求的 KV cache 空间；TTL 过短则命中率下降。5 分钟是 Anthropic 根据典型 API 调用间隔定的经验值，批量写作恰好符合这个节奏（连续章节生成间隔通常 < 2 分钟）。无限期缓存需要解决缓存失效和一致性问题，成本不划算。

**Q: 除了 system prompt，user prompt 中的重复结构也能缓存吗？**
A: 可以。Anthropic 支持对 user/assistant 消息中的任意 block 设置 `cache_control`，最多可标记 4 个位置。实际上 few-shot 样例是很好的缓存候选——样例是固定的，可以放在第一个 `cache_control` 断点，动态的查询放在最后。但要注意：缓存是前缀匹配的，缓存点之前的内容必须完全一致才能命中，一个字符的差异就会 miss。

**Q: 与 OpenAI 的自动缓存相比，Anthropic 的 ephemeral 缓存有什么优劣？**
A: OpenAI 的缓存是**自动触发**的（prompt 前缀 ≥ 1024 tokens 时自动缓存，开发者无需标记），Anthropic 是**显式标记**。显式标记的优势是开发者可以精确控制缓存哪段（比如只缓存 system prompt 而不缓存 few-shot 样例），缺点是需要手动维护。另外 Anthropic 缓存写入有额外费用（约 1.25× 正常 input 价格），但读取打折（约 0.1×），对于高频重复调用整体合算。

**Q: 如何预测缓存收益？**
A: 预期节省 = `命中次数 × cached_tokens × (正常价 - 缓存读取价)`。在本系统中，system prompt 约 2000 tokens，批量写 10 章，首章写入缓存，后 9 章均命中，节省约 `9 × 2000 × 0.9×正常价 ≈ 16200 token 价值`。实际命中率还取决于 TTL 内是否有调用，TTL 超时后需重新写入。

**💡 学习价值：** LLM 成本优化是工业界重点关注的方向。掌握不同提供商的缓存机制，是降低 AI 应用运营成本的关键。

---

### 4.2 异步向量索引重建

LanceDB 的嵌入计算在 CPU 上耗时约 2-5 秒/章。如果同步执行会阻塞 Streamlit UI。解决方案：

```python
# 在守护线程中异步执行，不阻塞主线程
thread = threading.Thread(target=self._sync_vector_index,
                          args=(chapter_number, title, content),
                          daemon=True)
thread.start()
```

`daemon=True` 确保主进程退出时线程自动终止，避免僵尸线程。

**🔬 深入探讨要点：**

**Q: 为什么用 daemon 线程而不是守护进程？两者区别是什么？**
A: daemon **线程**与主线程共享进程内存（不需要 IPC），启动成本低（微秒级），适合轻量 I/O 任务（如写 LanceDB 文件）。daemon **进程**有独立内存空间，用于 CPU 密集型任务绕过 GIL，但启动成本高（毫秒～秒级）、进程间通信复杂。嵌入计算虽然 CPU 密集，但 sentence-transformers 底层调用 PyTorch/NumPy，这些库会在 C 扩展中释放 GIL，因此线程足够。`daemon=True` 是关键——主进程退出时（Streamlit 关闭）守护线程自动被杀，不会阻塞进程退出。

**Q: 用户在向量索引更新一半时关闭应用，会损坏数据吗？**
A: LanceDB 使用 Lance 格式，写操作是 append-only + 原子提交（类似 LSM-Tree）。中途中断只会丢弃未提交的写操作，不会产生半写状态损坏文件。最坏情况是这一章的向量未能写入，下次搜索时缺少该章的语义片段，但不会崩溃。这也是选 LanceDB 而非 Chroma 的原因之一——Lance 的写事务语义更健壮。

**Q: 能否用 Queue 替代全局线程？**
A: 可以，且更健壮。单线程 + `queue.Queue` 方案：主线程将 `(chapter_number, content)` 放入队列，一个长驻 worker 线程消费队列串行执行嵌入。优点是消除并发写冲突、支持背压（队满时阻塞生产者）、可以优雅关闭（放入哨兵值）。当前方案为每章新建一个线程，如果用户快速连续写多章，可能有多个线程并发写 LanceDB，虽然 LanceDB 支持并发写，但仍有潜在的资源竞争。Queue 方案是更规范的做法，当前方案是为了简单快速实现。

**Q: daemon 线程修改 LanceDB 可能导致竞态条件吗？**
A: Streamlit 本身是多线程的（每个用户会话一个线程），LanceDB 支持多写者并发（通过文件锁 + MVCC），所以文件层面是安全的。但 Python 层面如果多个线程同时调用 `lancedb.connect()` 并写同一张表，存在连接对象争用风险。当前实现每次写入都新建连接（`lancedb.connect(path)`），规避了连接对象的共享状态问题，是合理的防御写法。此外，`_get_lancedb()` 和 `_get_embedding_model()` 的单例初始化已采用**双重检查锁（DCL）**：外层无锁快路径、内层加锁后二次检查，确保高并发下首次初始化只执行一次，不会因 Streamlit 多会话并发启动导致多个连接实例被创建。

**💡 学习价值：** 多线程的正确使用是 Python 后端开发的难点。这里涉及线程生命周期、竞态条件检测、异步 I/O 等深层概念。

---

### 4.3 鲁棒的 JSON 解析

LLM 生成的 JSON 经常包含格式错误（trailing commas、缺少引号、markdown 代码块包裹）。系统采用三级容错：

1. **标准 `json.loads()`** — 尝试直接解析
2. **子串提取** — `response.find("{")` 到 `response.rfind("}")` 截取 JSON 片段
3. **`json_repair.repair_json()`** — 自动修复常见 LLM JSON 错误

这使得即使 LLM 返回格式不规范的结果，系统也能继续工作而非崩溃。

**🔬 深入探讨要点：**

**Q: `json_repair` 库的实现原理是什么？与让 LLM 重新生成相比成本如何？**
A: `json_repair` 是基于字符流的贪心解析器，逐字符扫描，遇到非法字符时按最近合法 JSON 结构推断修复（如缺少引号则补引号、多余逗号则删除、未闭合括号则补全）。时间复杂度 O(n)，无需外部调用，延迟 < 1ms。相比之下，让 LLM 重新生成一次 JSON 需要 1-3 秒 + 额外 token 成本，且再次生成仍可能格式错误。`json_repair` 成功率已能覆盖绝大多数 LLM 输出的格式瑕疵，只有语义层面的错误（如字段值乱填）才需要重新调用 LLM。

**Q: 为什么不直接用 `json_repair`，而要先用 `find("{")`/`rfind("}")` 提取子串？**
A: 性能与鲁棒性双重考虑。`json.loads()` 是 C 扩展，纳秒级，先尝试直接解析有概率零成本通过。子串提取是纯 Python 字符串操作，微秒级，处理最常见的"LLM 在 JSON 前后加了说明文字"场景。`json_repair` 是完整解析器，毫秒级，用于处理格式错误。三级策略按成本递增排列，80% 的情况在第一级就通过，15% 在第二级，只有 5% 需要第三级，整体平均延迟远低于直接用 `json_repair`。

**Q: JSON 嵌套很深时 `json_repair` 成功率会下降吗？**
A: 会。`json_repair` 的贪心推断在浅层结构（1-2层嵌套）准确率接近 100%，但深层嵌套时，一个缺失括号可能导致后续整块结构错误重构，语义发生偏移。本系统中 LLM 输出的 JSON 结构相对固定（大纲/角色/审核报告），嵌套通常不超过 3 层，实测失败率 < 1%。对于更复杂的结构，可以在 system prompt 中提供 JSON schema 示例，引导 LLM 规范输出，从源头减少格式错误。

**Q: 能否用 Pydantic 对修复后的 JSON 进行类型检查？**
A: 完全可以，且是更健壮的做法。修复后的 dict 传入 Pydantic model，验证失败时抛出 `ValidationError`，可以捕获后决定是重试还是用默认值。当前系统使用字典访问 + `.get()` 防御，缺少类型约束。引入 Pydantic 的额外成本是维护 schema 类，但在 Agent 输出结构固定的场景下（如 `ReviewReport`），Pydantic 的类型安全收益显著，尤其是重构时能静态发现字段名变更导致的错误。

**💡 学习价值：** LLM 输出的不稳定性是生产系统的重大挑战。这里涉及容错设计、成本权衡、防御编程等核心工程思想。

---

### 4.4 三级模型回退机制

```
Agent 调用时的模型解析:
novel.model_writer         # 1. Agent 级别的独立模型配置
    ↓ (如果为空)
novel.llm_model            # 2. 项目级别的默认模型
    ↓ (如果为空)
os.environ["DEFAULT_MODEL"] # 3. 全局环境变量默认模型
```

允许用户为不同 Agent 配置不同模型——比如用 Claude Opus 写作（质量优先）、用 Haiku 做审查（速度优先），实现成本与质量的精细平衡。

**🔬 深入探讨要点：**

**Q: 三级回退是显式还是隐式？为什么不在 UI 上直接选模型？**
A: 是显式的——每次 Agent 调用时都执行 `model = novel.model_writer or novel.llm_model or os.environ.get("DEFAULT_MODEL")`，三行代码清晰可见。UI 上**确实可以选模型**（设置页有每个 Agent 的独立模型下拉框），三级回退是给"不想细调"的用户提供的合理默认值。这符合"渐进式配置"原则：新用户只设一个全局模型，高级用户可以精细到每个 Agent 独立配置。

**Q: 每次调用都需要遍历三级吗？能否缓存已解析的模型选择？**
A: 可以缓存，但当前故意不缓存。原因是用户可以在写作过程中随时切换模型（比如写到一半觉得当前模型质量不好），如果缓存了解析结果，本次会话内的切换就不会生效。由于三级回退只是 2-3 次 `or` 短路运算，成本可以忽略不计，缓存带来的一致性问题反而更大。如果性能有实际要求，可以用"写时失效"策略：模型配置变更时清除缓存。

**Q: 如果某个 Agent 的模型不支持 streaming，系统怎么降级？**
A: `NovelLLM` 在调用 `generate_stream()` 时，如果底层 API 返回错误（或模型不在支持流式的列表中），会捕获异常并 fallback 到 `generate()` 非流式调用，然后将完整响应作为单个 chunk 传给 `stream_callback`。UI 层面用户会看到内容"一次性全部出现"而非逐字流式，体验降级但功能不中断。`MODEL_REGISTRY` 中每个模型有 `supports_streaming` 标志，可在调用前提前检测。

**Q: 多用户场景下，用户 A 选 GPT、用户 B 选 Claude，如何隔离？**
A: 当前是**按 novel_id 隔离**——模型配置存在 `Novel` 表的 `model_writer` 等列，每个小说项目独立。同一用户的不同项目可以用不同模型，不同用户的项目天然隔离（novel_id 不同）。但 `DEFAULT_MODEL` 是全局环境变量，是进程级共享的。多用户 SaaS 化改造时，需要将全局默认模型下沉到用户级别（`User` 表新增 `default_model` 字段），替换环境变量读取。

**💡 学习价值：** 配置管理和多模型支持是 AI 应用架构的核心。理解回退机制和版本兼容性对设计可扩展系统至关重要。

---

### 4.5 伏笔生命周期管理

创造性写作中的伏笔管理是一个被忽视但关键的问题。系统实现了完整的伏笔生命周期：

```
创建(active) → 设置截止章节(collect_by_chapter) → 收回(collected) / 放弃(abandoned)
```

- 大纲生成时自动提取伏笔条目（`sync_foreshadowings_from_outlines`）
- LLM 批量分配收回截止期（`assign_foreshadowing_deadlines`）
- 到期预警注入到大纲编辑和写作提示词中
- 可视化页面展示伏笔分布时间线

**🔬 深入探讨要点：**

**Q: `sync_foreshadowings_from_outlines()` 如何避免重复创建同一个伏笔？**
A: 以伏笔**名称**为唯一键做 upsert 操作——先查 `SELECT * FROM foreshadowing WHERE novel_id=? AND name=?`，存在则跳过，不存在才 INSERT。幂等性的代价是依赖 LLM 对同一伏笔每次输出完全相同的名称，实际上 LLM 可能给同一伏笔起微妙不同的名字（"魔法封印之书" vs "封印之书"）。更健壮的方案是引入向量相似度去重——插入前对伏笔名称做嵌入，与已有伏笔计算余弦相似度，相似度 > 0.9 则视为重复，但实现复杂度更高。

**Q: 伏笔的"截止章节"是硬约束还是软建议？**
A: 软建议。超过截止章节后，写作提示词会注入"⚠️ 该伏笔已到期未收回，请在本章安排收束或延期"的警告，但不会阻止写作继续。这是有意为之——故事中伏笔的时机往往需要调整，强制硬截止反而会破坏剧情节奏。超期伏笔在可视化页面会高亮标红，提醒作者主动处理。未来可以增加"延期"操作，允许作者显式把截止章节往后推，同时记录延期原因。

**Q: 能否用图数据库（Neo4j）存储伏笔与角色/事件的因果关系？**
A: 理论上图数据库更适合表达"伏笔 A 在第 5 章由角色 B 埋下，与事件 C 相关，在第 30 章由角色 D 收束"这种多跳关系查询。但引入 Neo4j 的代价是：零服务端依赖的部署优势消失（Neo4j 需要独立进程）、学习成本上升、与现有 SQLAlchemy 层不兼容。当前用 SQLite 的 JOIN 查询已能满足需求（伏笔 → 章节是简单外键关系）。如果系统演化为 SaaS 且伏笔关系复杂度显著上升，再迁移到图数据库是合理的演进路径。

**Q: 伏笔重要度（high/medium/low）能否通过 LLM 动态调整？**
A: 可以。当前重要度是大纲生成时由 LLM 一次性评估赋值，后续不再自动更新。动态调整的思路是：在每次章节写作后，重新评估所有 active 伏笔与当前故事进展的关联度，动态上调或下调重要度。难点在于计算成本——100+ 伏笔逐一重新评估的 token 消耗可观。更实际的方案是只重新评估"距截止章节 ≤ 5 章"的伏笔，缩小每轮评估范围。

**💡 学习价值：** 伏笔管理是创意写作领域的独特需求，很少在通用系统中看到。这展示了如何为特定领域设计数据模型和工作流。

---

### 4.6 风格迁移系统

双层风格控制：

1. **个人风格档案（10 维度）：** 从用户上传的参考文本或已完成章节中分析提取
   - overall_style / sentence_patterns / vocabulary / narrative_voice / dialogue_style / description_style / rhythm_pacing / emotion_expression / signature_techniques / polish_instructions
2. **平台风格模板：** 针对起点、晋江、番茄、掌阅等平台，预置 14 种类型标签的写作风格描述

两层风格同时注入 WriterAgent 和 PolisherAgent 的提示词，实现"像作者 X 在平台 Y 上的风格写作"。

**🔬 深入探讨要点：**

**Q: 风格档案的 10 个维度是怎么定的？有参考学术论文吗？**
A: 主要参考了写作学和风格批评领域的实践分类，加上工程可操作性筛选：`overall_style`（整体氛围）、`sentence_patterns`（句式偏好）、`vocabulary`（用词倾向）、`narrative_voice`（叙事视角）、`dialogue_style`（对话风格）、`description_style`（描写风格）、`rhythm_pacing`（节奏把控）、`emotion_expression`（情感表达）、`signature_techniques`（标志性技法）、`polish_instructions`（给润色师的定向指令）。10 个维度是经验值，不是严格学术结论——实践中能覆盖大多数作家风格差异即可。PCA 降维的思路有意义，但需要大量标注数据才能做，当前数据量不足以支撑。

**Q: `analyze_style()` 是 LLM 分析还是统计特征？**
A: 是 LLM 分析——将参考文本（截断至前 5000 字）发给 LLM，要求按 10 个维度输出结构化描述。纯统计特征（句长分布、词汇多样性、标点密度等）对于捕捉"这个作者喜欢用冷硬派的叙事口吻"这类高层风格特征能力有限，LLM 理解语义更直接。但统计特征可以作为补充——比如用词汇多样性指数（TTR）和平均句长作为风格档案的数值基准，辅助验证 LLM 分析是否准确。

**Q: 平台风格模板是手工编写的吗？能否自动提炼？**
A: 是手工编写的，14 种类型标签（玄幻/都市/言情/悬疑等）各有一套平台特定的写作风格描述，大约 200-400 字/模板。自动提炼的思路可行但成本高：需要爬取目标平台的高评分作品（涉及版权问题）、按类型聚类、用 LLM 提炼共性风格特征。更实际的半自动方案是：让资深读者/编辑手动标注少量样本，再用 LLM 扩展和格式化。当前手工版本的优势是质量可控、可快速迭代。

**Q: 个人风格和平台风格冲突时如何权衡？**
A: 当前是并列注入，由 LLM 自行权衡——两层风格都出现在 prompt 中，没有显式优先级。实践中如果作者风格和平台风格有明显冲突，LLM 倾向于"折中"处理，效果可能不理想。更好的设计是增加"风格混合比例"配置（如"个人风格 70% / 平台风格 30%"），在 prompt 中显式说明权重，或者分两次调用：先用个人风格写，再用平台风格做定向润色。

**💡 学习价值：** 风格迁移涉及自然语言特征工程、提示工程和用户偏好建模。这是 AI 创意应用的高难度话题。

---

### 4.7 轻量级数据库迁移

没有引入 Alembic，而是在模型加载时自动检测缺失列并执行 `ALTER TABLE ADD COLUMN`：

```python
def _migrate_add_columns():
    inspector = sa_inspect(engine)
    for table_name, model_class in tables.items():
        existing = {c["name"] for c in inspector.get_columns(table_name)}
        for column in model_class.__table__.columns:
            if column.name not in existing:
                engine.execute(f"ALTER TABLE {table_name} ADD COLUMN ...")
```

适合单用户桌面应用场景——无需迁移脚本管理，新字段自动添加，向后兼容。

**🔬 深入探讨要点：**

**Q: `ALTER TABLE ADD COLUMN` 在 SQLite 中是 O(n) 操作吗？**
A: SQLite 的 `ALTER TABLE ADD COLUMN` 是 O(1) 操作——SQLite 只更新 schema 元数据，不重写数据文件。这与 MySQL/PostgreSQL 不同（后者可能需要全表扫描重建）。但 SQLite 的 `ALTER TABLE` 能力极其有限：只支持 ADD COLUMN，不支持 DROP COLUMN（3.35.0 前）、RENAME COLUMN（3.25.0 前）或修改列类型。如果需要这些操作，必须用"重建表"策略：CREATE 新表 → INSERT INTO SELECT → DROP 旧表 → RENAME 新表。

**Q: 为什么不用 Alembic？**
A: Alembic 是正确的生产选择，但引入成本不低：需要维护 migration 脚本版本链、理解 `upgrade/downgrade` 函数写法、处理自动检测 schema diff 时的边界情况。对于这个单用户桌面应用，migration 脚本的运行环境是用户本机，不存在多环境部署，回滚场景也极少。当前的自检迁移（启动时自动检测并 ALTER）满足 90% 的需求，代价是不能 rollback 和不能做破坏性变更。如果项目演化为多用户 SaaS，引入 Alembic 是必要的，届时可以将现有 schema 作为 base migration 的起点。

**Q: 这套系统支持列删除、类型变更、约束修改吗？**
A: 不支持，这是已知局限。当前只处理 ADD COLUMN，没有实现"表重建"迁移。未来如果需要 DROP COLUMN，计划在 `_migrate_add_columns()` 旁边新增 `_migrate_schema_rebuild()` 函数，以"备份→重建→恢复"的方式处理破坏性变更，并通过一个 `schema_version` 表记录当前版本号，决定是否执行重建。

**Q: 多进程下并发执行 `ALTER TABLE` 会死锁吗？**
A: SQLite 使用数据库级写锁，并发 `ALTER TABLE` 时第二个进程会等待第一个释放锁，不会死锁（SQLite 有 busy_timeout 机制），但会有等待延迟。系统启动时执行迁移检查，如果多个进程同时启动（如 Docker 多副本），第一个成功 ALTER，后续进程检测到列已存在则跳过，幂等安全。真正的风险是多进程并发**写数据**时的 WAL 模式配置——系统已启用 WAL 模式（`PRAGMA journal_mode=WAL`），支持一写多读，并发写仍然串行化。

**💡 学习价值：** 数据库演化和向后兼容性是生产系统的隐性成本。这涉及到 zero-downtime deployment、migration 策略等DevOps 议题。

---

### 4.8 章节版本控制

实现了一个带上限淘汰的版本历史系统：

- 每章最多保存 `MAX_VERSIONS=10` 个版本
- 超出时删除最旧版本
- 版本号使用 `MAX(version_number) + 1` 而非 `COUNT + 1`，避免删除后的编号冲突
- 支持 4 种版本类型：`draft / reviewed / polished / user_edit`

**🔬 深入探讨要点：**

**Q: 为什么用 `MAX(version_number) + 1` 而不是 UUID？**
A: 用自增整数是因为版本号需要**可排序且语义清晰**——用户看到"版本 1、2、3"比看到 UUID 直观得多，而且排序查询用整数比字符串高效（B-tree 索引友好）。UUID 的优势是全局唯一，在分布式场景下无需协调，但本系统是单用户本地 SQLite，不存在分布式场景。用 `MAX + 1` 而非 `COUNT + 1` 的原因是：删除旧版本后 COUNT 会缩小，导致版本号重用（例如已有版本 1、2、3，删除版本 1 后 COUNT=2，下一版本会变成版本 3，与已有版本 3 冲突）。`MAX + 1` 则是单调递增，不受删除影响。

**Q: 版本淘汰是 FIFO 吗？是否应该用 LRU？**
A: 是 FIFO——删除 `version_number` 最小的版本。LRU 在协作场景下有意义（保留被多人频繁查看的版本），但当前系统是单用户的，访问频率信息没有持久化（session state 里的当前版本重启后丢失），实现 LRU 需要额外的访问记录表。更值得考虑的是**按类型保留策略**：无论 FIFO 淘汰多少版本，至少保留最新一个 `user_edit` 版本（人工修改最宝贵）和最高分 `reviewed` 版本，其余再按时间淘汰。

**Q: 能否用 Git 式 DAG 来管理版本的分支和合并？**
A: 技术上可以，但对于小说写作场景收益有限。Git DAG 解决的是"多人同时修改同一文件"的合并问题，但当前写作场景是单作者线性修改（AI 生成 → 人工调整 → 再生成），不存在分叉合并需求。如果未来支持"从版本 3 分叉出版本 3a（实验性改写）和版本 3b（保守改写），最终择优保留"的 A/B 写作场景，DAG 才会有价值。届时可以在 `ContentVersion` 表中增加 `parent_version_id` 列来构建 DAG 结构。

**Q: 版本之间能否实现增量存储？**
A: 当前是全量存储——每个版本保存章节完整内容（一章约 3000-5000 字）。增量存储（diff-based）可以用 `difflib.unified_diff()` 计算相邻版本差异，存储 patch 而非全文，10 个版本的存储从 ~50KB 降至 ~5-10KB。但读取时需要从 base 版本开始逐个 apply patch，读取复杂度上升。对于当前数据量（几十KB/章），全量存储是合理的工程决策——存储成本可忽略，读取速度快，出错率低。增量存储适合版本数量更多（> 100）或内容更大（> 1MB）的场景。

**💡 学习价值：** 版本管理是所有协作系统的核心问题。从数据库到 Git 再到向量数据库，版本控制的设计模式是一个通用难题。

---

### 4.9 系统级去 AI 味引擎

去 AI 味在三个层级叠加注入：

1. **WriterAgent 写作阶段**：系统 prompt 顶部注入项目级 `deai_rules`（用户自定义）+ 全局 `DEFAULT_DEAI_RULES`（比喻密度上限 3.0/千字、禁止开头模式、禁止总结式段尾等）

2. **后处理三步流水线**（WriterAgent / PolisherAgent 共享 `_ContentPostProcessMixin`，调用 `_fix_forbidden_syntax` → `_fix_redundant_metaphors`）：

   **PolisherAgent 额外预处理**：在润色 LLM 调用**之前**先做一轮比喻精简（`_fix_redundant_metaphors`），避免润色时重新引入无效比喻。

   - **Step 1 — 破折号修复**（`_fix_forbidden_syntax` 内，无 LLM 成本）：正则保护合法破折号（话语打断、音效延长），其余滥用场景（解释说明、递进、镜头切换）替换为句号；循环最多 20 轮防死循环。
   - **Step 2 — 禁止句式循环 LLM 修正**（`_fix_forbidden_syntax` 内）：正则扫描"不是……而是……""与其说……不如说……"等句式，有命中则进入循环——每轮发起 LLM 修正，修正后重新检测，直到**全部清零**或达最大轮次（3 轮）；无命中直接转入 Step 3（零 LLM 成本）。入口置有 `_strip_reasoning()` 安全带，剥离任何残留的 `<!--reasoning...-->` 思维链注释，防止推理内容混入正文。
   - **Step 3 — 比喻密度多轮精简**（`_fix_redundant_metaphors`，在 Step 2 末尾调用）：统计全章比喻词密度，超过 3.0/千字则循环 LLM 审查精简，最多 5 轮；密度达标或无命中则零 LLM 成本直接返回。

3. **章纲强制执行清单（WriterAgent prompt 置顶）**：每章写作前将本章章纲的核心事件、冲突、场景、情感、出场人物列为"🔒 强制执行清单"置于 user prompt 顶部，写手必须逐项落实

**🔬 深入探讨要点：**

**Q: `_ContentPostProcessMixin` 如何实现 WriterAgent 和 PolisherAgent 的逻辑统一？**
A: 两个 Agent 的后处理逻辑（破折号修复 / 禁止句式修正 / 比喻精简）完全一致，唯一差异是日志前缀（`[WriterAgent]` vs `[PolisherAgent]`）和是否带 `chapter_number` 参数。提取为 Mixin 后，子类只需声明 `_agent_tag = "WriterAgent"` 和继承 `_ContentPostProcessMixin`，两份重复代码合并为一份（净减少约 150 行）。参数 `chapter_number` 改为可选（默认 0），PolisherAgent 调用时可省略。这是一个典型的模板方法模式变体——行为骨架在父类，差异通过类属性注入，而非覆盖方法。

**Q: 比喻密度阈值 3.0/千字 是怎么定的？**
A: 经验值，通过阅读参考作品人工校准。优秀的中文通俗小说（如天蚕土豆、月关的作品）每千字比喻密度大约在 1-3 个，AI 生成的内容通常在 5-10 个（AI 偏爱用比喻来"丰富"表达）。3.0 是一个略松的上限，不要求完全压平，只消除明显的"堆比喻"现象。如果要做 A/B 测试量化，可以让读者对不同密度版本打分，找到"读者最不觉得AI感"的密度区间作为最优阈值。

**Q: 章纲清单置顶 vs context 重排序，为什么选前者？**
A: LLM 的注意力分布并非均匀——研究表明 LLM 对 prompt 开头和结尾的内容注意力权重更高（"Lost in the Middle"现象）。章纲置顶确保核心约束在第一屏就被"看到"，降低被中间冗长上下文淹没的概率。Context 重排序（把章纲挪到 prompt 末尾）利用的是结尾效应，但末尾内容容易被模型视为"补充说明"而非"主要约束"。置顶的另一个优势是代码侵入性最小——只需在 user prompt 头部插入一段文字，不需要重构上下文组装逻辑。

**Q: 流式路径如何保证后处理流水线被执行？**
A: 流式路径（`stream_callback` 不为 None）先调用 `generate_stream()` 实时推流给 UI，generator 耗尽后完整内容收集到 `content` 变量，然后**同步**执行 `_fix_forbidden_syntax(content)` → `_fix_redundant_metaphors(content)`（通过 Mixin 统一调用路径）。这意味着用户在流式输出结束后会看到一个短暂的"处理中"状态（比喻精简可能额外耗时 1-2 秒），然后最终内容才真正写入数据库。早期版本的流式路径直接返回流式内容跳过了后处理，导致流式和非流式路径输出质量不一致，这个 bug 已修复（commit `14ccbe8`），Mixin 重构后两路径共享同一代码路径，不可能再出现分叉。

**Q: 使用 DeepSeek-R1 这类思维链模型时，推理过程如何处理？**
A: `NovelLLM` 维护 `last_reasoning: str` 属性，每次 `_generate_openai()` 调用后自动从响应的 `reasoning_content` 字段提取并写入（Anthropic 后端不支持则清空）。UI 层在 agent 调用完成后读取 `agent.llm.last_reasoning`，非空时在结果旁渲染「💭 思考过程」折叠 expander，默认收起不占位。对不支持思维链的模型完全透明——`last_reasoning` 始终为空字符串，expander 不显示。
覆盖的操作：润色、手动审核（写作页）、AI 解析文档 / 批量生成章纲 / 重新生成大纲（大纲页）。流式路径（`generate_stream()`）因逐 token 输出天然无法携带 reasoning，此时 `last_reasoning` 为空，不展示。
非流式统一通过 `generate()` → `_generate_openai()` 路径获取 reasoning，无需单独维护 `generate_with_reasoning()` 的复杂分支，任何调用 `generate()` 的地方调用后读 `llm.last_reasoning` 即可。

**💡 学习价值：** 去 AI 味是 AI 写作应用的核心差异化。这里涉及 Mixin 模式消除重复代码、prompt 分层设计、确定性后处理与 LLM 后处理的成本权衡、以及流式 vs 非流式的路径统一。

---

### 4.10 API Key 三级优先级

```
优先级：应用内保存 > 系统环境变量 > .env 文件

实现机制：
1. python-dotenv 的 load_dotenv() 默认不覆盖已有环境变量
2. apply_saved_keys() 在模块加载时将 api_keys.json 的 key 写入 os.environ（覆盖）
3. 因此：api_keys.json > 系统 env > .env
```

**🔬 深入探讨要点：**

**Q: 为什么允许明文保存在 api_keys.json？应该用 KMS 吗？**
A: 这是面向个人用户的桌面应用，`api_keys.json` 存在本机 `data/` 目录下，与系统钥匙串中存储的 WiFi 密码安全级别相当——只要本机不被入侵，风险可接受。生产级 SaaS 中确实应该用 KMS（AWS KMS / HashiCorp Vault）加密存储，但引入 KMS 需要网络连接和账号管理，与"离线可用的桌面应用"定位冲突。合理的折中方案是用 macOS 系统钥匙串（`keyring` 库）存储——既本地化又有系统级加密保护，是桌面应用的最佳实践，计划作为后续改进项。

**Q: `load_dotenv()` 的默认行为是什么？为什么设置 `override=False`？**
A: `load_dotenv()` 默认行为是将 `.env` 文件中的变量注入 `os.environ`，但**不覆盖**已有的环境变量（即 `override=False` 是默认值）。这意味着如果系统环境变量中已有 `ANTHROPIC_API_KEY`，`.env` 中的同名变量会被忽略——符合"系统级配置优先于文件配置"的常识。然后 `apply_saved_keys()` 再用 `os.environ[key] = value`（强制覆盖）将 `api_keys.json` 的 key 写入，确保应用内保存的 key 最终优先级最高。这个设计符合最小惊讶原则：用户在 UI 里改了 key，立刻生效，不被环境变量覆盖。

**Q: 能否用环境变量 substitution 实现优先级？**
A: 类似 `${ANTHROPIC_API_KEY:-fallback}` 的 shell substitution 语法只在 shell 解析变量时有效，Python 的 `os.environ` 不原生支持这种语法（需要 `string.Template` 或 `envsubst` 工具手动处理）。在 Python 应用中实现多级优先级，最清晰的方式还是代码逻辑显式控制（如当前的"json > sys_env > .env"三级）。过度依赖环境变量 substitution 会让优先级逻辑分散在多个配置文件中，增加调试难度。

**Q: 多用户场景下 API key 隔离策略是什么？**
A: 当前是**全局单一 key**——所有用户共享同一套 API key，调用计费统一记录在 key 持有者账号下。在 SaaS 场景下这完全不可接受——需要每个用户绑定自己的 API key（BYOK，Bring Your Own Key）或平台代收费（对用户隐藏 key，平台统一调用并收取溢价）。BYOK 方案需要将 `api_keys.json` 的内容迁移到数据库的 `User` 表中，并按 `user_id` 隔离；调用时根据当前登录用户动态选择 key，而非全局 `os.environ`。

**💡 学习价值：** 凭证管理是安全工程的核心。这涉及到密钥轮换、访问控制、审计日志等生产环境的必备知识。

---

### 4.11 协作系统

- 基于邀请码的角色加入（排除易混淆字符 O/0/I/1/l）
- 心跳机制追踪在线状态（10 分钟超时）
- 章节级评论和四状态审批工作流
- UI 层权限控制（审阅者看到 disabled 按钮而非隐藏功能）

**🔬 深入探讨要点：**

**Q: 邀请码长度为 8 的随机字符串，碰撞概率是多少？**
A: 邀请码字符集排除了易混淆字符 `O/0/I/1/l`，使用 `string.ascii_uppercase + string.digits` 去掉这 5 个字符后约剩 57 个字符。8 位长度的空间是 57^8 ≈ 11.6 万亿种组合，在用户量极少（个人/小团队）的场景下碰撞概率可忽略不计（< 0.000001%）。如果用户量扩展到数万，应改用更长码（12位）或改为 UUID 截断的 Base62 编码（如 `nanoid`），在保持可读性的同时降低碰撞概率。当前设计对目标用户群是足够的。

**Q: 心跳超时 10 分钟，这个数字是如何确定的？**
A: 10 分钟是根据写作场景的"用户行为间隔"估算的——用户可能长时间阅读、思考，5 分钟内没有任何操作是正常的，30 分钟不活跃则大概率已离开。10 分钟是这两个边界的折中。心跳写入是每次页面 rerun 时触发（Streamlit 的 `st.session_state` 刷新机制），频率约 30 秒-2 分钟一次（取决于用户操作），10 分钟超时意味着用户需要连续 5+ 次心跳都丢失才会被标记离线，误报率很低。自适应策略（如"用户处于写作模式时延长超时到 30 分钟"）是合理的改进方向。

**Q: 四状态审批工作流是否涵盖所有真实场景？**
A: `pending → approved / needs_revision / rejected` 涵盖了最核心的场景，但"有建议但不阻塞发布"这种状态（类似 GitHub PR 的"Comment"而非"Request Changes"）确实缺失。更完整的工作流应该是五状态：`pending / approved / approved_with_comments / needs_revision / rejected`，其中 `approved_with_comments` 表示"内容可发布，但有改进建议供参考"。当前的权宜之计是把建议写在 Comment 里并标记 `approved`，语义上略有混淆。这个扩展成本很低（数据库加一个枚举值 + UI 加一个按钮），计划在协作功能迭代时加入。

**Q: UI 层权限控制（disabled 按钮）vs API 层权限检查，哪种更安全？**
A: UI 层是**展示层防护**，不是安全防线——任何了解 Streamlit session state 机制的用户都可以通过直接修改 `st.session_state` 绕过 UI 限制（Streamlit 没有服务端 session，所有状态在客户端）。真正的安全防护必须在数据访问层——每次数据库写操作前检查当前用户的 `role`（owner vs reviewer），reviewer 调用写操作时返回错误。当前系统没有这层保护，完全依赖 UI 层的 disabled 按钮，在受信任的小团队场景下是可接受的，但 SaaS 化时必须补充服务端权限验证。

**💡 学习价值：** 实时协作系统的一致性和权限控制是复杂系统设计的典范。这涉及到分布式系统、并发控制和安全工程的多个领域。

---

## 六、深度学习与研究方向

| 核心话题 | 涉及技术 | 难度 | 推荐深入学习 |
|---------|----------|------|-----------|
| **LLM 成本优化** | Prompt Caching / Token 计费模型 / Batch API | ⭐⭐⭐ | 对标: OpenAI Batch API vs Anthropic Caching 的权衡 |
| **多线程与异步** | Daemon Thread / Race Condition / Lock-free Data Structures | ⭐⭐⭐⭐ | 深入: Python GIL 如何影响异步设计，何时该用 multiprocessing？ |
| **LLM 容错设计** | JSON Repair / Fallback Strategies / Error Recovery | ⭐⭐⭐ | 深入: 如何用形式化方法验证容错策略的完备性？ |
| **向量数据库** | Embedding / Vector Search / Time Travel / Incremental Update | ⭐⭐⭐⭐ | 深入: Lance vs Qdrant vs Pinecone，如何选型？向量量化如何降低存储成本？ |
| **配置管理** | Hierarchical Config / Environment Substitution / Feature Flags | ⭐⭐ | 深入: 12-Factor App 原则，如何管理跨环境配置？ |
| **数据库演化** | Schema Migration / Backward Compatibility / Zero-Downtime Deploy | ⭐⭐⭐⭐ | 深入: Alembic 的原理，数据库版本控制的最佳实践 |
| **版本控制** | Semantic Versioning / DAG-based History / Diff Compression | ⭐⭐⭐ | 深入: Git 的 plumbing/porcelain，LSM Tree 在版本管理中的应用 |
| **NLP 特征工程** | Style Transfer / Few-shot Learning / Prompt Optimization | ⭐⭐⭐⭐ | 深入: 风格特征的量化，如何用 CLIP/BLIP 等多模态模型增强风格识别？ |
| **写作质量评估** | 四关卡并行审核 / Weighted Scoring / Plot Alignment | ⭐⭐⭐⭐⭐ | 深入: plot_aligner 7项扣分维度如何避免LLM主观判断？如何避免评分器偏差？ |
| **去AI味工程** | 比喻密度检测 / Prompt 置顶约束 / 三步后处理流水线 | ⭐⭐⭐⭐ | 深入: 如何量化"AI 味"？确定性规则 vs LLM 后处理的成本权衡 |
| **协作系统** | Real-time Sync / CRDT / Conflict Resolution / Presence | ⭐⭐⭐⭐⭐ | 深入: 学习 Figma/Notion 的协作架构，CRDT 在创意应用中的应用 |

---

## 七、面试常见问题与参考回答

### Q1: 为什么不用 LangChain / CrewAI 这类成熟框架来编排 Agent？

**回答：** 我在初期评估过 LangChain 和 CrewAI（requirements.txt 中还保留了依赖），但最终选择了自建轻量 Agent 类。核心原因有三：

1. **Prompt 精细控制需求**：长篇写作场景中，WriterAgent 的系统提示包含大量反 AI 味的写作规则（如"禁止全知视角"、"对话要有口头禅和结巴"）。这些高度定制的 prompt 结构在框架中难以灵活调整。

2. **编排逻辑明确**：6 个 Agent 之间不需要自主决策谁先执行——写作管线是固定的（写→审→修→润），所有编排集中在 `NovelWorkflow` 状态机中。框架的"自主编排"能力在此场景下是多余的复杂度。

3. **底层 API 特性利用**：比如 Anthropic 的 prompt caching 需要在 system message 上设置 `cache_control` 字段，这在框架封装中很难暴露出来。

**取舍：** 框架的优势在于快速原型和标准化，但当你需要对每一层都有精确控制时，轻量自建的总成本反而更低。

---

### Q2: 三层记忆系统是怎么设计出来的？解决了什么核心问题？

**回答：** 长篇小说写作（50-100 万字，100+ 章节）面临一个根本问题：**LLM 的上下文窗口无法装下整本小说**。即使是 200K 上下文窗口的模型，也装不下完整的 100 章内容。

我的设计思路是模拟人类作家的记忆方式：

- **Layer 1（全局记忆）**：相当于作家"始终记得"的东西——世界观规则、角色设定、故事大纲。这些是永久的、结构化的，用 SQLite 存储。

- **Layer 2（章节记忆）**：相当于"最近几章在写什么"——滑动窗口保留最近 5 章的摘要和截断内容。这解决了短期连续性问题（角色在上一章受伤了，这一章不能突然没事）。

- **Layer 3（片段记忆）**：相当于"依稀记得第 12 章有过类似的场景"——通过向量相似度检索全书中与当前写作最相关的段落。这解决了长程引用问题（50 章前种下的伏笔，现在需要呼应）。

三层的查询结果拼接成一个完整上下文给 WriterAgent，大约控制在 8000-15000 tokens，既覆盖了必要信息，又不超出 token 预算。

---

### Q3: 冲突检测是怎么实现的？准确率如何？

**回答：** 冲突检测是**四关卡并行架构**，而非单一的规则引擎。`ReviewerAgent.parallel_pipeline_review()` 同时启动四个专职 Reviewer，每个只专注一个维度：剧情对齐（`plot_aligner`，权重 40%）、人设世界观（`character_guard`，20%）、时空状态（`continuity_tracker`，20%）、文风去AI（`style_refiner`，20%）。

**为什么不用规则引擎？** 小说中的"冲突"大多是语义层面的——"角色行为是否符合人设"这种判断无法用正则表达式或规则树实现。只有 LLM 能理解"一个内向的角色突然开始长篇大论"是 OOC（Out of Character）。

**准确率保障：**
1. 提供充足上下文——每个 Reviewer 获得全部角色档案、近 5 章摘要、章节大纲和完整待审内容
2. 结构化扣分维度——`plot_aligner` 有 7 个明确扣分项（各含具体分值），减少 LLM 的主观判断空间
3. 出场人物过滤——仅将本章出场人物传完整档案，非出场人物给单行简介，降低 LLM 注意力分散
4. 全量重审——修正后重审所有四个关卡（含已通过项），避免修正引入新问题被遗漏

---

### Q4: 你提到的 Prompt Caching 具体是怎么工作的？节省了多少？

**回答：** Anthropic API 支持服务端提示词缓存。当你在 system message 上标记 `cache_control: {"type": "ephemeral"}` 时，Anthropic 会缓存这个提示词的 KV cache 约 5 分钟。

在我的系统中，WriterAgent 的系统提示词约 2000+ 字符，但它在写每一章时都是相同的。每次调用只有 user prompt（包含具体章节信息）不同。启用缓存后：

- **首次调用**：正常计费，system prompt 被缓存
- **后续调用（5 分钟内）**：system prompt 部分不重新计算，input token 只计费 user prompt 部分
- **实际节省**：batch 模式下连续写 10 章，约节省 **85-90% 的 system prompt input token 费用**

我设计了一个阈值判断——只有 system prompt 长度 > 1000 字符时才启用缓存，避免对短提示词产生不必要的缓存开销。

---

### Q5: 为什么选 LanceDB 而不是 Chroma 或 Pinecone？

**回答：** 选型的核心约束是**零服务端依赖**。Neupen 设计为既支持 Docker 单容器部署，也支持打包成 macOS 桌面应用（通过 PyInstaller）。

- **Pinecone/Weaviate**：托管服务，需要网络连接和订阅费用，不适合桌面应用
- **Chroma**：嵌入式，但当时其持久化机制不够稳定，且不支持原生时间旅行
- **LanceDB**：嵌入式（和 SQLite 一样零依赖部署）、支持 Lance 格式原生的版本管理（`list_versions / checkout_version / restore_version`），可以回溯到任意时间点的向量索引状态

LanceDB 还有一个独特优势：**单表多小说**。所有小说的 chunk 存在同一张 `chapter_chunks` 表中，通过 `novel_id` 过滤。这使得跨小说语义搜索（`search_cross_novel`）成为可能——未来可以用来检测跨项目的设定复用。

---

### Q6: 写作管线中的审核循环会不会导致无限循环或质量退化？

**回答：** 这是一个很好的问题。现在的架构是**四关卡并行全量重审**，有三层保护机制：

1. **硬上限**：并行审核最多循环 5 轮（`MAX_REVIEW_ITERATIONS`），超出后使用当前最优版本继续

2. **最优版本追踪**：每轮迭代追踪 `best_content` 和 `best_score`，即使最后一轮分数下降，也会回退到历史最高分版本

3. **一次性统筹修正**：四个关卡的 REJECT feedback 合并后交给 WriterAgent **一次**修正，避免了逐条修复导致"修好 A 又破坏 B"的问题。修正后全量重审所有四个关卡（含之前通过的），确保修正没有引入新问题

相比旧版串行"修复一个→审查一个"的双循环，并行全量重审的优点是：feedback 更完整（一次看全4个维度的问题），修正更高效（一次覆盖所有冲突点），且不会出现"A 已通过但被 B 的修正连带破坏"的虚假通过。

---

### Q7: 你是如何处理 LLM 输出不稳定的问题的？

**回答：** LLM 的输出不确定性是工程中最常见的挑战。我在多个层面做了应对：

**JSON 输出：** 三级容错（标准解析 → 子串提取 → json_repair 自动修复）。实测中 LLM 约 15-20% 的 JSON 响应有格式问题，三级容错将失败率降到了 < 1%。

**写作质量：** 双循环审查机制本身就是对不稳定性的对冲——如果第一次写得不好，系统会自动修复或重写。

**角色一致性分析：** `analyze_chapter_consistency` 的结果中，角色更新会与数据库中已有角色名做交叉验证，过滤掉 LLM 虚构的不存在的角色名。

**优雅降级：** 所有非核心后处理（摘要生成、一致性分析、向量索引更新）都包裹在 `try/except` 中，失败不会阻断主流程。用户能拿到章节内容，后台任务可以下次补偿。

---

### Q8: 系统是怎么实现流式输出的？

**回答：** 流式输出的实现涉及三层：

1. **LLM 层**：`NovelLLM.generate_stream()` 返回 Python Generator，对 Anthropic 使用 `client.messages.stream()` 上下文管理器并 `yield delta.text`，对 OpenAI-compatible 使用 `stream=True` 参数并 `yield chunk.choices[0].delta.content`。

2. **Agent 层**：`WriterAgent.write_chapter()` 接受 `stream_callback` 参数。当提供回调时，调用 `generate_stream()` 而非 `generate()`，每收到一个 chunk 就调用 `stream_callback(chunk)`。

3. **UI 层**：Streamlit 的 `st.write_stream()` 或手动累积。每个 chunk 实时渲染，用户在写作过程中就能看到正在生成的内容。

**关键设计决策**：流式输出是可选的——同一个 Agent 方法同时支持同步和流式调用，通过有没有 `stream_callback` 来切换。这使得批量模式可以跳过流式输出以减少开销。

---

### Q9: 如果要将这个系统从桌面应用改造为多用户 SaaS 系统，你认为架构上需要做哪些改动？

**回答：** 这是一个很好的架构演进问题。主要改动点：

1. **数据库**：SQLite → PostgreSQL。当前的 SQLAlchemy ORM 层使得迁移成本较低，只需更换 connection string。但 LanceDB 作为嵌入式向量库需要替换为托管方案（如 LanceDB Cloud 或切换到 Qdrant/Weaviate）。

2. **认证系统**：当前的协作系统基于 session state + 邀请码，没有用户账号体系。需要引入 OAuth 或 JWT 认证。

3. **任务队列**：当前的写作管线是同步执行的（阻塞 Streamlit 线程）。多用户场景下需要将长耗时的写作/审查任务推入消息队列（如 Celery + Redis），前端通过 WebSocket 接收进度。

4. **计算隔离**：嵌入模型（Qwen3-Embedding）当前在应用进程中加载，多用户共享会有内存和 CPU 竞争。需要将嵌入计算拆分为独立微服务，或改用 API 形式的嵌入服务。

5. **Streamlit 局限**：Streamlit 的 session state 是进程内的，不支持水平扩展。需要将前端迁移到 React/Next.js + WebSocket 架构。

---

### Q10: 你在这个项目中遇到的最大技术挑战是什么？

**回答：** 最大的挑战是**三层记忆的上下文组装策略**。

难点在于：给 WriterAgent 的上下文既要"足够丰富"（不遗漏关键信息导致不一致），又要"足够精简"（不超出 token 预算导致截断或成本爆炸）。

最初的方案是简单拼接——把所有角色档案、最近 10 章全文、向量检索前 20 条全部塞进上下文。结果是 token 超限、模型注意力分散、生成质量反而下降。

最终的解决方案是**分层压缩 + 定向检索**：
- 全局记忆只提供结构化摘要（角色档案的 `to_profile_text()` 压缩到关键字段）
- 章节记忆截断到 1500 字 + 摘要
- 向量检索用当前章的核心事件和角色名作为查询词（而非章节大纲全文），确保检索到的是真正相关的片段

这个方案将每次写作调用的上下文控制在 8000-15000 tokens，既保证了一致性，又维持在合理的成本范围内。

---

### Q11: 这个项目有哪些你认为做得不够好、未来想改进的地方？

**回答：**（展示自我反思能力）

1. **缺少自动化测试**：当前没有单元测试和集成测试。Agent 的输出是非确定性的，但至少可以对 JSON 解析、版本管理、记忆组装等确定性逻辑进行测试。

2. **同步写作管线**：批量写作时阻塞 UI 线程，用户体验不佳。应该引入后台任务队列，前端通过轮询或 SSE 获取进度。

3. **嵌入模型冷启动**：Qwen3-Embedding 首次加载约 1.2GB，初始化时间较长。可以考虑提供一个轻量备选（如 512 维模型）或按需懒加载。（注：单例初始化的线程安全问题已通过双重检查锁修复，不再是待改项。）

4. **向量索引没有增量更新**：当前每次保存章节都是删除旧 chunk 再全量重建。可以实现差异更新来减少嵌入计算。

5. **迁移系统过于简陋**：只支持 ADD COLUMN，不支持列改名、类型变更、删列。随着 schema 演化变复杂，最终需要引入 Alembic。

---

### Q12: 你是怎么做 Agent 可观测性的？

**回答：** 系统集成了 Arize Phoenix + OpenTelemetry，在 `app.py` 启动时通过 `@st.cache_resource` 单次初始化，自动拉起本地 Phoenix 服务（`localhost:6006`）。

**为什么不在项目 venv 中直接 `pip install arize-phoenix`？** 可以，而且就是这么做的。Phoenix 直接安装在项目 venv 中，`core/tracing.py` 启动时优先使用 `sys.executable` 同目录下的 `.venv/bin/phoenix` 可执行文件，通过 subprocess 在后台拉起 server，不依赖任何外部安装。

**Span 粒度设计：** 两层并用——workflow 级别手动 span（`workflow.write_chapter` / `agent.writer` / `agent.polisher` / `reviewer.gate.{name}`）记录业务语义；OTel instrumentor 自动捕获每个 LLM 调用的 token 数、model、latency。这样既能在 Phoenix UI 看到一次章节生成的完整调用链，也能下钻到具体某次 Anthropic API 调用的耗时和消耗。

**降级机制：** Phoenix 未安装或启动超时（30s）时，`start_span()` 返回 `_NoopSpan`，所有 span 操作是空操作，写作流程完全不受影响。可通过 `NEUPEN_TRACING=0` 强制禁用。

---

### Q13: 你遇到过 Agent 生成结果正确但最终呈现给用户时出错的 bug 吗？

**回答：** 有一个典型案例——**风格档案（style_profile）数字未转换为语义文字**。

**根因：** `Novel.style_profile` 存储的是 int（1-5 的滑条值），写作时需要把它翻译成人类可读的语义描述（如 `4 → "长句偏多，从容不迫"`）。这个转换逻辑封装在 `PolisherAgent._format_style_profile()` 中。

问题在于 `WriterAgent.write_chapter_agentic_gen()`（Agentic 写作路径）有一段独立的风格档案注入代码，直接拼接了 `style_profile[k]` 的原始 int 值，没有调用 `_format_style_profile()`。而 `WriterAgent.write_chapter()`（普通路径）走了正确的转换路径。两条路径的行为不一致，导致 Agentic 写作时 prompt 里出现 `句式节奏: 4` 这样对 LLM 毫无意义的数字。

**修复：** 在 `write_chapter_agentic_gen` 中删除独立的风格注入逻辑，改为调用 `PolisherAgent._format_style_profile()` 复用同一套转换，消除了两条路径的行为差异。这个 bug 的教训是：**同一份数据有多条处理路径时，转换逻辑必须集中封装，不能各自内联**。

---

### Q14: IdeaAgent 的大纲生成是怎么设计的？为什么用三层并行？

**回答：** 早期的 IdeaAgent 是线性的：对话 → 一次 `extract_project_config()` 调用提取结构化配置 → 外部手动触发 `OutlineAgent.generate_full_outline()`。这个方案有两个核心问题：

1. **信息丢失**：从多轮对话中压缩成一份结构化 JSON 时，对话里很多具体的细节（角色背景故事、特定的世界观规则、用户偏好的情节走向）会丢失，因为 `extract_project_config()` 只提取固定字段。
2. **串行等待**：总大纲→人物档案→章纲是依次生成的，用户等待时间是三段等待的总和。

**现在的三层并行架构（`generate_outline_data()`）：**

```
第1层（并行）  ┌─ _gen_meta()          → 提取 title/author/genre 等元数据
              └─ _gen_total_outline() → 生成总大纲 + 世界观（核心，失败即终止）

第2层（并行）  ┌─ _gen_volumes()       → 根据总大纲生成卷纲
              └─ _gen_characters()    → 根据总大纲生成人物档案

第3层（串行）  _gen_chapter_batch()   → 分批30章串行（每批携带上批结尾，保持连续性）
```

**关键设计决策：**

- **对话历史直接传入每个 Sub-agent**：不做信息压缩，每个 Sub-agent 都拿到完整的灵感对话历史。这解决了信息丢失问题——人物生成器能从对话中读到用户随口说的"主角有一个从小失散的妹妹"这类细节。
- **`_gen_total_outline` 失败终止，其余有兜底**：总大纲是后续所有生成的基础，失败则整个流程无意义；卷纲/人物/章纲的 Sub-agent 失败返回空列表，用户能拿到部分结果，不会全部丢失。
- **章纲第3层串行而非并行**：每批生成时注入上批最后一章的 `ending` 作为下一批的 `prev_ending`，解决跨批次章节连续性问题。如果并行生成，各批次之间无法保持情节连贯。
- **章纲不完整时的 warning 机制**：某批失败时，`result.data["warnings"]` 包含 `{range, hint}` 字典，`hint` 从失败章节所属卷的 `summary/arc_goal/main_conflict` 提取，为用户提供可操作的补全提示。

**效果：** 对于 100 章的小说，第1+2层并行约 30-60 秒；第3层分批串行（约 4 批）约 4-8 分钟。整体比旧版线性流程快约 40%，且信息保真度更高。
