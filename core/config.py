"""
全局配置模块
负责加载环境变量，提供系统级别的配置参数
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ======================================
# 基础路径配置
# ======================================
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
LANCEDB_DIR = Path(os.getenv("LANCEDB_DIR", DATA_DIR / "lancedb"))
DB_PATH = DATA_DIR / "novels.db"

# 确保数据目录存在
DATA_DIR.mkdir(parents=True, exist_ok=True)
LANCEDB_DIR.mkdir(parents=True, exist_ok=True)

# ======================================
# 各家 API Key（按需填写，未填的提供商不可用）
# ======================================
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # Claude 系列
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")  # DeepSeek V3 / R1
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY", "")  # 豆包（火山引擎 Ark）
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")  # 通义千问（DashScope）
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")  # Google Gemini

# 默认使用的模型（可被项目级别设置覆盖）
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "claude-opus-4-6")

# 向后兼容
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", DEFAULT_MODEL)

# ======================================
# 记忆系统配置
# ======================================
# 生成新章节时获取最近N章的完整内容
RECENT_CHAPTERS_COUNT = 5

# 向量检索返回的最大结果数
VECTOR_TOP_K = 10

# 向量化分块大小（字符数）
CHUNK_SIZE = 500

# Embedding 模型（用于向量化章节内容，需支持中文）
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")

# ======================================
# 写作配置
# ======================================
# 每章目标字数（仅供参考，实际字数由大纲决定）
DEFAULT_CHAPTER_WORDS = 3000

# 字数容差（允许实际字数偏离目标字数的比例，0.30 = ±30%）
WORD_COUNT_TOLERANCE = float(os.getenv("WORD_COUNT_TOLERANCE", "0.30"))

# 默认去AI味写作规则（项目可覆盖）
DEFAULT_DEAI_RULES = """
━━━ 第一级：必须 100% 遵守（影响全文基调，任何一条违反都会让文字充满 AI 感）━━━

▶ 禁止上帝视角
  ❌ "房间很冷。" / "张毅感到恐慌。"
  ✅ "陈默打了个冷颤，把领口往上拉。" / "张毅指节发白，死死抠在吧台边缘。"
  规则：一切外部状态和内心情绪，必须转化为可观察的生理反射、肢体动作或环境细节。

▶ 消灭机械转折词
  ❌ "突然、忽然、竟然、蓦然、旋即"
  ✅ 用前一动作的生理惯性或环境刺激驱动下一动作：
     "揉搓的动作猛地死死掐住" / "耳尖神经质地一抽，脚步骤停"

▶ 台词必须碎片化
  ❌ "你知道吗，这件事其实从一开始就有问题，我早就察觉到了，只是没有说出口。"
  ✅ "不对——" 他顿了顿，喉结动了一下，"从一开始。"
  规则：半句话、结巴、无效口头禅、逻辑矛盾的短句。危机时刻直接用暴力行动截断对白。

▶ 禁止说书人式收尾
  ❌ "这意味着……" / "更大的危机正在逼近……" / "这就是命运的安排。"
  ✅ 在动作瞬间或突如其来的死寂中猝然中止，用物理结果或环境声响直接收尾。

▶ 【硬性禁止】破折号"——"滥用——零容忍，发现即替换
  破折号只有两种合法用法：
    ① 话语被打断/吞回：「"等一下——"他的声音哑住了。」
    ② 声音/动作物理延长：「轰——」「嗡——」
  以下场景绝对禁止使用破折号，改用逗号或句号：
    ❌ 解释说明：「他终于开口——声音很低。」→ ✅「他终于开口，声音很低。」
    ❌ 递进强调：「黑暗吞噬了一切——连回忆也不剩。」→ ✅「黑暗吞噬了一切，连回忆也不剩。」
    ❌ 镜头切换：「门开了——是她。」→ ✅「门开了。是她。」
    ❌ 心理转折：「他以为自己不在乎——原来还是在乎的。」→ ✅「他以为自己不在乎。原来还是在乎的。」
  判断标准：去掉破折号，改成逗号或句号后意思完全成立 → 就不该用破折号。

