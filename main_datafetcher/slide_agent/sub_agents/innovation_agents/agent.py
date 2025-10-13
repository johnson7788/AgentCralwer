# 文件: sub_agents/innovation_agents/agent.py
import os
import json
import sqlite3
import hashlib
import logging
from typing import AsyncGenerator, Optional, Dict, Any, List

from google.genai import types
from google.adk.events import Event, EventActions
from google.adk.agents import LoopAgent, BaseAgent
from google.adk.agents.llm_agent import LlmAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.callback_context import CallbackContext

# ADK 工具：FunctionTool（函数即工具），BaseToolset（动态供工具）
from google.adk.tools.base_toolset import BaseToolset
from ...config import CONTENT_WRITER_AGENT_CONFIG, CHECKER_AGENT_CONFIG
from ...create_model import create_model
from .tools import ALL_TOOLS
from .prompt import AnalyzerAgent_PROMPT, ExecutorAgent_PROMPT


logger = logging.getLogger(__name__)

# ========= 全局常量 =========
DEFAULT_MODEL = os.getenv("SOLVER_MODEL", "gemini-2.0-flash")
DEFAULT_MAX_ATTEMPTS = int(os.getenv("SOLVER_MAX_ATTEMPTS", "6"))
CACHE_DB_PATH = os.getenv("SOLVER_TOOL_CACHE_DB", "tool_cache.db")

