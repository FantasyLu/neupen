"""
Agentic Loop 驱动器

让 LLM 在生成最终内容前，通过工具调用主动查询数据库，
类似 Claude 写代码时边读文件边思考的方式。

防死循环机制（四重保护）：
  1. 最大调用次数：达到 max_tool_calls 后强制输出
  2. 去重保护：相同 tool + args 只执行一次，重复调用直接返回缓存
  3. 停滞检测：连续 stall_threshold 次均无新信息时提前终止
  4. Token 预算：估算 token 总量接近模型窗口上限时强制输出
  5. 单次工具超时：每次工具调用有独立超时保护
"""

import hashlib
import json
import re
import sys
import threading
from typing import Callable, Optional, Any

from core.config import (
    AGENTIC_MAX_TOOL_CALLS,
    AGENTIC_STALL_THRESHOLD,
    AGENTIC_TOOL_TIMEOUT,
    AGENTIC_TOKEN_BUDGET_RATIO,
)


# 解析 LLM 输出中的工具调用块
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)

# 估算 token 数（粗略：中文约1.5字/token，英文约4字符/token）
# 这里统一用字符数 / 2 作保守估算
_CHARS_PER_TOKEN = 2


def _estimate_tokens(messages: list[dict]) -> int:
    """估算当前消息列表的 token 总数（保守估算）"""
    total_chars = sum(len(str(m.get("content", ""))) for m in messages)
    return total_chars // _CHARS_PER_TOKEN


