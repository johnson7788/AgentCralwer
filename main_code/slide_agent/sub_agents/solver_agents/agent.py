import json
import logging
from typing import AsyncGenerator, Optional, Dict, Any, List

from google.genai import types
from google.adk.events import Event, EventActions
from google.adk.agents import LoopAgent, BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse

from ...config import DISCOVERY_AGENT_CONFIG, VALIDATOR_AGENT_CONFIG
from ...create_model import create_model
from .prompt import DISCOVERY_PROMPT, VALIDATION_PROMPT
from .tools import search_solver, run_code
from ...utils import stringify_references

logger = logging.getLogger(__name__)


def _only_json(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start:end + 1]
        return json.loads(text)
    except Exception:
        return None


# ===== Discovery Agent =====
class DiscoveryAgent(LlmAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="DiscoveryAgent",
            model=create_model(DISCOVERY_AGENT_CONFIG["model"], DISCOVERY_AGENT_CONFIG["provider"]),
            description="根据问题检索方案→生成/改写代码→试跑",
            instruction=self._dynamic_instruction,
            tools=[search_solver, run_code],
            before_model_callback=self._before_model_cb,
            after_model_callback=self._after_model_cb,
            **kwargs
        )

    def _dynamic_instruction(self, ctx: InvocationContext) -> str:
        problem = ctx.state.get("problem", "")
        language = ctx.state.get("language", "python")
        constraints = ctx.state.get("constraints", "")
        tests = ctx.state.get("tests") or []
        default_args = ctx.state.get("default_args") or []
        default_stdin = ctx.state.get("default_stdin") or ""
        last_feedback = ctx.state.get("last_feedback") or {}

        return DISCOVERY_PROMPT.format(
            PROBLEM=problem,
            LANGUAGE=language,
            CONSTRAINTS=constraints,
            TESTS=json.dumps(tests, ensure_ascii=False),
            DEFAULT_ARGS=json.dumps(default_args, ensure_ascii=False),
            DEFAULT_STDIN=default_stdin,
            LAST_FEEDBACK=json.dumps(last_feedback, ensure_ascii=False)
        )

    def _before_model_cb(self, callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
        logger.info(f"[DiscoveryAgent] start. problem={callback_context.state.get('problem')}")
        return None

    def _after_model_cb(self, callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
        parts = llm_response.content.parts or []
        part_one = parts[0] if parts else None
        # 工具调用或工具响应 → 直接放过，由框架继续驱动工具执行
        if part_one and (part_one.function_call is not None or part_one.function_response is not None):
            return llm_response

        # 最终文本应只包含 JSON（含 code 与 run_result）
        txt = "\n".join([p.text for p in parts if getattr(p, "text", None)])
        data = _only_json(txt)
        if not data:
            logger.warning("[DiscoveryAgent] 未解析到 JSON，原始输出保存在 state['last_solution_raw']")
            callback_context.state["last_solution_raw"] = txt
            return None

        callback_context.state["last_solution"] = data
        if "run_result" in data:
            callback_context.state["last_run"] = data.get("run_result")

        return llm_response

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # 每轮开始清理上轮验证状态
        ctx.session.state["last_validation"] = None
        async for e in super()._run_async_impl(ctx):
            yield e


# ===== Verifier Agent =====
class VerifierAgent(LlmAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="VerifierAgent",
            model=create_model(VALIDATOR_AGENT_CONFIG["model"], VALIDATOR_AGENT_CONFIG["provider"]),
            description="检查运行结果与需求是否匹配；失败则给出修复建议/下一步搜索与测试",
            instruction=self._dynamic_instruction,
            tools=[run_code],  # 验证阶段可补充运行更多样例
            before_model_callback=self._before_model_cb,
            after_model_callback=self._after_model_cb,
            **kwargs
        )

    def _dynamic_instruction(self, ctx: InvocationContext) -> str:
        problem = ctx.state.get("problem", "")
        language = ctx.state.get("language", "python")
        tests = ctx.state.get("tests") or []
        last_solution = ctx.state.get("last_solution") or {}
        last_run = ctx.state.get("last_run") or {}
        constraints = ctx.state.get("constraints", "")
        default_args = ctx.state.get("default_args") or []
        default_stdin = ctx.state.get("default_stdin") or ""
        return VALIDATION_PROMPT.format(
            PROBLEM=problem,
            LANGUAGE=language,
            CONSTRAINTS=constraints,
            TESTS=json.dumps(tests, ensure_ascii=False),
            LAST_SOLUTION=json.dumps(last_solution, ensure_ascii=False),
            LAST_RUN=json.dumps(last_run, ensure_ascii=False),
            DEFAULT_ARGS=json.dumps(default_args, ensure_ascii=False),
            DEFAULT_STDIN=default_stdin
        )

    def _before_model_cb(self, callback_context: CallbackContext, llm_request: LlmRequest) -> Optional[LlmResponse]:
        logger.info("[VerifierAgent] start.")
        return None

    def _after_model_cb(self, callback_context: CallbackContext, llm_response: LlmResponse) -> Optional[LlmResponse]:
        parts = llm_response.content.parts or []
        part_one = parts[0] if parts else None
        if part_one and (part_one.function_call is not None or part_one.function_response is not None):
            return llm_response

        txt = "\n".join([p.text for p in parts if getattr(p, "text", None)])
        data = _only_json(txt)
        if not data:
            callback_context.state["last_validation_raw"] = txt
            return None
        callback_context.state["last_validation"] = data
        # 提取给下一轮 Discovery 的提示
        callback_context.state["last_feedback"] = {
            "reason": data.get("reason", ""),
            "next_actions": data.get("next_actions") or [],
        }
        return llm_response


# ===== Controller Agent（循环控制/结果汇总） =====
class ControllerAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="ControllerAgent",
            description="根据验证结果终止或继续，输出最终方案或进入下一轮修复",
            **kwargs
        )

    def _fmt_success(self, st: Dict[str, Any]) -> str:
        sol = st.get("last_solution") or {}
        run = st.get("last_run") or {}
        refs_text = stringify_references(st.get("references", {})) or ""
        code = sol.get("code", "")
        filename = sol.get("filename", "")
        language = sol.get("language", "")
        notes = sol.get("notes", "")
        stdout = (run or {}).get("stdout", "")
        stderr = (run or {}).get("stderr", "")
        exit_code = (run or {}).get("exit_code", 0)

        parts = [
            "# ✅ 解决方案（已通过验证）",
            f"- 语言：{language}",
            f"- 文件：{filename}",
            f"- 退出码：{exit_code}",
            "## 运行输出（stdout）",
            f"```\n{stdout}\n```",
            "## 运行错误（stderr）",
            f"```\n{stderr}\n```",
            "## 代码",
            f"```{language}\n{code}\n```",
        ]
        if notes:
            parts.append("## 说明")
            parts.append(notes)
        if refs_text:
            parts.append("## 参考链接（检索）")
            parts.append(refs_text)
        return "\n".join(parts)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        max_attempts = int(st.get("max_attempts", 8))
        attempts = int(st.get("attempts", 0))

        val = st.get("last_validation") or {}
        verdict = (val.get("verdict") or "").upper()

        # 每轮结束 attempts +1
        attempts += 1
        st["attempts"] = attempts

        if verdict == "PASS":
            final_text = self._fmt_success(st)
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=final_text)]))
            yield Event(author=self.name, actions=EventActions(escalate=True))
            return

        if attempts >= max_attempts:
            # 超过最大尝试次数，返回当前最优/最新信息
            failure_reason = val.get("reason", "达到最大尝试次数。")
            sol = st.get("last_solution") or {}
            code = sol.get("code") or "(无)"
            language = sol.get("language", "")
            warn = [
                "# ⚠️ 未能在限定轮次内通过验证",
                f"- 原因：{failure_reason}",
                f"- 语言：{language}",
                "## 最新代码快照",
                f"```{language}\n{code}\n```"
            ]
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text="\n".join(warn))]))
            yield Event(author=self.name, actions=EventActions(escalate=True))
            return

        # 否则继续下一轮（由 LoopAgent 控制）
        return


def _loop_before(callback_context: CallbackContext):
    # 每次启动循环前清零尝试次数（agent.py 会先给默认0）
    state = callback_context.state
    state["attempts"] = 0
    return None


solver_loop_agent = LoopAgent(
    name="CodeSolverLoopAgent",
    max_iterations=200,
    sub_agents=[
        DiscoveryAgent(),
        VerifierAgent(),
        ControllerAgent(),
    ],
    before_agent_callback=_loop_before
)