# ========= 工具命中缓存（本地 sqlite） =========
class ToolCache:
    def __init__(self, db_path: str = CACHE_DB_PATH):
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS cache(
                key TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL
            )
        """)
        self.conn.commit()

    @staticmethod
    def _key_from_query(q: str) -> str:
        norm = " ".join(q.strip().lower().split())
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    def get(self, query: str) -> Optional[str]:
        k = self._key_from_query(query)
        cur = self.conn.execute("SELECT tool_name FROM cache WHERE key=?", (k,))
        row = cur.fetchone()
        return row[0] if row else None

    def put(self, query: str, tool_name: str):
        k = self._key_from_query(query)
        self.conn.execute("REPLACE INTO cache(key, tool_name) VALUES(?,?)", (k, tool_name))
        self.conn.commit()


# 单例获取
_CACHE_SINGLETON: Optional[ToolCache] = None
def get_cache() -> ToolCache:
    global _CACHE_SINGLETON
    if _CACHE_SINGLETON is None:
        _CACHE_SINGLETON = ToolCache(CACHE_DB_PATH)
    return _CACHE_SINGLETON

def get_cache_key(query: str) -> str:
    return ToolCache._key_from_query(query)


# ========= 根据问题做一个轻量级的启发式排序（后期改成Embedding排序） =========
def rank_tools_for_query(q: str, exclude: Optional[List[str]] = None) -> List[str]:
    ql = (q or "").lower()
    exclude = set(exclude or [])
    scores = []
    for name in ALL_TOOLS.keys():
        score = 0
        if any(ch in ql for ch in "+-*/()") or "计算" in ql:
            score += 5 if name == "calc_expression" else 0
        if "km" in ql or "mile" in ql or "转换" in ql or "换算" in ql:
            score += 5 if name == "unit_convert" else 0
        if "是什么" in ql or "介绍" in ql or "定义" in ql:
            score += 5 if name == "lookup_fact" else 0
        # 默认轻微偏好：表达式 > 换算 > 事实
        base = {"calc_expression": 2, "unit_convert": 1, "lookup_fact": 0}.get(name, 0)
        scores.append((score, base, name))
    ordered = [n for _, _, n in sorted(scores, key=lambda x: (x[0], x[1]), reverse=True)]
    return [n for n in ordered if n not in exclude]

# ========= 动态 Toolset：本轮只把“候选工具”暴露给 LLM =========
class CandidateToolset(BaseToolset):
    def __init__(self, default_top_k: int = 2):
        self.default_top_k = default_top_k

    async def get_tools(self, readonly_context=None) -> list:
        """
        决策逻辑优先级：
        1) 如果 state.cached_tool 存在：仅暴露该工具（缓存直达）。
        2) 否则如果 state.tool_candidates 非空：按 candidates 暴露。
        3) 否则使用 rank_tools_for_query 的前 K 个。
        """
        st = getattr(readonly_context, "state", {}) if readonly_context else {}
        q = st.get("question", "")
        cached = st.get("cached_tool")
        if cached and cached in ALL_TOOLS:
            return [ALL_TOOLS[cached]]

        tried = st.get("tried_tools") or []
        # 分析Agent猜测可以使用的tool
        candidates = st.get("tool_candidates") or []
        if not candidates:
            # 无 LLM 分析结果时，fallback 为本地启发式 Top-K
            topk = int(st.get("try_tool_number", self.default_top_k) or self.default_top_k)
            candidates = rank_tools_for_query(q, exclude=tried)[:topk]

        tools = [ALL_TOOLS[n] for n in candidates if n in ALL_TOOLS]
        # 如果候选为空，兜底给一个最可能的（避免模型无工具可用）
        if not tools:
            ordered = rank_tools_for_query(q, exclude=tried)
            if ordered:
                tools = [ALL_TOOLS[ordered[0]]]
        logger.info(f"[CandidateToolset] expose tools: {[t.name for t in tools]}")
        return tools

    async def close(self) -> None:
        return


# ========= 解析 “SOLVED:true|false JSON:{...}” =========
class SolveResult:
    def __init__(self, solved: bool, tool_name: Optional[str], answer: str, raw: str):
        self.solved = solved
        self.tool_name = tool_name
        self.answer = answer
        self.raw = raw

def _parse_solved(final_output: str) -> SolveResult:
    text = (final_output or "").strip()
    solved_flag = False
    tool_name = None
    payload = {}
    if "SOLVED:true" in text:
        solved_flag = True
    elif "SOLVED:false" in text:
        solved_flag = False
    try:
        idx = text.find("JSON:")
        if idx >= 0:
            j = text[idx + 5:].strip()
            payload = json.loads(j)
            tool_name = payload.get("tool")
    except Exception:
        pass
    answer = payload.get("answer", text)
    return SolveResult(solved_flag, tool_name, answer, text)


# ========= 分析 Agent：给出候选工具列表 =========
class AnalyzerAgent(LlmAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="AnalyzerAgent",
            model=create_model(model=CONTENT_WRITER_AGENT_CONFIG["model"], provider=CONTENT_WRITER_AGENT_CONFIG["provider"]),
            description="分析问题并给出下一轮要尝试的候选工具",
            instruction=self._dynamic_instruction,
            before_model_callback=self._before_model_cb,
            after_model_callback=self._after_model_cb,
            before_agent_callback=self._before_agent_cb,
            **kwargs
        )

    def _dynamic_instruction(self, ctx: InvocationContext) -> str:
        question = ctx.state.get("question", "")
        tried_list = ctx.state.get("tried_tools", [])
        tried = ", ".join(tried_list) if tried_list else "无"
        try_num = int(ctx.state.get("try_tool_number", 2) or 2)
        tool_desc = "\n".join([
            "- calc_expression: 计算含 + - * / 与括号的表达式",
            "- unit_convert: 在 km 与 miles 之间换算",
            "- lookup_fact: 简单知识库查询"
        ])
        return AnalyzerAgent_PROMPT.format(
            try_num=try_num,
            tool_desc=tool_desc,
            question=question,
            tried=tried
        )

    def _before_agent_cb(self, callback_context: CallbackContext) -> None:
        """
        # 如果上一次尝试工具不成功，那么需要清空历史记录，防止上下文过长
        :param callback_context:
        :return:
        """
        # 返回 None，继续调用 LLM
        callback_context.session.events = []
        return None
    def _before_model_cb(self, callback_context: CallbackContext, llm_request) -> Optional[Any]:
        logger.info(f"[AnalyzerAgent] start. question={callback_context.state.get('question')}")
        return None

    def _after_model_cb(self, callback_context: CallbackContext, llm_response) -> Optional[Any]:
        parts = llm_response.content.parts or []
        txt = "\n".join([p.text for p in parts if getattr(p, "text", None)])
        try:
            # 容错：如果模型加了说明性文字，尽力截出 JSON
            s, e = txt.find("{"), txt.rfind("}")
            data = json.loads(txt[s:e+1]) if s != -1 and e != -1 else json.loads(txt)
            cands = data.get("candidates") or []
            # 仅保留已注册的工具名
            cands = [c for c in cands if c in ALL_TOOLS]
            callback_context.state["tool_candidates"] = cands
        except Exception:
            # 兜底：用本地启发式
            q = callback_context.state.get("question", "")
            tried = callback_context.state.get("tried_tools", [])
            k = int(callback_context.state.get("try_tool_number", 2) or 2)
            callback_context.state["tool_candidates"] = rank_tools_for_query(q, exclude=tried)[:k]
        return llm_response


# ========= 执行 Agent（带动态 Toolset）：真正调用工具并给出“是否解决”判定 =========
class ExecutorAgent(LlmAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="ExecutorAgent",
            model=DEFAULT_MODEL,
            description="使用当前候选工具尝试直接解决问题；成功则以结构化格式给出最终答案",
            # 关键：把动态 Toolset 放入 tools，当前轮只暴露“候选工具”
            tools=[CandidateToolset()],
            instruction=self._instruction,
            after_model_callback=self._after_model_cb,
            **kwargs
        )

    def _instruction(self, ctx: InvocationContext) -> str:
        question = ctx.state.get("question", "")
        return ExecutorAgent_PROMPT.format(question=question)

    def _after_model_cb(self, callback_context: CallbackContext, llm_response) -> Optional[Any]:
        parts = llm_response.content.parts or []
        if getattr(parts[0], "function_call"):
            #不是模型回复，是函数的调用
            print(f"进行函数调用: {parts[0]}")
            return llm_response
        txt = "\n".join([p.text for p in parts if getattr(p, "text", None)])
        result = _parse_solved(txt)
        st = callback_context.state
        st["solved"] = result.solved
        st["final_answer"] = result.answer
        st["used_tool"] = result.tool_name
        st["last_raw"] = result.raw
        # 记录失败轨迹（便于前端/日志观测）
        if not result.solved:
            err = {"tool": result.tool_name or "none", "error": result.answer, "raw": result.raw}
            st["failed_attempts"] = (st.get("failed_attempts") or []) + [err]
        return llm_response


# ========= 控制 Agent：更新尝试计数、缓存命中、终止条件与输出 =========
class ControllerAgent(BaseAgent):
    def __init__(self, **kwargs):
        super().__init__(
            name="ControllerAgent",
            description="根据执行结果决定是否收敛输出或继续下一轮",
            **kwargs
        )

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        st = ctx.session.state
        st["attempts"] = int(st.get("attempts", 0)) + 1
        attempts = st["attempts"]
        max_attempts = int(st.get("max_attempts", DEFAULT_MAX_ATTEMPTS))

        solved = bool(st.get("solved"))
        used_tool = st.get("used_tool")
        final_answer = st.get("final_answer") or ""
        question = st.get("question", "")
        tried = st.get("tried_tools") or []

        # 成功：输出+缓存映射
        if solved:
            if used_tool:
                # 成功记忆：问题 -> 工具
                get_cache().put(question, used_tool)
            msg = f"✅ 已解决：{final_answer}\n（工具：{used_tool or 'none'}；尝试次数：{attempts}）"
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=msg)]))
            yield Event(author=self.name, actions=EventActions(escalate=True))
            return

        # 失败但未超出最大次数：记录已尝试工具并继续下一轮
        if used_tool and used_tool not in tried:
            st["tried_tools"] = tried + [used_tool]

        if attempts < max_attempts:
            # 给前端一个中间态说明（可选）
            last_err = (st.get("failed_attempts") or [{}])[-1]
            note = f"第 {attempts}/{max_attempts} 次尝试失败：{last_err.get('error','unknown')}（工具：{used_tool or 'none'}）。将换用其他工具重试。"
            yield Event(author=self.name, content=types.Content(parts=[types.Part(text=note)]))
            # 不 escalate，让 LoopAgent 进入下一轮
            return

        # 彻底失败：汇总轨迹
        logs = st.get("failed_attempts") or []
        lines = ["❌ 未能解决问题。", f"问题：{question}", f"共尝试 {attempts} 次。", "失败轨迹："]
        for i, it in enumerate(logs, 1):
            lines.append(f"{i}. 工具={it.get('tool')}，错误/输出={it.get('error')}")
        yield Event(author=self.name, content=types.Content(parts=[types.Part(text="\n".join(lines))]))
        yield Event(author=self.name, actions=EventActions(escalate=True))
        return


# ========= Loop 入口 =========
def _loop_before(callback_context: CallbackContext):
    # 每次 run 前清理“当前轮”的候选集合
    st = callback_context.state
    st["tool_candidates"] = []
    st["attempts"] = 0
    st["max_attempts"] = DEFAULT_MAX_ATTEMPTS
    st["tried_tools"] = []
    st["failed_attempts"] = []
    return None

solver_loop_agent = LoopAgent(
    name="ProblemSolverLoopAgent",
    max_iterations=200,  # 安全上限（真正终止由 Controller 控制）
    sub_agents=[
        AnalyzerAgent(),   # 依据问题 & 已失败历史，选出下一批候选工具
        ExecutorAgent(),   # 用“动态 Toolset”让 LLM 只调用这一批工具求解
        ControllerAgent(), # 根据结果：成功则收敛输出与缓存映射，失败则进入下一轮
    ],
    before_agent_callback=_loop_before
)