▶ 【硬性禁止】对比转折句式——零容忍，发现即视为严重违规
  ❌ "不是……而是……"（所有变体：不是A而是B、不是A，是B、不是A是B）
  ❌ "与其说……不如说……"
  ❌ "与其……不如……"
  这类句式是 AI 生成文本的最强识别标志，任何场合均不得使用，无例外。
  ✅ 改为直接陈述各自事实，分开成独立句子：
     × "那不是恐惧，是愤怒。" → ✅ "胸腔里堵着什么，像火。"
     × "不是他不想说，而是无从开口。" → ✅ "他张了张嘴。什么都没出来。"

━━━ 第二级：强烈推荐（显著提升文学质感，尽量遵守）━━━

▶ 生理细节替换 AI 套话
  ❌ "瞳孔骤缩、大脑空白、心跳漏一拍"（AI 高频套话，禁用）
  ✅ "视线边缘发黑、耳鸣、咬肌痉挛、忘记眨眼导致眼角干裂"

▶ 句式物理化
  ❌ "开门。拔刀。血流。"（连续无主语碎句）
  ✅ "门板被撞开的瞬间，刀刃已自下而上撩过去，带出一线暗红粘稠。"
  规则：将零碎动作按物理因果融成长短错落的完整叙事句。

▶ 替换模糊动词
  ❌ "似乎、仿佛、宛如、隐约"
  ✅ 用具有空间位移感的确凿动词；删去无意义的"着、地"及长副词。

━━━ 第三级：按情节类型适用（有相关场景时执行）━━━

▶ 超能力/变异场景
  ❌ "金光大盛、残影、高压水枪般喷涌" / "升级、进化"（带仪式感）
  ✅ 写攻击的痛感、摩擦力、阻尼感和肉体代价；变异描写为违背意志的悲剧：
     "皮肤生出硬质死鳞" / "指甲盖里生出灰白肉丝"

▶ 恶劣环境场景
  规则：环境必须对暴露肉体发起持续侵害，写出物理质感：
  踩菌丝 → 鞋底碾碎脓包的黏稠回弹；菌丝贴皮肤 → 发痒、发烫、顺毛孔往肉里钻的排异感

▶ 超凡感知场景
  ❌ "雷达、信号、波段、网络扫描"（电子科技词汇）
  ✅ "千万根针同时扎进神经末梢"（降级为原始生物本能与痛苦）

▶ 空间交代
  ❌ "车厢很黑。" / "现场一片混乱。"（纯概念化）
  ✅ 必须用带有破坏痕迹、湿度、气味、磨损的物理特写铺设画面。

