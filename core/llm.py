"""
多提供商 LLM 统一接口
支持 Anthropic Claude / DeepSeek / 豆包 / 通义千问 / Google Gemini
"""

import os
import sys
import time
import random
import logging
from typing import Generator, Callable, Any

logger = logging.getLogger(__name__)

# ── 重试配置（可通过环境变量覆盖） ─────────────────────────────────────
_RETRY_MAX_ATTEMPTS = int(os.environ.get("LLM_RETRY_MAX_ATTEMPTS", "4"))
_RETRY_BASE_DELAY   = float(os.environ.get("LLM_RETRY_BASE_DELAY", "2.0"))   # 秒
_RETRY_MAX_DELAY    = float(os.environ.get("LLM_RETRY_MAX_DELAY", "60.0"))   # 秒
_RETRY_JITTER       = float(os.environ.get("LLM_RETRY_JITTER", "0.3"))       # 随机抖动比例


def _is_retryable_error(e: Exception) -> bool:
    """判断异常是否可重试（限流 / 服务端错误 / 网络超时）"""
    err_str = str(e).lower()
    # Anthropic SDK 具名异常
    try:
        import anthropic as _anth
        if isinstance(e, (_anth.RateLimitError, _anth.APIStatusError)):
            if isinstance(e, _anth.APIStatusError):
                return e.status_code in (429, 500, 502, 503, 504)
            return True  # RateLimitError 一律重试
    except ImportError:
        pass
    # OpenAI / 字符串匹配兜底
    if any(k in err_str for k in ("429", "rate limit", "too many requests",
                                   "500", "502", "503", "504",
                                   "connection", "timeout", "timed out",
                                   "service unavailable", "overloaded")):
        return True
    return False


def _with_retry(fn: Callable[[], Any],
                max_attempts: int = None,
                base_delay: float = None,
                max_delay: float = None,
                label: str = "") -> Any:
    """
    指数退避重试包装器（仅用于非流式调用）。

    重试条件：429 限流 / 5xx 服务端错误 / 网络超时
    不重试：401 认证错误 / 其他明确的客户端错误

    Args:
        fn:           无参可调用，直接发起请求
        max_attempts: 最大尝试次数（含首次），默认读 _RETRY_MAX_ATTEMPTS
        base_delay:   首次重试等待秒数，默认 _RETRY_BASE_DELAY
        max_delay:    单次等待上限，默认 _RETRY_MAX_DELAY
        label:        日志标记
    """
    max_attempts = max_attempts or _RETRY_MAX_ATTEMPTS
    base_delay   = base_delay   or _RETRY_BASE_DELAY
    max_delay    = max_delay    or _RETRY_MAX_DELAY

    last_exc: Exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if not _is_retryable_error(e):
                raise
            if attempt == max_attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay *= (1 + _RETRY_JITTER * random.uniform(-1, 1))
            logger.warning(
                "[LLM-Retry] %s 第%d/%d次失败，%.1fs后重试: %s",
                label, attempt, max_attempts, delay, e
            )
            time.sleep(delay)

    raise last_exc

# ======================================
# 模型注册表
# ======================================

