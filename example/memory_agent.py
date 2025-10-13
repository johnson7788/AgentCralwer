#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/11 17:26
# @File  : memory_agent.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  :

"""
目标：
- 给定一个用户问题，按优先级尝试不同工具；
- 一旦某工具解决问题，立即停止并把“问题→工具”的映射缓存到本地 sqlite；
- 下次遇到同类（或相同）问题，绕过试错，直接用命中过的工具；
- 结合 OpenAI Agents SDK 的 Agent/Tools/Session（会话记忆）。

依赖版本：
    pip install openai-agents-python

文档要点（对应注释中的编号）：
(1) 定义 Agent、模型、tools：Agents 基础配置与函数工具的定义与自动入参模式生成
    https://openai.github.io/openai-agents-python/agents/
    https://openai.github.io/openai-agents-python/tools/
(2) 使用 Runner.run 与 Session 让代理记住历史上下文（非我们自建的“工具命中缓存”）
    https://openai.github.io/openai-agents-python/sessions/
(3) 如果想动态控制工具可用性，可用 is_enabled 或每次只给 Agent 暴露一个工具
    https://openai.github.io/openai-agents-python/ref/agent/  (get_all_tools / 条件启用)

本示例选择更可控的做法：外层“编排器”循环多次调用 Runner.run，
每次仅暴露一个候选工具给 Agent。一旦判断“SOLVED: true”即记忆并停止。
"""

import dotenv
import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Callable, List, Optional

from agents import Agent, Runner, SQLiteSession, function_tool, FunctionTool
dotenv.load_dotenv()

# =========================
# 1) 工具
# =========================

# 每次尝试多少个工具去解决问题，不要把所有工具都放进去，要一批次一批次放入，防止上下文爆炸
TRY_TOOL_NUMBER = 2

@function_tool
def calc_expression(expr: str) -> str:
    """
    计算基础算术表达式（只支持 + - * / 和括号）。
    Args:
        expr: 要计算的算式，如 "12*(3+4)/2"
    """
    try:
        # 安全起见，仅 eval 限制在空命名空间
        val = eval(expr, {"__builtins__": {}}, {})
        return json.dumps({"tool": "calc_expression", "ok": True, "result": val}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"tool": "calc_expression", "ok": False, "error": str(e)}, ensure_ascii=False)


@function_tool
def unit_convert(value: float, from_unit: str, to_unit: str) -> str:
    """
    简单单位换算（目前只做 'km' <-> 'miles'）
    Args:
        value: 数值
        from_unit: 源单位: km 或 miles
        to_unit: 目标单位: km 或 miles
    """
    try:
        if from_unit == to_unit:
            res = value
        elif from_unit == "km" and to_unit == "miles":
            res = value * 0.621371
        elif from_unit == "miles" and to_unit == "km":
            res = value / 0.621371
        else:
            return json.dumps({"tool": "unit_convert", "ok": False, "error": "unsupported units"}, ensure_ascii=False)
        return json.dumps({"tool": "unit_convert", "ok": True, "result": res}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"tool": "unit_convert", "ok": False, "error": str(e)}, ensure_ascii=False)


@function_tool
def lookup_fact(topic: str) -> str:
    """
    伪“知识库查询”（演示用），真实项目可改为外部 HTTP / DB 检索。
    Args:
        topic: 查询主题
    """
    FAKE_DB = {
        "python": "Python 是一种解释型、通用型编程语言。",
        "openai": "OpenAI 提供了 Responses API 与 Agents SDK 等开发者工具。"
    }
    text = FAKE_DB.get(topic.lower())
    if text:
        return json.dumps({"tool": "lookup_fact", "ok": True, "result": text}, ensure_ascii=False)
    return json.dumps({"tool": "lookup_fact", "ok": False, "error": "not found"}, ensure_ascii=False)


ALL_TOOLS: List[FunctionTool] = [calc_expression, unit_convert, lookup_fact]

# =========================
# 2) “工具命中缓存” - 本地 sqlite
# =========================

class ToolCache:
    def __init__(self, db_path: str = "tool_cache.db"):
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
        # 你可替换为更强的语义签名；此处简化为归一化+hash
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


# =========================
# 3) 策略：根据输入给出“工具优先级”序列
# =========================

def rank_tools_for_query(q: str) -> List[FunctionTool]:
    """
    非 LLM 的快速启发式排序（可换成统计、Embedding 检索或 LLM 评估）。
    """
    ql = q.lower()
    scores = []
    for t in ALL_TOOLS:
        name = t.name  # function_tool 装饰后会有稳定的 name（参考 Tools 文档）
        score = 0
        if "km" in ql or "mile" in ql or "转换" in ql or "换算" in ql:
            score += 5 if name == "unit_convert" else 0
        if any(ch in ql for ch in "+-*/()") or "计算" in ql:
            score += 5 if name == "calc_expression" else 0
        if "是什么" in ql or "介绍" in ql or "定义" in ql:
            score += 5 if name == "lookup_fact" else 0
        scores.append((score, t))

    # 主观权重后，按默认顺序微调（越靠前越常用可加一点）
    base_order = {calc_expression.name: 2, unit_convert.name: 1, lookup_fact.name: 0}
    scored = sorted(scores, key=lambda x: (x[0], base_order.get(x[1].name, 0)), reverse=True)
    return [t for _, t in scored]


# =========================
# 4) Orchestrator：按顺序批量尝试工具 + 命中缓存后，直接使用对应工具
# =========================

