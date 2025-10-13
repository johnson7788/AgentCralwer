#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/13 10:41
# @File  : pdf_utils.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  : 下载pdf文件的不同的方式

import os
import re
import asyncio
import aiohttp
import async_timeout
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from crawl4ai import AsyncWebCrawler

LIMIT_NUM = 2

def get_run_configs(name):
    if name == "href_pdf":
        return CrawlerRunConfig(
            js_code="""
                async function downloadFile(url, filename) {
                    const response = await fetch(url);
                    const blob = await response.blob();
                    const a = document.createElement('a');
                    a.href = window.URL.createObjectURL(blob);
                    a.download = filename || url.split('/').pop();
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                }
                const links = Array.from(document.querySelectorAll(
                    'a[href$=".pdf"], a[href$=".PDF"], a[href*=".pdf?"], a[href*=".PDF?"]'
                ));
                for (const link of links.slice(0, 2)) {
                    await downloadFile(link.href);
                    await new Promise(r => setTimeout(r, 3000));
                }
            """,
            wait_for="css:a[href*='.pdf']",
            delay_before_return_html=60,
            wait_for_timeout=30000,
        )
    elif name == "application_pdf":
        return CrawlerRunConfig(
            js_code="""
                async function downloadFile(url, filename) {
                    const response = await fetch(url);
                    const blob = await response.blob();
                    const a = document.createElement('a');
                    a.href = window.URL.createObjectURL(blob);
                    a.download = filename || url.split('/').pop() || "download.pdf";
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                }
                const links = Array.from(document.querySelectorAll(
                    'a[href$=".pdf"], a[href$=".PDF"], a[href*=".pdf?"], a[href*=".PDF?"], a[type="application/pdf"]'
                ));
                for (const link of links.slice(0, 2)) {
                    const filename = link.getAttribute('title') || 'file.pdf';
                    await downloadFile(link.href, filename);
                    await new Promise(r => setTimeout(r, 3000));
                }
            """,
            wait_for="css:a[href*='.pdf'], a[type='application/pdf']",
            delay_before_return_html=60,
            wait_for_timeout=30000,
        )

async def download_with_crawler(url, download_path, run_config):
    os.makedirs(download_path, exist_ok=True)
    config = BrowserConfig(
        accept_downloads=True,
        headless=False,
        downloads_path=download_path,
        enable_stealth=True,
    )
    async with AsyncWebCrawler(config=config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        return bool(result.downloaded_files)

async def fetch_pdfs_from_page(url, download_dir):
    os.makedirs(download_dir, exist_ok=True)
    config = BrowserConfig(headless=False)
    run_config = CrawlerRunConfig(wait_for="css:a[href*='.pdf']")
    statuses = []
    async with AsyncWebCrawler(config=config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        html = result.html
        pdf_urls = re.findall(r'href="(https?://[^"]+\.pdf)"', html, re.IGNORECASE)
        pdf_urls = list(set(pdf_urls))
        for link in pdf_urls[:LIMIT_NUM]:
            filename = os.path.basename(link)
            path = os.path.join(download_dir, filename)
            async with aiohttp.ClientSession() as session:
                try:
                    async with async_timeout.timeout(60):
                        async with session.get(link, ssl=False) as resp:
                            if resp.status == 200:
                                with open(path, "wb") as f:
                                    f.write(await resp.read())
                                statuses.append(True)
                except Exception:
                    statuses.append(False)
    return all(statuses)