MODEL_REGISTRY: dict[str, dict] = {

    # ── Anthropic Claude ──────────────────────────────────────────────
    "claude-opus-4-6": {
        "display_name": "Claude Opus 4.6",
        "provider": "anthropic",
        "provider_name": "Anthropic",
        "writing_style": (
            "文笔细腻，擅长人物心理刻画和氛围渲染，对话层次丰富，"
            "叙事逻辑严密，长篇跨章节一致性极佳。"
        ),
        "strengths": ["复杂情节逻辑", "人物深度塑造", "跨章节一致性最强", "冲突检测质量最高"],
        "best_genres": ["严肃文学", "悬疑推理", "历史", "多线叙事", "言情"],
        "speed": "慢",
        "cost_level": "高",
        "context_window": "200K",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_url": "https://console.anthropic.com/",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "综合质量天花板，推荐用于对质量要求极高的作品",
    },
    "claude-sonnet-4-6": {
        "display_name": "Claude Sonnet 4.6",
        "provider": "anthropic",
        "provider_name": "Anthropic",
        "writing_style": (
            "平衡质量与速度，文笔流畅自然，人物塑造有深度，"
            "适合大多数题材，综合表现优秀。"
        ),
        "strengths": ["速度适中", "综合质量优秀", "成本可控", "理解能力强"],
        "best_genres": ["玄幻", "都市", "言情", "悬疑", "科幻"],
        "speed": "中",
        "cost_level": "中",
        "context_window": "200K",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_url": "https://console.anthropic.com/",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "Claude 家族性价比最高，大多数项目的首选",
    },
    "claude-haiku-4-5-20251001": {
        "display_name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "provider_name": "Anthropic",
        "writing_style": (
            "简洁明快，适合快速生成初稿，文风干净利落，"
            "为后期人工润色留有较大空间。"
        ),
        "strengths": ["极快速度", "成本最低", "适合批量初稿"],
        "best_genres": ["轻小说", "爽文", "快节奏动作", "短章节"],
        "speed": "极快",
        "cost_level": "低",
        "context_window": "200K",
        "api_key_env": "ANTHROPIC_API_KEY",
        "api_key_url": "https://console.anthropic.com/",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "追求速度时的 Claude 选择，质量低于 Opus/Sonnet",
    },

    # ── DeepSeek ──────────────────────────────────────────────────────
    "deepseek-chat": {
        "display_name": "DeepSeek V3",
        "provider": "openai_compatible",
        "provider_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "writing_style": (
            "中文功底扎实，爽文节奏把控出色，对话简练有力，"
            "升级流、打脸流写法娴熟，天然契合国内网文阅读习惯。"
        ),
        "strengths": ["中文网文风格最佳", "爽文节奏感强", "成本极低", "速度快", "国内直连稳定"],
        "best_genres": ["玄幻修仙", "都市爽文", "系统流", "无限流", "穿越重生"],
        "speed": "快",
        "cost_level": "极低",
        "context_window": "64K",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_url": "https://platform.deepseek.com/api_keys",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "性价比之王，爽文/网文的不二之选；服务端磁盘缓存自动命中",
    },
    "deepseek-reasoner": {
        "display_name": "DeepSeek R1（推理增强）",
        "provider": "openai_compatible",
        "provider_name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "writing_style": (
            "逻辑推理能力极强，擅长设计精密的伏笔和反转，"
            "适合需要严密推理链的硬核故事，文风偏理性严谨。"
        ),
        "strengths": ["情节逻辑无懈可击", "伏笔设计精妙", "悬疑反转出彩", "审核质量高"],
        "best_genres": ["悬疑推理", "科幻硬核", "历史架空", "烧脑玄幻"],
        "speed": "较慢",
        "cost_level": "低",
        "context_window": "64K",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api_key_url": "https://platform.deepseek.com/api_keys",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "推理型模型，尤其适合担任审核师角色；写作速度较慢",
    },

    # ── 豆包（字节跳动 Ark 平台）─────────────────────────────────────
    "doubao-pro-32k": {
        "display_name": "豆包 Pro 32K",
        "provider": "openai_compatible",
        "provider_name": "字节跳动",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "writing_style": (
            "语言生动活泼，现代感强，擅长都市题材和情感描写，"
            "对话自然口语化，节奏轻快，年轻读者好感度高。"
        ),
        "strengths": ["都市言情最佳", "对话自然", "情感渲染", "国内直连极快", "价格亲民"],
        "best_genres": ["都市言情", "青春校园", "轻喜剧", "甜宠", "现代都市"],
        "speed": "快",
        "cost_level": "低",
        "context_window": "32K",
        "api_key_env": "DOUBAO_API_KEY",
        "api_key_url": "https://console.volcengine.com/ark",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "需在火山引擎控制台开通 Ark 并获取 API Key；模型 ID 即 doubao-pro-32k",
    },
    "doubao-lite-32k": {
        "display_name": "豆包 Lite 32K",
        "provider": "openai_compatible",
        "provider_name": "字节跳动",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "writing_style": (
            "轻量快速，文风轻松，适合快速批量生成初稿，"
            "后续可配合人工或润色师 Agent 提升质量。"
        ),
        "strengths": ["速度最快", "成本最低", "适合批量初稿生成"],
        "best_genres": ["轻小说", "短篇故事", "快节奏爽文"],
        "speed": "极快",
        "cost_level": "极低",
        "context_window": "32K",
        "api_key_env": "DOUBAO_API_KEY",
        "api_key_url": "https://console.volcengine.com/ark",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "轻量版，追求极致速度和成本时使用",
    },

    # ── 通义千问（阿里云 DashScope）──────────────────────────────────
    "qwen-max": {
        "display_name": "通义千问 Max",
        "provider": "openai_compatible",
        "provider_name": "阿里云",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "writing_style": (
            "文风稳健，对东方文化和典故理解深刻，古雅词汇运用自然，"
            "细节描写丰富，擅长营造古典意境和仙气飘飘的氛围。"
        ),
        "strengths": ["东方文化底蕴最深", "古风词汇运用", "意境营造", "仙侠设定合理", "细节丰富"],
        "best_genres": ["古风仙侠", "历史架空", "东方玄幻", "宫廷斗争", "修真"],
        "speed": "中",
        "cost_level": "中",
        "context_window": "32K",
        "api_key_env": "QWEN_API_KEY",
        "api_key_url": "https://dashscope.console.aliyun.com/apiKey",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "古风/仙侠题材首选，中华文化理解最深",
    },
    "qwen-plus": {
        "display_name": "通义千问 Plus",
        "provider": "openai_compatible",
        "provider_name": "阿里云",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "writing_style": (
            "文风均衡，中文表达自然，速度与质量兼顾，"
            "上下文窗口超长（131K），适合大批量章节稳定输出。"
        ),
        "strengths": ["速度快", "成本低", "超长上下文（131K）", "稳定输出"],
        "best_genres": ["玄幻", "修仙", "都市", "通用"],
        "speed": "快",
        "cost_level": "低",
        "context_window": "131K",
        "api_key_env": "QWEN_API_KEY",
        "api_key_url": "https://dashscope.console.aliyun.com/apiKey",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "千问家族性价比最高，适合大批量写作",
    },
    "qwen-turbo": {
        "display_name": "通义千问 Turbo",
        "provider": "openai_compatible",
        "provider_name": "阿里云",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "writing_style": (
            "极致速度，文风简洁，适合对延迟敏感的场景，"
            "可用于快速生成提纲和摘要类任务。"
        ),
        "strengths": ["速度极快", "成本极低", "适合辅助任务"],
        "best_genres": ["任意题材（初稿/辅助用途）"],
        "speed": "极快",
        "cost_level": "极低",
        "context_window": "1M",
        "api_key_env": "QWEN_API_KEY",
        "api_key_url": "https://dashscope.console.aliyun.com/apiKey",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "超低成本，推荐仅用于章节摘要等辅助任务",
    },

    # ── Google Gemini ─────────────────────────────────────────────────
    "gemini-2.0-flash": {
        "display_name": "Gemini 2.0 Flash",
        "provider": "openai_compatible",
        "provider_name": "Google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "writing_style": (
            "创意天马行空，世界观构建宏大，想象力出众，"
            "语言现代流畅，擅长设定独特的异域风情和科幻设定。"
        ),
        "strengths": ["创意世界观构建", "极速响应", "成本极低", "超长上下文（1M）"],
        "best_genres": ["科幻", "奇幻世界构建", "冒险探索", "末世", "赛博朋克"],
        "speed": "极快",
        "cost_level": "极低",
        "context_window": "1M",
        "api_key_env": "GEMINI_API_KEY",
        "api_key_url": "https://aistudio.google.com/app/apikey",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "创意与速度并重；需要能访问 Google 服务的网络环境",
    },
    "gemini-1.5-pro": {
        "display_name": "Gemini 1.5 Pro",
        "provider": "openai_compatible",
        "provider_name": "Google",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "writing_style": (
            "逻辑严谨，擅长多线并行叙事，超长上下文（1M token）"
            "确保百万字规模的全局一致性，适合史诗级长篇。"
        ),
        "strengths": ["超长上下文全局一致性", "多线叙事把控", "逻辑严密"],
        "best_genres": ["史诗玄幻", "多线叙事", "宏大世界观", "长篇历史"],
        "speed": "中",
        "cost_level": "中",
        "context_window": "1M",
        "api_key_env": "GEMINI_API_KEY",
        "api_key_url": "https://aistudio.google.com/app/apikey",
        "supports_caching": True,
        "supports_streaming": True,
        "note": "最大上下文窗口，适合超长篇小说；需要 Google 服务访问权限",
    },
}