def _hash_call(tool_name: str, args: dict) -> str:
    """生成工具调用的唯一哈希（用于去重）"""
    key = json.dumps(
        {"tool": tool_name, "args": args}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.md5(key.encode()).hexdigest()


# ──────────────────────────────────────────────────────────────
# 步骤事件类型（供 UI 展示）
# ──────────────────────────────────────────────────────────────


class StepEvent:
    """描述 Agentic Loop 中的一个步骤事件"""

    THINKING = "thinking"          # LLM 输出了思考内容（含工具调用请求）
    TOOL_CALL = "tool_call"        # 即将执行工具
    TOOL_RESULT = "tool_result"    # 工具执行完成
    DUPLICATE_SKIP = "duplicate_skip"  # 重复调用，使用缓存
    STALL_DETECTED = "stall_detected"  # 检测到停滞
    MAX_CALLS_REACHED = "max_calls"    # 达到最大调用次数
    BUDGET_EXCEEDED = "budget"         # Token 预算耗尽
    FINAL_OUTPUT = "final_output"      # 最终输出
    CONTENT_READY = "content_ready"    # 写作/审核全流程完成，携带最终内容和结果
    STATUS_MSG = "status_msg"          # 纯状态文字通知（无数据副作用）


# ──────────────────────────────────────────────────────────────
# Agentic Loop 主体
# ──────────────────────────────────────────────────────────────


class AgenticLoop:
    """
    Agentic Loop 驱动器。

    使用方式：
        loop = AgenticLoop(
            llm=self.llm,
            tool_executor=ToolExecutor(self.memory),
            step_callback=my_callback,  # 可选，UI 实时更新
        )
        result = loop.run(system_prompt, initial_prompt)

    step_callback 签名：(event_type: str, data: dict) -> None
    """

    def __init__(
        self,
        llm,  # NovelLLM 实例
        tool_executor,  # ToolExecutor 实例
        max_tool_calls: int = AGENTIC_MAX_TOOL_CALLS,
        stall_threshold: int = AGENTIC_STALL_THRESHOLD,
        tool_timeout: int = AGENTIC_TOOL_TIMEOUT,
        token_budget_ratio: float = AGENTIC_TOKEN_BUDGET_RATIO,
        step_callback: Optional[Callable[[str, dict], None]] = None,
    ):
        self.llm = llm
        self.tool_executor = tool_executor
        self.max_tool_calls = max_tool_calls
        self.stall_threshold = stall_threshold
        self.tool_timeout = tool_timeout
        self.step_callback = step_callback

        # 根据模型上下文窗口估算 token 预算
        try:
            from core.llm import MODEL_REGISTRY

            model_info = MODEL_REGISTRY.get(llm.model_id, {})
            raw_window = model_info.get("context_window", 100000)
            # context_window 可能是 "200K"/"64K"/"1M" 字符串，需解析成整数
            if isinstance(raw_window, str):
                raw_window = raw_window.strip().upper()
                if raw_window.endswith("M"):
                    context_window = int(float(raw_window[:-1]) * 1_000_000)
                elif raw_window.endswith("K"):
                    context_window = int(float(raw_window[:-1]) * 1_000)
                else:
                    context_window = int(raw_window)
            else:
                context_window = int(raw_window)
        except Exception:
            context_window = 100000
        self._token_budget = int(context_window * token_budget_ratio)

    def _emit(self, event_type: str, data: dict):
        """触发步骤事件回调（兼容旧式 step_callback）"""
        if self.step_callback:
            try:
                self.step_callback(event_type, data)
            except Exception:
                pass  # 回调错误不影响主流程

    def _execute_with_timeout(self, tool_name: str, args: dict) -> str:
        """带超时保护的工具执行"""
        result_holder = [None]
        error_holder = [None]

        def _run():
            try:
                result_holder[0] = self.tool_executor.execute(tool_name, args)
            except Exception as e:
                error_holder[0] = str(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=self.tool_timeout)

        if t.is_alive():
            return f"[超时] 工具 '{tool_name}' 执行超过 {self.tool_timeout} 秒，已跳过"
        if error_holder[0]:
            return f"[执行错误] {error_holder[0]}"
        return result_holder[0] or "[工具返回空结果]"

    def _parse_tool_calls(self, response: str) -> list[tuple[str, dict]]:
        """
        从 LLM 响应中解析所有工具调用。
        返回 [(tool_name, args), ...] 列表。
        预处理全角标点 → 半角，再用 json.loads 严格解析，失败则用 json_repair 兜底修复。
        """
        # 全角标点 → 半角（LLM 有时在 JSON 里混入中文标点）
        # 用 dict 形式避免源文件中文引号字符长度歧义
        _FULLWIDTH_MAP = str.maketrans({
            '，': ',', '：': ':', '；': ';',
            '\u201c': '"', '\u201d': '"',   # " "
            '\u2018': "'", '\u2019': "'",   # ' '
            '（': '(', '）': ')',
            '【': '[', '】': ']',
        })

        calls = []
        for raw in _TOOL_CALL_RE.findall(response):
            raw = raw.strip().translate(_FULLWIDTH_MAP)
            data = None
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                # json_repair 兜底：处理 LLM 输出的不规范 JSON（单引号、缺引号、末尾逗号等）
                try:
                    from json_repair import repair_json
                    repaired = repair_json(raw, return_objects=True)
                    if isinstance(repaired, dict):
                        data = repaired
                        print(
                            f"[AgenticLoop] 工具调用 JSON 已自动修复（原始错误：{e}）",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"[AgenticLoop] 工具调用 JSON 修复后非 dict，忽略：{raw}",
                            file=sys.stderr,
                        )
                except Exception as repair_err:
                    print(
                        f"[AgenticLoop] 工具调用 JSON 解析失败：{e}；修复也失败：{repair_err}\n原文：{raw}",
                        file=sys.stderr,
                    )

            if data is None:
                continue
            tool_name = data.get("tool", "")
            args = data.get("args", {})
            if tool_name and isinstance(args, dict):
                calls.append((tool_name, args))
            else:
                print(
                    f"[AgenticLoop] 忽略格式不完整的工具调用：{raw}",
                    file=sys.stderr,
                )
        return calls

    def run_gen(
        self,
        system_prompt: str,
        initial_user_prompt: str,
        max_tokens_per_call: int = 8192,
        force_tool_first: bool = False,
    ):
        """
        Generator 版 Agentic Loop。

        每个步骤事件以 (event_type, data) 的形式 yield 出来，
        调用方可逐事件处理（UI 实时刷新）。
        最终输出在 event_type == StepEvent.FINAL_OUTPUT 的 data["text"] 里。

        force_tool_first: 若为 True，第一轮若 LLM 未调用任何工具，
        则注入强制提示要求先查询，再发起第二轮，确保至少有一轮工具调用。

        流程：
          1. 发送初始 prompt，LLM 响应
          2. 若响应含工具调用：执行工具 → 追加结果 → 继续循环
          3. 若响应无工具调用：视为最终输出，yield FINAL_OUTPUT 后结束
          4. 任一终止条件触发（次数/停滞/预算）：
             追加强制指令 → 最后一次调用 → yield FINAL_OUTPUT 后结束
        """
        messages = [{"role": "user", "content": initial_user_prompt}]
        call_count = 0
        call_cache: dict[str, str] = {}  # hash → result（去重缓存）
        stall_counter = 0  # 连续无新信息次数
        _all_reasonings: list[str] = []  # 收集所有轮次的 reasoning_content
        _is_first_round = True  # 是否是第一轮响应

        # ── 主循环 ──────────────────────────────────────────────
        while True:
            # Token 预算检查
            current_tokens = _estimate_tokens(messages)
            if current_tokens > self._token_budget:
                yield (StepEvent.BUDGET_EXCEEDED, {
                    "estimated_tokens": current_tokens,
                    "budget": self._token_budget,
                })
                break

            # 调用 LLM
            try:
                response = self.llm.generate_chat(
                    system_prompt=system_prompt,
                    messages=messages,
                    max_tokens=max_tokens_per_call,
                )
            except Exception as e:
                print(f"[AgenticLoop] LLM 调用失败：{e}", file=sys.stderr)
                yield (StepEvent.FINAL_OUTPUT, {
                    "text": f"[生成失败] {e}",
                    "all_reasoning": "\n\n---\n\n".join(_all_reasonings),
                })
                return

            # 收集本轮 reasoning_content（支持思维链的模型才有值）
            round_reasoning = self.llm.last_reasoning or ""
            if round_reasoning:
                _all_reasonings.append(round_reasoning)

            # 解析工具调用
            tool_calls = self._parse_tool_calls(response)

            # ── 无工具调用 → 检查是否需要强制工具调用 ──────────
            if not tool_calls:
                if force_tool_first and _is_first_round:
                    # 第一轮没有工具调用，注入强制提示要求先查询
                    print(
                        "[AgenticLoop] force_tool_first: 第一轮未调用工具，注入强制提示",
                        file=sys.stderr,
                    )
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": (
                        "⚠️ 你刚才直接输出了正文，但规则要求必须先通过工具查询信息。\n"
                        "请现在立即调用工具（至少查询出场人物档案和前情摘要），"
                        "查询完成后再输出完整正文。"
                    )})
                    _is_first_round = False
                    continue  # 重新进入循环，不 yield FINAL_OUTPUT
                # 正常最终输出
                yield (StepEvent.FINAL_OUTPUT, {
                    "text": response,
                    "all_reasoning": "\n\n---\n\n".join(_all_reasonings),
                })
                return
            if not tool_calls:
                yield (StepEvent.FINAL_OUTPUT, {
                    "text": response,
                    "all_reasoning": "\n\n---\n\n".join(_all_reasonings),
                })
                return

            # ── 触发思考事件（通知 UI：LLM 决定查询了什么）──────
            thinking_text = _TOOL_CALL_RE.sub("", response).strip()
            yield (StepEvent.THINKING, {
                "thinking_text": thinking_text,
                "tool_count": len(tool_calls),
                "reasoning": round_reasoning,
            })
            _is_first_round = False  # 有工具调用，后续不再强制

            # 将 LLM 响应追加到消息历史
            messages.append({"role": "assistant", "content": response})

            # ── 次数上限检查 ─────────────────────────────────────
            if call_count >= self.max_tool_calls:
                yield (StepEvent.MAX_CALLS_REACHED, {"call_count": call_count})
                break

            # ── 执行所有工具调用 ─────────────────────────────────
            new_info_count = 0
            tool_results_parts = []

            for tool_name, args in tool_calls:
                call_hash = _hash_call(tool_name, args)

                if call_hash in call_cache:
                    result = call_cache[call_hash]
                    yield (StepEvent.DUPLICATE_SKIP, {
                        "tool": tool_name,
                        "args": args,
                        "cached_result_preview": result[:100],
                    })
                else:
                    yield (StepEvent.TOOL_CALL, {
                        "tool": tool_name,
                        "args": args,
                        "call_index": call_count + 1,
                        "max_calls": self.max_tool_calls,
                    })

                    result = self._execute_with_timeout(tool_name, args)
                    call_cache[call_hash] = result
                    call_count += 1
                    new_info_count += 1

                    yield (StepEvent.TOOL_RESULT, {
                        "tool": tool_name,
                        "args": args,
                        "result_preview": result[:200],
                        "result_length": len(result),
                    })

                tool_results_parts.append(f"[工具结果: {tool_name}]\n{result}")

            # 将所有工具结果合并追加（作为 user 消息）
            combined_results = "\n\n".join(tool_results_parts)
            messages.append({"role": "user", "content": combined_results})

            # ── 停滞检测 ─────────────────────────────────────────
            if new_info_count == 0:
                stall_counter += 1
            else:
                stall_counter = 0

            if stall_counter >= self.stall_threshold:
                yield (StepEvent.STALL_DETECTED, {
                    "stall_count": stall_counter,
                    "threshold": self.stall_threshold,
                })
                break

        # ── 强制最终输出（各类终止条件触发后）──────────────────
        messages.append({
            "role": "user",
            "content": (
                "你已收集到足够的信息（或查询已达到上限）。"
                "请现在直接输出完整的最终结果，不要再调用任何工具，不要输出 <tool_call> 块。"
            ),
        })

        try:
            final_response = self.llm.generate_chat(
                system_prompt=system_prompt,
                messages=messages,
                max_tokens=max_tokens_per_call,
            )
        except Exception as e:
            print(f"[AgenticLoop] 强制输出时 LLM 调用失败：{e}", file=sys.stderr)
            final_response = f"[生成失败，已达工具调用上限] {e}"

        final_reasoning = self.llm.last_reasoning or ""
        if final_reasoning:
            _all_reasonings.append(final_reasoning)

        yield (StepEvent.FINAL_OUTPUT, {
            "text": final_response,
            "all_reasoning": "\n\n---\n\n".join(_all_reasonings),
        })

    def run(
        self,
        system_prompt: str,
        initial_user_prompt: str,
        max_tokens_per_call: int = 8192,
    ) -> str:
        """
        同步包装：消费 run_gen()，返回最终文本。
        供非 UI 场景（批量、测试等）直接调用；同时通过 _emit 触发 step_callback。
        """
        final_text = ""
        for event_type, data in self.run_gen(
            system_prompt=system_prompt,
            initial_user_prompt=initial_user_prompt,
            max_tokens_per_call=max_tokens_per_call,
        ):
            self._emit(event_type, data)
            if event_type == StepEvent.FINAL_OUTPUT:
                final_text = data.get("text", "")
        return final_text
