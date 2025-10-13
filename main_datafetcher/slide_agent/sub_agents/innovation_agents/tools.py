#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/6/20 10:02
# @File  : tools.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  : 动态加载 MCP Server 工具（取代写死定义）

import asyncio
import json
import dotenv
from typing import Dict, Any
from google.adk.tools import FunctionTool
from .mcp_client import get_mcp_tools, call_mcp_tool_sync

dotenv.load_dotenv()

# ========== 动态加载 MCP 工具 ==========

def load_mcp_tools(server_url: str) -> Dict[str, FunctionTool]:
    """
    动态从 MCP server 拉取所有工具，并包装为 FunctionTool。
    """
    async def _load():
        tools = await get_mcp_tools(server_url)
        return tools

    try:
        tools_meta = asyncio.run(_load())
    except Exception as e:
        print(f"❌ Failed to load tools from MCP server {server_url}: {e}")
        return {}

    tool_dict = {}

    for tool in tools_meta:
        name = tool.get("name")
        desc = tool.get("description", "No description provided")
        params = tool.get("parameters", {})

        def make_tool_func(tool_name):
            def _func(**kwargs):
                """动态代理 MCP 工具调用"""
                result = call_mcp_tool_sync(server_url, tool_name, kwargs)
                return result
            return _func

        func = make_tool_func(name)
        wrapped = FunctionTool(
            func=func,
            name=name,
            description=desc,
            parameters=params
        )
        tool_dict[name] = wrapped

    print(f"✅ Loaded {len(tool_dict)} tools dynamically from {server_url}")
    return tool_dict


# ========== 入口配置 ==========

def get_all_tools() -> Dict[str, FunctionTool]:
    """
    从 ./mcp_config.json 中加载配置，然后动态拉取工具。
    """
    try:
        with open("./mcp_config.json", "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print("❌ Cannot find mcp_config.json. Please make sure it exists.")
        return {}

    mcp_servers = config.get("mcpServers", {})
    all_tools = {}

    for name, info in mcp_servers.items():
        if info.get("disabled"):
            continue
        url = info.get("url")
        if not url:
            continue
        tools = load_mcp_tools(url)
        all_tools.update(tools)

    return all_tools


# ========== 导出全局注册表 ==========

ALL_TOOLS = get_all_tools()

if __name__ == "__main__":
    # 仅测试输出
    for k in ALL_TOOLS:
        print(f"Tool: {k}")