# ── 本地模型支持 ──────────────────────────────────────────────────────────

_LOCAL_MODEL_PREFIX = "__local__"  # 内部标记，不对外暴露


def _build_local_model_entry(m: dict) -> dict:
    """根据 local_models.json 中的一条记录构建 MODEL_REGISTRY 条目"""
    return {
        "display_name": m.get("display") or m.get("id", "本地模型"),
        "provider": "openai_compatible",
        "provider_name": "本地模型",
        "base_url": m.get("base_url", "http://localhost:11434/v1").rstrip("/"),
        "api_key_env": "",          # 本地模型无固定 Key 环境变量
        "api_key_override": m.get("api_key", "").strip() or "local",  # 空 Key 用占位符
        "local": True,
        "supports_caching": False,
        "supports_streaming": True,
        "context_window": m.get("context_window", "未知"),
        "speed": "本地",
        "cost_level": "免费",
        "strengths": ["完全离线", "数据不出本机", "无调用费用"],
        "best_genres": ["所有题材"],
        "note": m.get("note", "") or f"本地模型，Base URL: {m.get('base_url', 'http://localhost:11434/v1')}",
        "writing_style": "取决于具体模型",
    }


def _load_local_models_into_registry():
    """从 local_models.json 加载本地模型到 MODEL_REGISTRY（模块初始化时调用）"""
    try:
        from core.config import load_local_models
        for m in load_local_models():
            mid = m.get("id", "").strip()
            if mid:
                MODEL_REGISTRY[mid] = _build_local_model_entry(m)
    except Exception:
        pass  # 初始化阶段失败静默处理


