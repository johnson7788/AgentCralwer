#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/6/20 10:02
# @File  : tools.py.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  : 搜索图片，用于PPT的配图

import re
import os
import time
import httpx
from datetime import datetime
import random
import dotenv
from google.adk.tools import FunctionTool
import hashlib
from google.adk.tools import ToolContext
import requests
import json
from typing import List, Dict, Any
dotenv.load_dotenv()

# ========= 定义基础工具（示例，与 OpenAI Agent 示例保持一致） =========
def calc_expression(expr: str) -> dict:
    """
    计算基础算术表达式（支持 + - * / 和括号）。
    Args:
        expr: 算式，如 "12*(3+4)/2"
    Returns:
        dict: {'status':'success','result':<number>} 或 {'status':'error','error_message':<msg>}
    """
    try:
        val = eval(expr, {"__builtins__": {}}, {})
        return {"status": "success", "result": val}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

def unit_convert(value: float, from_unit: str, to_unit: str) -> dict:
    """
    简单单位换算（当前支持 'km' <-> 'miles'）。
    Args:
        value: 数值
        from_unit: 'km' 或 'miles'
        to_unit: 'km' 或 'miles'
    Returns:
        dict: {'status':'success','result':<number>} 或 {'status':'error','error_message':<msg>}
    """
    try:
        if from_unit == to_unit:
            res = value
        elif from_unit == "km" and to_unit == "miles":
            res = value * 0.621371
        elif from_unit == "miles" and to_unit == "km":
            res = value / 0.621371
        else:
            return {"status": "error", "error_message": "unsupported units"}
        return {"status": "success", "result": res}
    except Exception as e:
        return {"status": "error", "error_message": str(e)}

def lookup_fact(topic: str) -> dict:
    """
    伪“知识库查询”（演示用），实际可替换为外部 HTTP/DB 检索。
    Args:
        topic: 主题
    Returns:
        dict: {'status':'success','result':<text>} 或 {'status':'error','error_message':<msg>}
    """
    FAKE_DB = {
        "python": "Python 是一种解释型、通用型编程语言。",
        "openai": "OpenAI 提供了 Responses API 与 Agents SDK 等开发者工具。"
    }
    text = FAKE_DB.get(topic.lower())
    if text:
        return {"status": "success", "result": text}
    return {"status": "error", "error_message": "not found"}

# 包装为 ADK FunctionTool（供 LLM 直接调用）  —— ADK 建议这样定义工具函数。:contentReference[oaicite:3]{index=3}
TOOL_CALC = FunctionTool(func=calc_expression)
TOOL_UNIT = FunctionTool(func=unit_convert)
TOOL_FACT = FunctionTool(func=lookup_fact)

# 工具注册表
ALL_TOOLS = {
    "calc_expression": TOOL_CALC,
    "unit_convert": TOOL_UNIT,
    "lookup_fact": TOOL_FACT,
}

if __name__ == '__main__':
    pass