@dataclass
class SolveResult:
    solved: bool
    tool_name: Optional[str]
    final_text: str
    raw_final_output: str


class ToolOrchestrator:
    """
    外层控制器：
      - 优先查缓存（命中则只暴露该工具给 Agent）
      - 未命中：按 rank_tools_for_query 顺序逐个尝试
      - “成功”判定：让 Agent 严格输出形如 `SOLVED:true|false JSON: {...}`
                     （结构化输出在 Agents 文档中也有推荐做法，可升级为 pydantic output_type）
    """

    def __init__(self, session: SQLiteSession, tool_cache: ToolCache):
        self.session = session
        self.cache = tool_cache

    @staticmethod
    def _build_agent_with_tools(tools: List[FunctionTool]) -> Agent:
        # 关键：每次只暴露一个（或少数）工具给 Agent，模型只能调用它
        # Agents 基础配置（参见 Agents 文档“Basic configuration”）
        instructions = (
            "你是问题求解助手。你必须尽可能调用可用的工具。\n"
            "完成后，请用严格格式回复：\n"
            "SOLVED:true|false JSON:{\"tool\":\"<工具名或none>\",\"answer\":\"<最终答案或错误原因>\"}\n"
            "（注意：一定要包含 SOLVED: 前缀与 JSON: 前缀，JSON 必须可解析）"
        )
        return Agent(
            name="Solver",
            instructions=instructions,
            # 不显式指定 model 也可运行；如需手动：model=\"gpt-4o-mini\" 等
            tools=tools,
        )

    @staticmethod
    def _parse_solved(final_output: str) -> SolveResult:
        text = final_output.strip()
        # 期望格式：以 "SOLVED:true JSON:{...}" 或 "SOLVED:false JSON:{...}"
        solved_flag = False
        tool_name = None
        payload = {}
        if "SOLVED:true" in text:
            solved_flag = True
        elif "SOLVED:false" in text:
            solved_flag = False
        # 抓取 JSON 部分
        try:
            idx = text.find("JSON:")
            if idx >= 0:
                j = text[idx + 5 :].strip()
                payload = json.loads(j)
                tool_name = payload.get("tool")
        except Exception:
            pass
        answer = payload.get("answer", text)
        return SolveResult(solved=solved_flag, tool_name=tool_name, final_text=answer, raw_final_output=text)

    async def solve(self, user_query: str) -> SolveResult:
        # 1) 先查缓存（命中：只给该工具）
        cached_tool = self.cache.get(user_query)
        attempts: List[FunctionTool]
        if cached_tool:
            tool = next((t for t in ALL_TOOLS if t.name == cached_tool), None)
            attempts = [tool] if tool else rank_tools_for_query(user_query)
        else:
            attempts = rank_tools_for_query(user_query)

        # ✅ 2) 按 TRY_TOOL_NUMBER 分批尝试（每次 N 个工具）
        for i in range(0, len(attempts), TRY_TOOL_NUMBER):
            batch = attempts[i : i + TRY_TOOL_NUMBER]
            agent = self._build_agent_with_tools(batch)
            result = await Runner.run(
                agent,
                input=user_query,
                session=self.session,  # 与会话记忆集成（Agents SDK Session；非我们自建缓存）
            )
            parsed = self._parse_solved(result.final_output)
            if parsed.solved:
                # 记忆：问题→工具 映射
                if parsed.tool_name:
                    self.cache.put(user_query, parsed.tool_name)
                return parsed

        # 3) 全部失败：给出失败信息
        return SolveResult(False, None, "所有工具均未解决该问题。", "SOLVED:false JSON:{\"tool\":\"none\",\"answer\":\"no tool solved\"}")


# =========================
# 5) 演示主程序
# =========================

async def demo():
    # (2) Sessions: SQLiteSession，使多轮对话自动带上下文（Agents SDK 内置）
    #    https://openai.github.io/openai-agents-python/sessions/
    session = SQLiteSession("demo_user", "conversations.db")

    cache = ToolCache()
    orchestrator = ToolOrchestrator(session, cache)

    # --- 示例 1：算式，优先命中 calc_expression
    q1 = "请计算 (12+8)*3/2"
    r1 = await orchestrator.solve(q1)
    print("[Q1]", q1)
    print("[A1]", r1.final_text)
    print("[raw]", r1.raw_final_output)
    print("-" * 60)

    # --- 示例 2：单位换算，会命中 unit_convert
    q2 = "把 10 miles 换算成 km"
    r2 = await orchestrator.solve(q2)
    print("[Q2]", q2)
    print("[A2]", r2.final_text)
    print("[raw]", r2.raw_final_output)
    print("-" * 60)

    # --- 示例 3：知识查询，会命中 lookup_fact
    q3 = "OpenAI 是什么？简要介绍一下"
    r3 = await orchestrator.solve(q3)
    print("[Q3]", q3)
    print("[A3]", r3.final_text)
    print("[raw]", r3.raw_final_output)
    print("-" * 60)

    # --- 示例 4：重复问题（测试缓存直达）
    q4 = "请计算 (12+8)*3/2"  # 与 q1 相同，应该直接使用缓存中的 calc_expression
    r4 = await orchestrator.solve(q4)
    print("[Q4 - 缓存复用]", q4)
    print("[A4]", r4.final_text)
    print("[raw]", r4.raw_final_output)
    print("-" * 60)


if __name__ == "__main__":
    asyncio.run(demo())
