"""
Agent 轨迹追踪模块 — Arize Phoenix + OpenTelemetry

功能：
- 在 app.py 启动时自动拉起 Phoenix 子进程（localhost:6006）
- 注册 Anthropic / OpenAI SDK 自动 instrumentor，零侵入捕获所有 LLM 调用
- 暴露 get_tracer() 供 workflow.py 添加手动 span
- 支持通过环境变量 PHOENIX_HOST 跳过本地进程，连接外部 Phoenix（如 Docker）

访问 UI：http://localhost:6006

依赖：arize-phoenix 已在项目 venv 中安装（pip install arize-phoenix），
      启动时优先使用 venv 内的 .venv/bin/phoenix 可执行文件。

环境变量：
  NEUPEN_TRACING=0        禁用 tracing（默认启用）
  PHOENIX_HOST            指定外部 Phoenix host，设置后跳过本地启动
  PHOENIX_PORT            Phoenix HTTP 端口（默认 6006）
  PHOENIX_GRPC_PORT       Phoenix gRPC 端口（默认 4317）
  PHOENIX_BIN             覆盖 phoenix 可执行文件路径（默认使用 venv 内的 phoenix）
"""

import atexit
import logging
import os
import shutil
import subprocess
import sys
import time

logger = logging.getLogger(__name__)

# Phoenix 默认配置
_PHOENIX_HOST = os.environ.get("PHOENIX_HOST", "localhost")
_PHOENIX_PORT = int(os.environ.get("PHOENIX_PORT", "6006"))
_PHOENIX_GRPC_PORT = int(os.environ.get("PHOENIX_GRPC_PORT", "4317"))
_USE_EXTERNAL_PHOENIX = os.environ.get("PHOENIX_HOST") is not None

_phoenix_proc: subprocess.Popen = None
_tracer_initialized = False
_tracer = None

# Phoenix 启动超时（秒）——首次启动需要跑数据库迁移，约 10-15s
_PHOENIX_STARTUP_TIMEOUT = int(os.environ.get("PHOENIX_STARTUP_TIMEOUT", "30"))