_load_local_models_into_registry()

# 默认模型（无项目级别配置时使用）
DEFAULT_MODEL_ID = os.getenv("DEFAULT_MODEL", "claude-opus-4-6")


# ======================================
# 注册表工具函数
# ======================================

def get_model_info(model_id: str) -> dict:
    """获取模型信息；不存在时 fallback 到默认模型"""
    return MODEL_REGISTRY.get(model_id, MODEL_REGISTRY.get(DEFAULT_MODEL_ID, list(MODEL_REGISTRY.values())[0]))


def list_models_by_provider() -> dict[str, list[tuple[str, dict]]]:
    """按提供商分组，返回 {provider_name: [(model_id, info), ...]}"""
    result: dict[str, list] = {}
    for model_id, info in MODEL_REGISTRY.items():
        pname = info["provider_name"]
        result.setdefault(pname, []).append((model_id, info))
    return result


def get_api_key(model_id: str) -> str:
    """从环境变量读取指定模型的 API Key；本地模型返回配置的 override 值"""
    info = get_model_info(model_id)
    if info.get("local"):
        return info.get("api_key_override", "local") or "local"
    return os.getenv(info["api_key_env"], "").strip()


def check_api_key(model_id: str) -> tuple[bool, str]:
    """
    检查 API Key 是否已配置
    Returns:
        (ok: bool, message: str)
    """
    info = get_model_info(model_id)
    # 本地模型：无需 API Key，直接放行
    if info.get("local"):
        return True, ""
    key = get_api_key(model_id)
    if not key:
        return False, (
            f"未找到 {info['display_name']} 的 API Key。\n"
            f"请在 .env 文件中设置 {info['api_key_env']}=your_key\n"
            f"获取地址：{info.get('api_key_url', '')}"
        )
    return True, ""


# ======================================
# 统一 LLM 接口
# ======================================

