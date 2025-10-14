#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/14 11:08
# @File  : markdown_utils.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  : 网页内容保存成markdown

import asyncio
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from crawl4ai import AsyncWebCrawler

async def save_markdown(url, save_path):
    """
    url: "https://www.nbcnews.com/business"
    """
    content = ""
    config = BrowserConfig(headless=False)
    async with AsyncWebCrawler(config=config) as crawler:
        result = await crawler.arun(
            url=url,
        )
        content = result.markdown
    if content:
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"保存成功：{save_path}")
        return True
    else:
        print("保存失败")
        return False
