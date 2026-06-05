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

# 审核通过的最高冲突等级（0-10，超过此值需要人工确认）
AUTO_APPROVE_THRESHOLD = 3

# AI 审核-修改自动循环的最大迭代次数（防止无限循环）
MAX_REVIEW_ITERATIONS = int(os.getenv("MAX_REVIEW_ITERATIONS", "5"))

# 审核通过所需的最低评分（满分 10）
REVIEW_SCORE_THRESHOLD = float(os.getenv("REVIEW_SCORE_THRESHOLD", "8.0"))

# 低于此分数时触发完整重写（带审核反馈喂给写手），设为 0 禁用
LOW_SCORE_REWRITE_THRESHOLD = float(os.getenv("LOW_SCORE_REWRITE_THRESHOLD", "6.0"))

# ======================================
# 版本控制配置
# ======================================
# 每个内容保留的最大历史版本数
MAX_VERSIONS = 10

# ======================================
# 日志配置
# ======================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


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
