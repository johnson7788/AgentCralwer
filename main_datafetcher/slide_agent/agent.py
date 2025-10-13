# 文件: agent.py
from google.adk.agents.sequential_agent import SequentialAgent  # 这里保留以兼容原结构（未使用）
from google.adk.agents.callback_context import CallbackContext
from google.genai import types
from dotenv import load_dotenv
import os

# 问题求解循环 Agent（新的）
from .sub_agents.innovation_agents.agent import solver_loop_agent, get_cache, DEFAULT_MAX_ATTEMPTS

# 加载环境变量
load_dotenv('.env')

def _get_text_from_context(callback_context: CallbackContext) -> str | None:
    """拿到用户刚发来的纯文本。"""
    user_content = getattr(callback_context, "user_content", None)
    if user_content and getattr(user_content, "parts", None):
        for part in user_content.parts:
            text = getattr(part, "text", None)
            if text:
                return text
    return None

def before_agent_callback(callback_context: CallbackContext):
    """
    初始化：抽取问题文本、读取 metadata 配置、接入本地工具命中缓存（SQLite）。
    约定 state 字段：
      - question: 本次用户问题
      - attempts/max_attempts: 当前/最大尝试次数
      - tried_tools: 已尝试的工具名列表
      - failed_attempts: [{tool, error, raw}] 失败轨迹
      - tool_candidates: 分析 Agent 给出的候选工具名（当前轮）
      - cached_tool: 命中缓存的工具名（若有则优先仅用该工具）
      - try_tool_number: 每一轮最多暴露的工具数量（默认 2）
    """
    st = callback_context.state
    md = st.get("metadata") or {}

    text = _get_text_from_context(callback_context)
    if not text:
        return types.Content(
            role="model",
            parts=[types.Part(text="请输入要解决的问题（例如：'把 10 miles 换算成 km' 或 '请计算 (12+8)*3/2'）。")]
        )

    # ---- 基础 state ----
    st["question"] = text.strip()
    st["attempts"] = 0
    st["max_attempts"] = int(md.get("max_attempts") or os.getenv("SOLVER_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS))
    st["tried_tools"] = []
    st["failed_attempts"] = []
    st["tool_candidates"] = []
    st["solved"] = False
    st["final_answer"] = None
    st["used_tool"] = None

    # ---- 每轮暴露多少工具给 LLM（避免上下文过大）----
    st["try_tool_number"] = int(md.get("try_tool_number") or os.getenv("SOLVER_TRY_TOOL_NUMBER", 2))

    # ---- 工具命中缓存：问题 -> 工具名 ----
    cache = get_cache()
    st["cached_tool"] = cache.get(st["question"])

    return None


root_agent = SequentialAgent(
    name="ProblemSolverSystemAgent",
    description="问题求解系统：根据用户问题动态选择工具并求解；成功则记录工具映射，失败则更换工具重试。",
    # 这里直接接入循环求解 Agent（内部自己做循环控制）
    sub_agents=[solver_loop_agent],
    before_agent_callback=before_agent_callback
)
