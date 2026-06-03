"""
多提供商 LLM 统一接口
支持 Anthropic Claude / DeepSeek / 豆包 / 通义千问 / Google Gemini
"""

import os
from typing import Generator

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
        "supports_caching": False,
        "supports_streaming": True,
        "note": "性价比之王，爽文/网文的不二之选",
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
        "supports_caching": False,
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
        "supports_caching": False,
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
        "supports_caching": False,
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
        "supports_caching": False,
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
        "supports_caching": False,
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
        "supports_caching": False,
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
        "supports_caching": False,
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
        "supports_caching": False,
        "supports_streaming": True,
        "note": "最大上下文窗口，适合超长篇小说；需要 Google 服务访问权限",
    },
}

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
    """从环境变量读取指定模型的 API Key"""
    info = get_model_info(model_id)
    return os.getenv(info["api_key_env"], "").strip()


def check_api_key(model_id: str) -> tuple[bool, str]:
    """
    检查 API Key 是否已配置
    Returns:
        (ok: bool, message: str)
    """
    info = get_model_info(model_id)
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

    def __init__(self, model_id: str = None):
        self.model_id = model_id or DEFAULT_MODEL_ID
        self.info = get_model_info(self.model_id)
        self.provider = self.info["provider"]
        self._client = None

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
                    "（DeepSeek / 豆包 / 千问 / Gemini 都通过 OpenAI 兼容接口调用）"
                )
            return _openai.OpenAI(
                api_key=api_key,
                base_url=self.info["base_url"]
            )

        raise RuntimeError(f"不支持的提供商类型：{self.provider}")

    # ── 对外统一接口 ────────────────────────────────────────────────

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 8192,
                 cache_system: bool = True) -> str:
        """
        非流式生成
        Anthropic 模型在 system_prompt > 1000 字符时自动启用 prompt caching
        """
        if self.provider == "anthropic":
            return self._generate_anthropic(system_prompt, user_prompt, max_tokens, cache_system)
        return self._generate_openai(system_prompt, user_prompt, max_tokens)

    def generate_stream(self, system_prompt: str, user_prompt: str,
                        max_tokens: int = 8192) -> Generator[str, None, None]:
        """流式生成，逐 token yield"""
        if self.provider == "anthropic":
            yield from self._stream_anthropic(system_prompt, user_prompt, max_tokens)
        else:
            yield from self._stream_openai(system_prompt, user_prompt, max_tokens)

    def generate_chat(self, system_prompt: str, messages: list,
                      max_tokens: int = 4096) -> str:
        """
        多轮对话接口
        messages 格式: [{"role": "user"/"assistant", "content": "..."}]
        """
        if self.provider == "anthropic":
            try:
                import anthropic as _anthropic
                response = self.client.messages.create(
                    model=self.model_id,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=messages,
                )
                return response.content[0].text
            except _anthropic.RateLimitError:
                raise RuntimeError("⚠️ API 调用频率超限，请稍后重试")
            except _anthropic.AuthenticationError:
                raise RuntimeError("❌ Anthropic API Key 无效，请检查 ANTHROPIC_API_KEY")
            except Exception as e:
                raise RuntimeError(f"Anthropic API 调用失败：{e}")
        else:
            try:
                response = self.client.chat.completions.create(
                    model=self.model_id,
                    max_tokens=max_tokens,
                    messages=[{"role": "system", "content": system_prompt}] + messages,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                raise RuntimeError(f"{self.info['display_name']} API 调用失败：{e}")

    # ── Anthropic 后端 ──────────────────────────────────────────────

    def _generate_anthropic(self, system_prompt: str, user_prompt: str,
                             max_tokens: int, cache_system: bool) -> str:
        import anthropic as _anthropic

        # 对长系统提示词启用 prompt caching
        if cache_system and len(system_prompt) > 1000:
            system_content = [{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"}
            }]
        else:
            system_content = system_prompt

        try:
            response = self.client.messages.create(
                model=self.model_id,
                max_tokens=max_tokens,
                system=system_content,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return response.content[0].text
        except _anthropic.RateLimitError:
            raise RuntimeError("⚠️ API 调用频率超限，请稍后重试")
        except _anthropic.AuthenticationError:
            raise RuntimeError("❌ Anthropic API Key 无效，请检查 .env 中的 ANTHROPIC_API_KEY")
        except Exception as e:
            raise RuntimeError(f"Anthropic API 调用失败：{e}")

    def _stream_anthropic(self, system_prompt: str, user_prompt: str,
                          max_tokens: int) -> Generator[str, None, None]:
        with self.client.messages.stream(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}]
        ) as stream:
            for text in stream.text_stream:
                yield text

    # ── OpenAI-compatible 后端 ──────────────────────────────────────

    def _generate_openai(self, system_prompt: str, user_prompt: str,
                         max_tokens: int) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model_id,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            err = str(e)
            if "401" in err or "authentication" in err.lower() or "invalid api key" in err.lower():
                raise RuntimeError(
                    f"❌ {self.info['display_name']} API Key 无效，"
                    f"请检查 .env 中的 {self.info['api_key_env']}"
                )
            if "429" in err or "rate limit" in err.lower():
                raise RuntimeError("⚠️ API 调用频率超限，请稍后重试")
            raise RuntimeError(f"{self.info['display_name']} API 调用失败：{e}")

    def _stream_openai(self, system_prompt: str, user_prompt: str,
                       max_tokens: int) -> Generator[str, None, None]:
        stream = self.client.chat.completions.create(
            model=self.model_id,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            stream=True
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