class NovelLLM:
    """
    多提供商 LLM 统一封装
    - Anthropic 模型：使用 anthropic SDK，支持 prompt caching
    - 其他模型：使用 openai SDK（OpenAI-compatible API）
    """

    def __init__(self, model_id: str = None, novel_id: int = None):
        self.model_id = model_id or DEFAULT_MODEL_ID
        self.info = get_model_info(self.model_id)
        self.provider = self.info["provider"]
        self._client = None
        self.novel_id = novel_id
        # 最近一次非流式 generate() 的思维链内容（无则为空字符串）。
        # 每次 generate() 调用后自动更新，供 UI 层读取展示。
        self.last_reasoning: str = ""

    @property
    def client(self):
        """懒加载 API 客户端"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self):
        ok, msg = check_api_key(self.model_id)
        if not ok:
            raise RuntimeError(f"❌ {msg}")

        api_key = get_api_key(self.model_id)

        if self.provider == "anthropic":
            import anthropic as _anthropic
            return _anthropic.Anthropic(api_key=api_key)

        elif self.provider == "openai_compatible":
            try:
                import openai as _openai
            except ImportError:
                raise RuntimeError(
                    "请先安装 openai 包：pip install openai\n"
                    "（DeepSeek / 豆包 / 千问 / Gemini / 本地模型 都通过 OpenAI 兼容接口调用）"
                )
            return _openai.OpenAI(
                api_key=api_key or "local",   # OpenAI SDK 要求非空，本地服务不校验
                base_url=self.info["base_url"]
            )

        raise RuntimeError(f"不支持的提供商类型：{self.provider}")

    # ── 调试辅助 ─────────────────────────────────────────────────

    @staticmethod
    def _log_prompt(method: str, model_id: str, system_prompt: str, user_content: str):
        """将 prompt 打印到 stderr。由 DEBUG_PROMPTS 环境变量控制。"""
        from core.config import DEBUG_PROMPTS
        if not DEBUG_PROMPTS:
            return

        def _trunc(s: str, head: int = 2000, tail: int = 500) -> str:
            return s

        sep = "=" * 80
        sys.stderr.write(f"\n{sep}\n")
        sys.stderr.write(f"[LLM] {method} | model={model_id}\n")
        sys.stderr.write(f"{sep}\n")
        sys.stderr.write(f"--- SYSTEM ({len(system_prompt)} chars) ---\n")
        sys.stderr.write(_trunc(system_prompt) + "\n")
        sys.stderr.write(f"--- USER ({len(user_content)} chars) ---\n")
        sys.stderr.write(_trunc(user_content) + "\n")
        sys.stderr.write(f"{sep}\n\n")
        sys.stderr.flush()

    # ── Token 统计 ─────────────────────────────────────────────────

    def _record_tokens(self, input_tokens: int, output_tokens: int):
        """记录 token 消耗到关联的小说项目数据库。"""
        if not self.novel_id:
            return
        db = None
        try:
            from core.models import get_db, Novel
            db = get_db()
            novel = db.query(Novel).filter(Novel.id == self.novel_id).first()
            if novel:
                novel.total_input_tokens = (novel.total_input_tokens or 0) + input_tokens
                novel.total_output_tokens = (novel.total_output_tokens or 0) + output_tokens
                db.commit()
        except Exception:
            pass  # token 统计失败不应影响主流程
        finally:
            if db is not None:
                db.close()

    # ── 对外统一接口 ────────────────────────────────────────────────

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192,
                 cache_system: bool = True,
                 temperature: float = None) -> str:
        """
        非流式生成
        Anthropic 模型在 system_prompt > 1000 字符时自动启用 prompt caching
        """
        self._log_prompt("generate", self.model_id, system_prompt, user_prompt)
        if self.provider == "anthropic":
            return self._generate_anthropic(system_prompt, user_prompt, max_tokens, cache_system, temperature)
        return self._generate_openai(system_prompt, user_prompt, max_tokens, temperature)

    def generate_with_reasoning(self, system_prompt: str, user_prompt: str,
                                 max_tokens: int = 8192,
                                 temperature: float = None) -> tuple[str, str]:
        """
        非流式生成，同时返回正文和思维链。
        返回: (content, reasoning)
          - content:   模型输出的正文部分
          - reasoning: 思维链内容（无则为空字符串）
        内部调用 generate()，reasoning 从 self.last_reasoning 读取。
        """
        content = self.generate(system_prompt, user_prompt, max_tokens=max_tokens, temperature=temperature)
        return content, self.last_reasoning

    def generate_stream(self, system_prompt: str, user_prompt: str,
                         max_tokens: int = 8192,
                         temperature: float = None) -> Generator[str, None, None]:
        """流式生成，逐 token yield"""
        self._log_prompt("generate_stream", self.model_id, system_prompt, user_prompt)
        if self.provider == "anthropic":
            gen = self._stream_anthropic(system_prompt, user_prompt, max_tokens, temperature)
        else:
            gen = self._stream_openai(system_prompt, user_prompt, max_tokens, temperature)

        # 包装生成器，流结束后估算 token 消耗
        collected = []
        for chunk in gen:
            collected.append(chunk)
            yield chunk
        # 流结束后估算（中文约 1 token / 1.5 字符，保守按 2 字符 = 1 token）
        if collected:
            input_est = max(1, (len(system_prompt) + len(user_prompt)) // 2)
            output_est = max(1, len("".join(collected)) // 2)
            self._record_tokens(input_est, output_est)

    def generate_chat(self, system_prompt: str, messages: list,
                       max_tokens: int = 4096,
                       temperature: float = None) -> str:
        """
        多轮对话接口
        messages 格式: [{"role": "user"/"assistant", "content": "..."}]

        对于 OpenAI-compatible 提供商（DeepSeek/豆包/千问/Gemini），
        自动将系统提示词中的动态上下文（--- 之后的部分）移到最后一条用户消息前缀，
        使 system message 保持字节级稳定以命中服务端磁盘缓存。
        """
        chat_digest = "\n".join(
            f"[{m['role']}] {m.get('content', '')[:300]}{'...' if len(m.get('content', '')) > 300 else ''}"
            for m in messages
        )
        self._log_prompt("generate_chat", self.model_id, system_prompt, chat_digest)
        if self.provider == "anthropic":
            try:
                import anthropic as _anthropic
                kwargs = dict(
                    model=self.model_id,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=messages,
                )
                if temperature is not None:
                    kwargs["temperature"] = temperature
                response = self.client.messages.create(**kwargs)
                # 记录 token 消耗
                if hasattr(response, 'usage'):
                    self._record_tokens(
                        getattr(response.usage, 'input_tokens', 0),
                        getattr(response.usage, 'output_tokens', 0)
                    )
                return response.content[0].text
            except _anthropic.RateLimitError:
                raise RuntimeError("⚠️ API 调用频率超限，请稍后重试")
            except _anthropic.AuthenticationError:
                raise RuntimeError("❌ Anthropic API Key 无效，请检查 ANTHROPIC_API_KEY")
            except Exception as e:
                raise RuntimeError(f"Anthropic API 调用失败：{e}")
        else:
            # OpenAI-compatible 提供商：拆分静态/动态部分以优化缓存命中
            static_prompt = system_prompt
            dynamic_context = ""
            if "\n---\n" in system_prompt:
                parts = system_prompt.split("\n---\n", 1)
                static_prompt = parts[0].strip()
                dynamic_context = parts[1].strip()
            try:
                api_messages = [{"role": "system", "content": static_prompt}]
                if dynamic_context:
                    # 将动态上下文注入最后一条用户消息前缀
                    if messages and messages[-1]["role"] == "user":
                        prefixed = messages[:-1] + [{
                            "role": "user",
                            "content": f"{dynamic_context}\n\n---\n\n{messages[-1]['content']}"
                        }]
                        api_messages += prefixed
                    else:
                        api_messages.append({"role": "user", "content": dynamic_context})
                        api_messages += messages
                else:
                    api_messages += messages
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    max_tokens=max_tokens,
                    messages=api_messages,
                )
                # 记录 token 消耗
                if hasattr(response, 'usage'):
                    self._record_tokens(
                        getattr(response.usage, 'prompt_tokens', 0),
                        getattr(response.usage, 'completion_tokens', 0)
                    )
                return self._extract_openai_content(response.choices[0].message)
            except Exception as e:
                raise RuntimeError(f"{self.info['display_name']} API 调用失败：{e}")

    # ── Anthropic 后端 ──────────────────────────────────────────────

    def _generate_anthropic(self, system_prompt: str, user_prompt: str,
                             max_tokens: int, cache_system: bool,
                             temperature: float = None) -> str:
        import anthropic as _anthropic

        self.last_reasoning = ""  # Anthropic 不支持思维链分离

        # 对长系统提示词启用 prompt caching
        if cache_system and len(system_prompt) > 1000:
            system_content = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }]
        else:
            system_content = system_prompt

        kwargs = dict(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_content,
            messages=[{"role": "user", "content": user_prompt}]
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        def _do_call():
            try:
                response = self.client.messages.create(**kwargs)
                if hasattr(response, 'usage'):
                    self._record_tokens(
                        getattr(response.usage, 'input_tokens', 0),
                        getattr(response.usage, 'output_tokens', 0)
                    )
                return response.content[0].text
            except _anthropic.AuthenticationError:
                raise RuntimeError("❌ Anthropic API Key 无效，请检查 .env 中的 ANTHROPIC_API_KEY")
            except _anthropic.RateLimitError as e:
                raise e  # 交给 _with_retry 判断是否可重试
            except _anthropic.APIStatusError as e:
                raise e  # 5xx 交给 _with_retry
            except Exception as e:
                raise RuntimeError(f"Anthropic API 调用失败：{e}")

        try:
            return _with_retry(_do_call, label=f"Anthropic/{self.model_id}")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Anthropic API 调用失败（重试耗尽）：{e}")

    def _stream_anthropic(self, system_prompt: str, user_prompt: str,
                           max_tokens: int, temperature: float = None) -> Generator[str, None, None]:
        """
        流式生成：对首次建立连接阶段加重试；yield 开始后不重试（避免重复输出）。
        """
        import anthropic as _anthropic

        kwargs = dict(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        # 重试建立流连接本身（首次 token 前的网络/限流错误）
        def _open_stream():
            return self.client.messages.stream(**kwargs)

        stream_ctx = _with_retry(_open_stream, label=f"Anthropic-stream/{self.model_id}")
        with stream_ctx as stream:
            for text in stream.text_stream:
                yield text

    # ── OpenAI-compatible 后端 ──────────────────────────────────────

    @staticmethod
    def _extract_openai_content(message) -> str:
        """
        从 OpenAI-compatible 响应 message 中提取正文。
        兼容 DeepSeek-R1 的 reasoning_content 字段：
        - reasoning_content（思维链）静默丢弃，与流式模式行为一致，
          避免思维链混入小说正文或 JSON 结果。
        - 若 content 为空但 reasoning_content 非空（极少数情况），
          直接返回 reasoning_content 作为兜底。
        """
        content = getattr(message, "content", None) or ""
        reasoning = getattr(message, "reasoning_content", None) or ""
        if content:
            return content
        if reasoning:
            return reasoning
        return content

    def _generate_openai(self, system_prompt: str, user_prompt: str,
                          max_tokens: int, temperature: float = None) -> str:
        kwargs = dict(
            model=self.model_id,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        def _do_call():
            try:
                response = self.client.chat.completions.create(**kwargs)
                if hasattr(response, 'usage'):
                    self._record_tokens(
                        getattr(response.usage, 'prompt_tokens', 0),
                        getattr(response.usage, 'completion_tokens', 0)
                    )
                msg = response.choices[0].message
                # 提取思维链并存入 last_reasoning，供 UI 层读取
                self.last_reasoning = getattr(msg, "reasoning_content", None) or ""
                return self._extract_openai_content(msg)
            except Exception as e:
                err = str(e)
                if "401" in err or "authentication" in err.lower() or "invalid api key" in err.lower():
                    raise RuntimeError(
                        f"❌ {self.info['display_name']} API Key 无效，"
                        f"请检查 .env 中的 {self.info['api_key_env']}"
                    )
                raise  # 其他异常交给 _with_retry 判断

        try:
            return _with_retry(_do_call, label=f"OpenAI/{self.model_id}")
        except RuntimeError:
            raise
        except Exception as e:
            err = str(e)
            if "429" in err or "rate limit" in err.lower():
                raise RuntimeError("⚠️ API 调用频率超限，请稍后重试")
            raise RuntimeError(f"{self.info['display_name']} API 调用失败：{e}")

    def _stream_openai(self, system_prompt: str, user_prompt: str,
                        max_tokens: int, temperature: float = None) -> Generator[str, None, None]:
        """
        流式生成：对首次建立连接阶段加重试；yield 开始后不重试（避免重复输出）。
        兼容 DeepSeek-R1：reasoning_content chunk 静默收集，不 yield 给调用方
        （避免思维链混入小说正文），仅将最终 content 部分流出。
        """
        kwargs = dict(
            model=self.model_id,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            stream=True
        )
        if temperature is not None:
            kwargs["temperature"] = temperature

        def _open_stream():
            return self.client.chat.completions.create(**kwargs)

        stream = _with_retry(_open_stream, label=f"OpenAI-stream/{self.model_id}")
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # reasoning_content：DeepSeek-R1 思维链字段，静默跳过不输出
            if getattr(delta, "reasoning_content", None):
                continue
            if delta.content:
                yield delta.content
