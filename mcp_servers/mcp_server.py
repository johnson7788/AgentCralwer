#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/13 10:42
# @File  : mcp_href_pdf.py
# @Author: johnson
# @Contact: github: johnson7788
# @Desc  : 三种不同策略的 PDF 下载工具 (基于 FastMCP)

import datetime
import os
import asyncio
from fastmcp import FastMCP, Context
from mcp.types import CallToolResult
from common.pdf_utils import get_run_configs, download_with_crawler, fetch_pdfs_from_page
from common.markdown_utils import save_markdown

mcp = FastMCP("PDFDownloader")


# ======================================================
# 1️⃣ HREF 链接抓取下载：查找页面中所有以 .pdf 结尾的 href 并下载
# ======================================================
@mcp.tool()
async def download_pdf_via_href_links(url: str, project_name: str) -> CallToolResult:
    """
    从网页中提取所有直接以 `.pdf` 结尾的超链接 (href)，并通过浏览器自动触发下载。
    默认使用这个即可下做所有pdf文件了，优先使用。
    📘 特点:
    - 使用 crawl4ai 的异步浏览器；
    - 扫描页面中 `a[href$='.pdf']` 等链接；
    - 适用于直接可访问的 PDF 文件链接；
    - 不解析 <a type="application/pdf"> 类型。

    :param url: 目标网页 URL
    :param project_name: 下载项目名称 (用于保存目录)
    :return: 下载结果
    """
    save_dir = os.path.join("./downloaded_pdfs", project_name)
    # meta_in = ctx.request_meta or {}
    run_config = get_run_configs("href_pdf")
    status = await download_with_crawler(url, save_dir, run_config)
    result = f"✅ [HREF下载成功]: {url}" if status else f"❌ [HREF下载失败]: {url}"

    return CallToolResult(
        content=[{"type": "text", "text": result}],
        meta={
            # "user_id": meta_in.get("user_id", "unknown"),
            "server_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        },
    )


# ======================================================
# 2️⃣ MIME类型识别下载：检测 <a type="application/pdf"> 标签
# ======================================================
@mcp.tool()
async def download_pdf_via_mime_type(url: str, project_name: str) -> CallToolResult:
    """
    通过识别页面中声明 MIME 类型为 `application/pdf` 的链接下载 PDF。

    📘 特点:
    - 同样基于 crawl4ai；
    - 匹配 <a type="application/pdf"> 标签；
    - 适用于网站使用 MIME 类型而非后缀标识 PDF 的情况；
    - 兼容 href 含 .pdf 的常规链接。

    :param url: 目标网页 URL
    :param project_name: 下载项目名称
    :return: 下载结果
    """
    save_dir = os.path.join("./downloaded_pdfs", project_name)
    # meta_in = ctx.request_meta or {}
    run_config = get_run_configs("application_pdf")
    status = await download_with_crawler(url, save_dir, run_config)
    result = f"✅ [MIME下载成功]: {url}" if status else f"❌ [MIME下载失败]: {url}"

    return CallToolResult(
        content=[{"type": "text", "text": result}],
        meta={
            # "user_id": meta_in.get("user_id", "unknown"),
            "server_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        },
    )


# ======================================================
# 3️⃣ 页面正则提取下载：解析 HTML 并用 aiohttp 下载 PDF
# ======================================================
@mcp.tool()
async def download_pdf_via_html_parse(url: str, project_name: str) -> CallToolResult:
    """
    直接抓取网页 HTML 内容，通过正则表达式提取所有 PDF 链接并下载。

    📘 特点:
    - 不依赖 JS 执行；
    - 使用 aiohttp 逐个请求 PDF；
    - 适合静态页面；
    - 无法处理异步加载的 PDF 链接。

    :param url: 目标网页 URL
    :param project_name: 下载项目名称
    :return: 下载结果
    """
    save_dir = os.path.join("./downloaded_pdfs", project_name)
    # meta_in = ctx.request_meta or {}
    status = await fetch_pdfs_from_page(url, save_dir)
    result = f"✅ [HTML解析下载成功]: {url}" if status else f"❌ [HTML解析下载失败]: {url}"

    return CallToolResult(
        content=[{"type": "text", "text": result}],
        meta={
            # "user_id": meta_in.get("user_id", "unknown"),
            "server_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        },
    )


# ======================================================
# 4️⃣ 网页内容保存为 Markdown
# ======================================================
@mcp.tool()
async def save_webpage_as_markdown(url: str, project_name: str) -> CallToolResult:
    """
    抓取指定 URL 的网页内容，并将其保存为 Markdown 格式的文件。

    📘 特点:
    - 使用 crawl4ai 提取网页主要内容并转换为 Markdown；
    - 保存到 `downloaded_markdowns/{project_name}` 目录下；
    - 文件名基于当前时间戳生成。

    :param url: 目标网页 URL
    :param project_name: 项目名称 (用于保存目录)
    :return: 保存结果
    """
    save_dir = os.path.join("./downloaded_markdowns", project_name)
    os.makedirs(save_dir, exist_ok=True)

    # Generate a unique filename using a timestamp
    filename = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    save_path = os.path.join(save_dir, filename)

    status = await save_markdown(url, save_path)
    result = f"✅ [Markdown 保存成功]: {save_path}" if status else f"❌ [Markdown 保存失败]: {url}"

    return CallToolResult(
        content=[{"type": "text", "text": result}],
        meta={
            "server_timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        },
    )


if __name__ == "__main__":
    mcp.run(transport="sse")
