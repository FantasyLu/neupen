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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")   # Claude 系列
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")    # DeepSeek V3 / R1
DOUBAO_API_KEY    = os.getenv("DOUBAO_API_KEY", "")       # 豆包（火山引擎 Ark）
QWEN_API_KEY      = os.getenv("QWEN_API_KEY", "")         # 通义千问（DashScope）
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")       # Google Gemini

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
DEFAULT_DEAI_RULES = """## 视角与情感
- 禁止上帝视角。不写"房间很冷"，写"陈默打了个冷颤，把领口往上拉"；不写"张毅很恐慌"，写"张毅指节发白，死死抠在吧台边缘"
- 剔除一切抽象情感形容词，将心理和情绪直接转化为生理反射、肢体动作或环境细节

## 台词与对白
- 角色台词必须碎片化、自私、带有生理惯性：半句话、结巴、无效口头禅、逻辑矛盾的短句
- 危机时刻用冷酷命令、推搡或暴力行动截断平淡对白——对方还没问完，陈默已经一把揪住他衣领往后一扯
- 核心设定、情报必须由处于极度负面情绪（恐惧/愤怒/濒死）中的角色以歇斯底里的碎裂形式输出，宁可信息残缺也不能对白工整

## 感官与生理
- 极度震惊或恐惧时写真实生理障碍："视线边缘发黑、耳鸣、咬肌痉挛、忘记眨眼导致眼角干裂"，禁用"瞳孔骤缩、大脑空白、心跳漏一拍"等AI套话
- 角色每个动作切换必须由前一动作的生理惯性或突发环境刺激驱动：用"揉搓的动作猛地死死掐住"替换"突然停下"；用"耳尖神经质地一抽"替换"突然听到声音"
- 全文消除"突然、忽然、竟然、蓦然、旋即"等机械转折词

## 句式与动词
- 按物理因果将零碎动作融成长短错落的完整叙事句，禁止连续三四字无主语短句："开门。拔刀。血流。"→"门板被撞开的瞬间，刀刃已自下而上撩过去，带出一线暗红粘稠"
- 删去"着、地"及无意义长副词，用具有空间位移感的确凿动词替换"似乎、仿佛、宛如、隐约"
- 禁用"不是……而是……"/"与其说……不如说……"：直接并列事实——"那不是血，是一股泛着暗金光的黏稠活体"

## 章节收尾
- 段落/章节结尾必须在动作瞬间或突如其来的死寂中猝然中止，用物理结果或环境声响直接收尾
- 禁止出现"这意味着……"、"更大的危机正在逼近……"、"这就是命运的安排"等说书人式总结

## 物理质感与空间
- 超能力/变异遵循粗粝的力学和生物学逻辑：写攻击的痛感、摩擦力、阻尼感和肉体代价，用"砸进湿棉被的闷响"、"刀被肉牙卡住"替换"金光大盛、残影、高压水枪般喷涌"
- 恶劣环境必须对暴露肉体发起持续侵害：踩在菌丝上要写鞋底碾碎脓包的黏稠回弹；菌丝贴皮肤要写发痒、发烫、顺毛孔往肉里钻的排异感
- 身体变异描写为违背人类意志的悲剧：用"皮肤生出硬质死鳞"、"指甲盖里生出灰白肉丝"替换带有仪式感的"升级、进化"
- 超凡感知全部降级为最原始的生物本能与痛苦：用"千万根针同时扎进神经末梢"替换"雷达、信号、波段、网络扫描"等电子科技词汇
- 交代空间背景时必须用带有破坏痕迹、湿度、气味、磨损的物理特写铺设画面，禁止纯概念化交代（"车厢很黑"、"现场一片混乱"）

## 配角处理
- 普通配角只能输出基于其职业身份的本能、猜测或偏见，不得掌握超越自身视角的全局信息，不能充当设定百科全书"""

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
GATE_CONTEXT_THRESHOLD = float(os.getenv("GATE_CONTEXT_THRESHOLD", "8.5"))
# 关卡2 全局场记熔断阈值（状态+世界观+时空逻辑）
GATE_CONTINUITY_THRESHOLD = float(os.getenv("GATE_CONTINUITY_THRESHOLD", "9.0"))
# 关卡3 文风打磨官熔断阈值（去AI痕迹+文风）
GATE_STYLISTIC_THRESHOLD = float(os.getenv("GATE_STYLISTIC_THRESHOLD", "8.0"))
# 每个关卡的最大重试次数
MAX_GATE_RETRIES = int(os.getenv("MAX_GATE_RETRIES", "2"))
# 最终得分权重
FINAL_SCORE_WEIGHTS = (0.3, 0.4, 0.3)  # (局部校对, 全局场记, 文风打磨)

# ======================================
# 版本控制配置
# ======================================
# 每个内容保留的最大历史版本数
MAX_VERSIONS = 10

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


# 启动时自动应用已保存的 Key
apply_saved_keys()
