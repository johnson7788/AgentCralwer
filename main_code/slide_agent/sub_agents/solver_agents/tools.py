#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/14
# @Desc  : 编程求解工具：search_solver + run_code

import os
import time
import json
import shlex
import httpx
import asyncio
import tempfile
import subprocess
from typing import List, Dict, Any, Optional
from datetime import datetime

from google.adk.tools import ToolContext
import dotenv

dotenv.load_dotenv()

# —— 通用搜索（沿用现有 SEARCH_TOOL_API 适配） ——
def _search_api(keyword: str, limit: int = 8, search_engine: str = "zhipu") -> Dict[str, Any]:
    """
    兼容已有的搜索后端：
    - 默认使用 'zhipu'（通用 Web/代码问答混合检索）
    - 若你本地有专门的代码检索服务，可把 search_engine 换成 'code' 或自行修改后端路径
    返回格式需包含 'articles': [{'title','url','snippet','publish_time','file_id'}...]
    """
    api = os.environ.get("SEARCH_TOOL_API")
    if not api:
        return {"articles": []}

    url = f"{api}/api/search_keyword"
    payload = {"keyword": keyword, "limit": limit, "search_engine": search_engine}
    try:
        resp = httpx.post(url, json=payload, headers={'content-type': 'application/json'}, timeout=None, trust_env=False)
        resp.raise_for_status()
        return resp.json() or {"articles": []}
    except Exception:
        return {"articles": []}


async def search_solver(
    question: str,
    tool_context: ToolContext,
    limit: int = 8
):
    """
    根据问题检索可能的解决方案（相似问题、解决思路、可复用代码）。
    - 写入 tool_context.state['references'] 以便最终统一渲染
    - 返回字符串列表，供 LLM 总结/吸收
    """
    agent_name = tool_context.agent_name
    print(f"[{agent_name}] 调用工具：search_solver: {question}")

    references = tool_context.state.get("references", {})
    start_time = time.time()

    # 尝试多种查询角度（原样 + 添加关键词）
    queries = [question]
    if len(question) < 200:
        queries += [question + " stackoverflow", question + " fix", question + " code example"]

    results_all = []
    for q in queries[:3]:
        res = _search_api(q, limit=limit, search_engine="zhipu")
        arts = res.get("articles") or []
        results_all.extend(arts)

    took = time.time() - start_time
    print(f"[search_solver] got {len(results_all)} items, took={took:.2f}s")

    items = []
    for art in results_all[:limit]:
        file_id = art.get("file_id") or f"web::{art.get('url')}"
        if file_id not in references:
            idx_val = len(references) + 1
            url = art.get("url") or ""
            title = art.get("title") or url[:60]
            snippet = art.get("snippet") or ""
            references[file_id] = {
                "idx_val": idx_val,
                "file_id": file_id,
                "title": title,
                "publish_time": art.get("publish_time") or "",
                "snippet": snippet,
                "url": url
            }
        ref = references[file_id]
        items.append(f"{ref['idx_val']}. {ref['title']}\nURL: {ref['url']}\n摘要: {ref['snippet']}\n")

    tool_context.state["references"] = references
    return items if items else ["未检索到可用参考，请调整关键词（可尝试添加报错信息/函数名/语言版本）。"]


# ===================== 运行器（run_code） =====================

def _ensure_workspace() -> str:
    root = os.path.join(os.getcwd(), "project_runs")
    os.makedirs(root, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(root, ts)
    os.makedirs(path, exist_ok=True)
    return path


def _choose_filename(language: str, filename: Optional[str]) -> str:
    if filename:
        return filename
    ext_map = {
        "python": ".py",
        "bash": ".sh",
        "sh": ".sh",
        "node": ".js",
        "javascript": ".js",
        "js": ".js",
        "go": ".go",
        "c": ".c",
        "cpp": ".cpp",
        "java": ".java",
        "rust": ".rs",
    }
    ext = ext_map.get(language.lower(), ".txt")
    return f"solution{ext}"


def _build_command(language: str, filepath: str, args: List[str]) -> List[str]:
    lang = language.lower()
    if lang in ("python", "py"):
        return ["python", filepath, *args]
    if lang in ("bash", "sh"):
        return ["bash", filepath, *args]
    if lang in ("javascript", "js", "node"):
        return ["node", filepath, *args]
    # 可扩展：c/cpp/go/java/rust（视运行环境是否安装编译器）
    # 未支持的语言统一以文本方式返回错误
    return ["__unsupported__", lang]

def _truncate(s: str, max_len: int = 20000) -> str:
    if s is None:
        return ""
    return s if len(s) <= max_len else (s[:max_len] + "\n...[truncated]")

async def run_code(
    code: str,
    tool_context: ToolContext,
    language: str = "python",
    filename: str = "",
    args: List[str] = None,
    stdin: str = "",
    timeout: int = 15,
    save: bool = True
):
    """
    将代码保存并执行，返回运行结果（stdout/stderr/exit_code）。
    - language: 目前内置支持 python/bash/node；其它语言将返回 unsupported 提示。
    - args: 传给进程的 argv
    - stdin: 标准输入
    - timeout: 终止超时（秒）
    """
    agent_name = tool_context.agent_name
    print(f"[{agent_name}] 调用工具：run_code (lang={language}, filename={filename or '(auto)'}, timeout={timeout}s)")

    args = args or []
    timeout = max(1, int(timeout))

    # 工作空间
    workdir = _ensure_workspace()
    fname = _choose_filename(language, filename)
    filepath = os.path.join(workdir, fname)

    # 写文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(code)

    # 执行
    cmd = _build_command(language, filepath, args)
    if cmd[0] == "__unsupported__":
        result = {
            "ok": False,
            "language": language,
            "filename": fname,
            "cmd": "",
            "exit_code": -1,
            "stdout": "",
            "stderr": f"language '{cmd[1]}' not supported by run_code",
            "time_ms": 0,
            "saved_path": filepath
        }
        return result

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=stdin.encode("utf-8") if stdin else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            timeout=timeout,
            check=False
        )
        took_ms = int((time.time() - start) * 1000)
        result = {
            "ok": proc.returncode == 0,
            "language": language,
            "filename": fname,
            "cmd": " ".join(shlex.quote(x) for x in cmd),
            "exit_code": proc.returncode,
            "stdout": _truncate(proc.stdout.decode("utf-8", errors="ignore")),
            "stderr": _truncate(proc.stderr.decode("utf-8", errors="ignore")),
            "time_ms": took_ms,
            "saved_path": filepath
        }
        # 将最近一次运行结果写回状态，便于 Verifier 访问
        tool_context.state["last_run"] = result
        return result
    except subprocess.TimeoutExpired:
        result = {
            "ok": False,
            "language": language,
            "filename": fname,
            "cmd": " ".join(shlex.quote(x) for x in cmd),
            "exit_code": -9,
            "stdout": "",
            "stderr": f"Process timed out after {timeout}s",
            "time_ms": int((time.time() - start) * 1000),
            "saved_path": filepath
        }
        tool_context.state["last_run"] = result
        return result