def _find_phoenix_bin() -> str:
    """
    按优先级查找 phoenix 可执行文件：
    1. 环境变量 PHOENIX_BIN（高级用户手动覆盖）
    2. 当前 venv 的 bin 目录（与 sys.executable 同目录，pip install 默认位置）
    3. PATH 中的 phoenix
    返回找到的路径，找不到返回 None。
    """
    # 1. 环境变量覆盖
    env_bin = os.environ.get("PHOENIX_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin

    # 2. venv bin 目录（pip install arize-phoenix 的默认安装位置）
    venv_bin = os.path.join(os.path.dirname(sys.executable), "phoenix")
    if os.path.isfile(venv_bin) and os.access(venv_bin, os.X_OK):
        return venv_bin

    # 3. PATH
    path_bin = shutil.which("phoenix")
    if path_bin:
        return path_bin

    return None


def _start_phoenix_server() -> bool:
    """
    在后台启动 Phoenix server。
    返回 True 表示成功启动或已在运行。
    """
    global _phoenix_proc

    if _USE_EXTERNAL_PHOENIX:
        logger.info(f"[Tracing] 使用外部 Phoenix: {_PHOENIX_HOST}:{_PHOENIX_PORT}")
        return True

    import socket

    # 检测端口是否已有 Phoenix 在运行
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        if s.connect_ex((_PHOENIX_HOST, _PHOENIX_PORT)) == 0:
            logger.info(f"[Tracing] Phoenix 已在运行 (port {_PHOENIX_PORT})，跳过启动")
            return True

    phoenix_bin = _find_phoenix_bin()
    if not phoenix_bin:
        logger.warning(
            "[Tracing] 未找到 phoenix 可执行文件。\n"
            "请运行：pip install arize-phoenix\n"
            "或设置环境变量 PHOENIX_BIN 指向 phoenix 二进制路径"
        )
        return False

    try:
        _phoenix_proc = subprocess.Popen(
            [phoenix_bin, "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info(f"[Tracing] Phoenix 已启动 (pid={_phoenix_proc.pid}, bin={phoenix_bin})")
    except Exception as e:
        logger.warning(f"[Tracing] Phoenix 启动失败：{e}")
        return False

    # 等待端口就绪
    deadline = time.time() + _PHOENIX_STARTUP_TIMEOUT
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex((_PHOENIX_HOST, _PHOENIX_PORT)) == 0:
                logger.info(f"[Tracing] Phoenix 就绪：http://{_PHOENIX_HOST}:{_PHOENIX_PORT}")
                return True
        time.sleep(0.5)

    logger.warning(
        f"[Tracing] Phoenix 在 {_PHOENIX_STARTUP_TIMEOUT}s 内未就绪，trace 数据将丢失。"
        f"可设置 PHOENIX_STARTUP_TIMEOUT 延长等待时间。"
    )
    return False


def _stop_phoenix_server():
    """atexit 回调：关闭 Phoenix 子进程"""
    global _phoenix_proc
    if _phoenix_proc and _phoenix_proc.poll() is None:
        _phoenix_proc.terminate()
        try:
            _phoenix_proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _phoenix_proc.kill()
        logger.info("[Tracing] Phoenix 子进程已关闭")


def _setup_otel(project_name: str = "neupen-novel"):
    """
    初始化 OpenTelemetry tracer，配置 OTLP exporter 指向 Phoenix。
    注册 Anthropic + OpenAI instrumentor。
    """
    global _tracer

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
    except ImportError:
        logger.warning("[Tracing] opentelemetry-sdk 未安装，跳过初始化")
        return

    endpoint = f"http://{_PHOENIX_HOST}:{_PHOENIX_GRPC_PORT}"

    resource = Resource.create({"service.name": project_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _tracer = trace.get_tracer(project_name)
    logger.info(f"[Tracing] OTel tracer 初始化完成，endpoint={endpoint}")

    # 注册 Anthropic instrumentor
    try:
        from openinference.instrumentation.anthropic import AnthropicInstrumentor
        AnthropicInstrumentor().instrument(tracer_provider=provider)
        logger.info("[Tracing] AnthropicInstrumentor 已注册")
    except ImportError:
        logger.warning("[Tracing] openinference-instrumentation-anthropic 未安装")
    except Exception as e:
        logger.warning(f"[Tracing] AnthropicInstrumentor 注册失败：{e}")

    # 注册 OpenAI instrumentor（DeepSeek/豆包/千问/Gemini 均走 OpenAI 兼容接口）
    try:
        from openinference.instrumentation.openai import OpenAIInstrumentor
        OpenAIInstrumentor().instrument(tracer_provider=provider)
        logger.info("[Tracing] OpenAIInstrumentor 已注册")
    except ImportError:
        logger.warning("[Tracing] openinference-instrumentation-openai 未安装")
    except Exception as e:
        logger.warning(f"[Tracing] OpenAIInstrumentor 注册失败：{e}")


def init_tracing(project_name: str = "neupen-novel") -> bool:
    """
    应用启动入口：启动 Phoenix + 初始化 OTel。
    幂等，多次调用只初始化一次。

    Returns:
        True 表示 tracing 已就绪，False 表示初始化失败（应用仍可正常运行）。
    """
    global _tracer_initialized
    if _tracer_initialized:
        return _tracer is not None

    _tracer_initialized = True

    if os.environ.get("NEUPEN_TRACING", "1") == "0":
        logger.info("[Tracing] NEUPEN_TRACING=0，tracing 已禁用")
        return False

    ok = _start_phoenix_server()
    if not ok:
        return False

    _setup_otel(project_name)
    atexit.register(_stop_phoenix_server)

    if _tracer is not None:
        logger.info(f"[Tracing] 就绪。Phoenix UI: http://{_PHOENIX_HOST}:{_PHOENIX_PORT}")
    return _tracer is not None


def get_tracer():
    """获取全局 tracer 实例，未初始化时返回 None。"""
    return _tracer


class _NoopSpan:
    """当 tracer 不可用时的占位 span，支持 with 语法和 set_attribute。"""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def set_attribute(self, key, value): pass
    def set_status(self, *args): pass
    def record_exception(self, *args): pass


def start_span(name: str, attributes: dict = None):
    """
    便捷方法：创建一个 span（支持 with 语法）。
    tracer 不可用时返回 _NoopSpan，业务代码无需判空。

    用法：
        with start_span("agent.writer", {"chapter": 3}) as span:
            span.set_attribute("model", "claude-opus-4")
            result = writer.write(...)
    """
    if _tracer is None:
        return _NoopSpan()

    ctx_mgr = _tracer.start_as_current_span(name)
    span = ctx_mgr.__enter__()
    if attributes:
        for k, v in attributes.items():
            if v is not None:
                span.set_attribute(k, str(v) if not isinstance(v, (int, float, bool, str)) else v)
    return _SpanContextManager(ctx_mgr, span)


class _SpanContextManager:
    """包装 OTel span context manager，暴露 set_attribute 接口。"""
    def __init__(self, ctx_mgr, span):
        self._ctx_mgr = ctx_mgr
        self._span = span

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            from opentelemetry.trace import StatusCode
            self._span.set_status(StatusCode.ERROR, str(exc_val))
            self._span.record_exception(exc_val)
        self._ctx_mgr.__exit__(exc_type, exc_val, exc_tb)
        return False

    def set_attribute(self, key: str, value):
        if value is not None:
            self._span.set_attribute(
                key,
                str(value) if not isinstance(value, (int, float, bool, str)) else value
            )