▶ 配角发言
  规则：普通配角只输出基于其职业身份的本能、猜测或偏见，不掌握超越自身视角的全局信息，不能充当设定百科全书。"""

# ── Temperature 默认值（按 Agent 角色）──────────────────────
TEMPERATURE_OUTLINE = float(os.getenv("TEMPERATURE_OUTLINE", "0.6"))
TEMPERATURE_CHARACTER = float(os.getenv("TEMPERATURE_CHARACTER", "0.7"))
TEMPERATURE_WRITER = float(os.getenv("TEMPERATURE_WRITER", "0.8"))
TEMPERATURE_REVIEWER = float(os.getenv("TEMPERATURE_REVIEWER", "0.2"))
TEMPERATURE_POLISHER = float(os.getenv("TEMPERATURE_POLISHER", "0.5"))
TEMPERATURE_READER = float(os.getenv("TEMPERATURE_READER", "0.5"))
TEMPERATURE_CANVAS = float(os.getenv("TEMPERATURE_CANVAS", "0.7"))

# 审核通过的最高冲突等级（0-10，超过此值需要人工确认）
AUTO_APPROVE_THRESHOLD = 6

# AI 审核-修改自动循环的最大迭代次数（单轮 fix-review 上限）
MAX_REVIEW_ITERATIONS = int(os.getenv("MAX_REVIEW_ITERATIONS", "5"))

# 全局审核总次数上限（fix 轮 + 重写轮合计），超过后选历史最高分版本作为终稿
MAX_TOTAL_ATTEMPTS = int(os.getenv("MAX_TOTAL_ATTEMPTS", "10"))

# 审核通过所需的最低评分（满分 10）——达到此分数才退出修改循环
REVIEW_SCORE_THRESHOLD = float(os.getenv("REVIEW_SCORE_THRESHOLD", "7.0"))

# 低于此分数时触发完整重写（带审核反馈喂给写手），设为 0 禁用
# 设为与 REVIEW_SCORE_THRESHOLD 相同，保证只要没通过审核就必然触发重写
LOW_SCORE_REWRITE_THRESHOLD = float(os.getenv("LOW_SCORE_REWRITE_THRESHOLD", "7.0"))

# ======================================
# 流水线审核配置（三关卡漏斗式）
# ======================================
# 关卡1 局部校对官熔断阈值（大纲+人设一致性）
GATE_CONTEXT_THRESHOLD = float(os.getenv("GATE_CONTEXT_THRESHOLD", "8.0"))
# 关卡2 全局场记熔断阈值（状态+世界观+时空逻辑）
GATE_CONTINUITY_THRESHOLD = float(os.getenv("GATE_CONTINUITY_THRESHOLD", "8.5"))
# 关卡3 文风打磨官熔断阈值（去AI痕迹+文风）
GATE_STYLISTIC_THRESHOLD = float(os.getenv("GATE_STYLISTIC_THRESHOLD", "8.0"))
# 每个关卡的最大重试次数
MAX_GATE_RETRIES = int(os.getenv("MAX_GATE_RETRIES", "2"))
# 并行四审核流水线的最大审核轮数（每轮 = 审核 + 修正，全部通过提前退出）
# 用户可在设置页「写作质量」中按项目覆盖此默认值
MAX_PARALLEL_REVIEW_ROUNDS = int(os.getenv("MAX_PARALLEL_REVIEW_ROUNDS", "5"))
# 最终得分权重
FINAL_SCORE_WEIGHTS = (0.3, 0.4, 0.3)  # (局部校对, 全局场记, 文风打磨)
# 时空与状态检查官是否使用 Agentic 模式（主动查询历史原文/档案再评分，准确度更高但耗时更长）
CONTINUITY_TRACKER_AGENTIC = os.getenv("CONTINUITY_TRACKER_AGENTIC", "1") == "1"

# ======================================
# 内容压缩配置
# ======================================
# 世界观单个 value 超过此字数时触发 LLM 字段级压缩
# 压缩目标由 LLM 根据 key 名自动判断（规则类多保留，背景描述类可压得更短）
COMPRESS_WORLD_THRESHOLD = int(os.getenv("COMPRESS_WORLD_THRESHOLD", "300"))
# 世界观压缩时给 LLM 的参考上限（LLM 会根据 key 类型在此范围内自行决定保留多少）
COMPRESS_WORLD_TARGET_MAX = int(os.getenv("COMPRESS_WORLD_TARGET_MAX", "400"))

# 大纲各字段触发压缩的阈值（超过才调 LLM，不超过直接用原文）
COMPRESS_OUTLINE_THRESHOLD = int(os.getenv("COMPRESS_OUTLINE_THRESHOLD", "150"))

# 大纲各字段的压缩目标字数（分字段配置，供 LLM 参考，非硬截断）
COMPRESS_OUTLINE_TARGETS: dict[str, int] = {
    "theme": int(os.getenv("COMPRESS_OUTLINE_THEME_TARGET", "100")),
    "main_conflict": int(os.getenv("COMPRESS_OUTLINE_CONFLICT_TARGET", "300")),
    "protagonist_arc": int(os.getenv("COMPRESS_OUTLINE_ARC_TARGET", "500")),
    "ending_summary": int(os.getenv("COMPRESS_OUTLINE_ENDING_TARGET", "500")),
}

# ======================================
# 版本控制配置
# ======================================
# 每个内容保留的最大历史版本数
MAX_VERSIONS = 10

# ======================================
# Agentic 模式配置
# ======================================
# 每次任务最多调用工具的次数（超出后强制输出最终结果）
AGENTIC_MAX_TOOL_CALLS = int(os.getenv("AGENTIC_MAX_TOOL_CALLS", "15"))

# 连续无效查询（重复/空结果）达到此次数时视为停滞，强制终止工具循环
AGENTIC_STALL_THRESHOLD = int(os.getenv("AGENTIC_STALL_THRESHOLD", "3"))

# 单次工具调用超时秒数（超时则跳过该次调用）
AGENTIC_TOOL_TIMEOUT = int(os.getenv("AGENTIC_TOOL_TIMEOUT", "30"))

# Token 预算比例（当估算 token 数达到模型窗口的此比例时，强制终止工具循环）
AGENTIC_TOKEN_BUDGET_RATIO = float(os.getenv("AGENTIC_TOKEN_BUDGET_RATIO", "0.75"))

# ======================================
# 日志配置
# ======================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 调试模式：设为 1 则在每次 LLM 调用时将 system prompt 和 user prompt 打印到 stderr
DEBUG_PROMPTS = os.getenv("DEBUG_PROMPTS", "0") == "1"


# ======================================
# 应用内 API Key 持久化
# ======================================
_API_KEYS_FILE = DATA_DIR / "api_keys.json"
_LOCAL_MODELS_FILE = DATA_DIR / "local_models.json"


def load_saved_keys() -> dict:
    """从 api_keys.json 加载已保存的 API Key"""
    if _API_KEYS_FILE.exists():
        try:
            return json.loads(_API_KEYS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_api_keys(keys: dict):
    """保存 API Key 到 api_keys.json（合并已有配置，空值删除）"""
    existing = load_saved_keys()
    existing.update(keys)
    existing = {k: v for k, v in existing.items() if v and v.strip()}
    _API_KEYS_FILE.write_text(
        json.dumps(existing, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def apply_saved_keys():
    """将 api_keys.json 中的 Key 注入到 os.environ（优先级高于 .env）"""
    for key, value in load_saved_keys().items():
        if value and value.strip():
            os.environ[key] = value.strip()


# ======================================
# 本地模型配置（local_models.json）
# ======================================

def load_local_models() -> list[dict]:
    """
    读取本地模型配置列表。
    每条记录格式：
    {
        "id":           "qwen2.5:7b",          # Ollama 模型名 / OpenAI-compatible model name
        "display":      "Qwen2.5 7B（本地）",   # UI 显示名
        "base_url":     "http://localhost:11434/v1",
        "api_key":      "",                     # 留空 = 无需 Key（Ollama 默认）
        "context_window": "128K",               # 可选
        "note":         ""                      # 可选备注
    }
    """
    if _LOCAL_MODELS_FILE.exists():
        try:
            data = json.loads(_LOCAL_MODELS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_local_models(models: list[dict]):
    """
    保存本地模型配置列表到 local_models.json，
    并同步更新 core.llm.MODEL_REGISTRY（热加载，无需重启）。
    """
    _LOCAL_MODELS_FILE.write_text(
        json.dumps(models, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # 热加载：同步更新已导入的 MODEL_REGISTRY
    try:
        from core.llm import MODEL_REGISTRY, _build_local_model_entry, _LOCAL_MODEL_PREFIX
        # 先移除所有旧的本地模型条目
        old_keys = [k for k, v in MODEL_REGISTRY.items() if v.get("local")]
        for k in old_keys:
            MODEL_REGISTRY.pop(k, None)
        # 重新注册
        for m in models:
            mid = m.get("id", "").strip()
            if mid:
                MODEL_REGISTRY[mid] = _build_local_model_entry(m)
    except Exception:
        pass  # llm 模块未加载时忽略，下次 import 时会自动读取


# 启动时自动应用已保存的 Key
apply_saved_keys()
