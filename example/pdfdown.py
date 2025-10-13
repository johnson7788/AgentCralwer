#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/11 09:34
# @File  : pdfdown.py.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  :
import re
import logging
import asyncio
import aiohttp
import async_timeout
from tqdm import tqdm
import os
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
from crawl4ai import AsyncWebCrawler

web_sites = [
  {
    "name": "华润置地",
    "url": "https://crland-umb.azurewebsites.net/zh-cn/investors/financial-results-and-presentations/"
  },
  {
    "name": "信达生物",
    "url": "https://investor.innoventbio.com/cn/investors/webcasts-and-presentations/"
  },
  {
    "name": "住友商事",
    "url": "https://www.sumitomocorp.com/ja/jp/ir/report"
  },
  {
    "name": "亿航智能",
    "url": "https://ir.ehang.com/financial-information/quarterly-results"
  },
  {
    "name": "碧桂园",
    "url": "https://www.countrygarden.com.cn/investor/presentation"
  },
  {
    "name": "菊池制作所",
    "url": "https://www.kikuchiseisakusho.co.jp/ir/irnews.html"
  },
  {
    "name": "英伟达",
    "url": "https://investor.nvidia.com/financial-info/financial-reports/default.aspx"
  },
  {
    "name": "瑞声科技",
    "url": "https://www.aactechnologies.com/portfolio/investorsRelations"
  },
  {
    "name": "特斯拉",
    "url": "https://ir.tesla.com/#quarterly-disclosure"
  }
]
# 每页下载2个pdf文件，用于测试
LIMIT_NUM = 2

def get_run_configs(name):
    if name == "href_pdf":
        run_config = CrawlerRunConfig(
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

                const links = Array.from(
                  document.querySelectorAll(
                    'a[href$=".pdf"], a[href$=".PDF"], a[href*=".pdf?"], a[href*=".PDF?"]'
                  )
                );
                const firstTwo = links.slice(0, 2);
                for (const link of firstTwo) {
                    const url = link.href;
                    console.log("Downloading:", url);
                    await downloadFile(url);
                    await new Promise(r => setTimeout(r, 3000)); // 等待下载触发完成
                }
            """,
            # 等待：确保页面上至少出现过一个 PDF 链接（而不是等固定秒数）
            wait_for="css:a[href*='.pdf']",
            # 给下载动作一点“收尾”时间（秒），不会触发 .strip()
            delay_before_return_html=60,
            # 可选：扩大等待上限（毫秒）
            wait_for_timeout=30000,
        )
    elif name == "application_pdf":
        run_config = CrawlerRunConfig(
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
    
                const links = Array.from(
                  document.querySelectorAll(
                    'a[href$=".pdf"], a[href$=".PDF"], a[href*=".pdf?"], a[href*=".PDF?"], a[type="application/pdf"]'
                  )
                );
                const firstTwo = links.slice(0, 2);
                for (const link of firstTwo) {
                    const url = link.href;
                    const filename = link.getAttribute('title') || 'file.pdf';
                    console.log("Downloading:", url, filename);
                    await downloadFile(url, filename);
                    await new Promise(r => setTimeout(r, 3000));
                }
            """,
            wait_for="css:a[href*='.pdf'], a[type='application/pdf']",
            delay_before_return_html=60,
            wait_for_timeout=30000,
        )
    return run_config

async def download_all_pdfs_from_url(run_config, url: str, download_path: str):
    os.makedirs(download_path, exist_ok=True)
    # 配置允许下载 & 下载目录
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/118.0.0.0 Safari/537.36",
        "Referer": url,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    config = BrowserConfig(
        accept_downloads=True,
        headless=False,
        downloads_path=download_path,
        headers=headers,
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/127.0.0.0 Safari/537.36",
        enable_stealth=True,  # 🚀 启用 playwright-stealth
    )
    async with AsyncWebCrawler(config=config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        # 输出下载结果
        if result.downloaded_files:
            print("Downloaded files:")
            for f in result.downloaded_files:
                print(" -", f)
            return True
        else:
            print("No PDF files were downloaded.")
            return False

async def fetch_pdfs_from_page(url, download_dir):
    """
    对于外链情况的处理，自动重试3次
    :param url:
    :param download_dir:
    :return:
    """
    headers = {
        # 统一 UA + 现代浏览器常见头，减少“像脚本”的特征
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/127.0.0.0 Safari/537.36"),
        "Accept": ("text/html,application/xhtml+xml,application/xml;q=0.9,"
                   "image/avif,image/webp,*/*;q=0.8"),
        "Accept-Language": "en-US,en;q=0.9",
        "Upgrade-Insecure-Requests": "1",
    }
    os.makedirs(download_dir, exist_ok=True)
    config = BrowserConfig(headless=False, headers=headers)
    run_config = CrawlerRunConfig(wait_for="css:a[href*='.pdf']")
    # 是否有下载成功的
    statuses = []
    async with AsyncWebCrawler(config=config) as crawler:
        result = await crawler.arun(url=url, config=run_config)
        html = result.html

        pdf_urls = re.findall(r'href="(https?://[^"]+\.pdf)"', html, re.IGNORECASE)
        pdf_urls = list(set(pdf_urls))

        print(f"发现 {len(pdf_urls)} 个 PDF 链接")
        for link in pdf_urls[:LIMIT_NUM]:
            filename = link.split("/")[-1]
            save_path = os.path.join(download_dir, filename)
            print(f"下载 {link} → {filename}")
            for attempt in range(3):
                try:
                    async with aiohttp.ClientSession(headers=headers) as session:
                        async with async_timeout.timeout(60):  # 超时时间延长
                            async with session.get(link, ssl=False) as resp:
                                if resp.status == 200:
                                    with open(save_path, "wb") as f:
                                        f.write(await resp.read())
                                    print(f"✅ 下载成功: {filename}")
                                    statuses.append(True)
                                    break
                                else:
                                    print(f"⚠️ 状态码 {resp.status}")
                                    statuses.append(False)
                    await asyncio.sleep(2)
                except Exception as e:
                    print(f"❌ 第 {attempt + 1} 次下载出错: {e}")
                    if attempt == 2:
                        statuses.append(False)
    status = all(statuses)
    return status

def download_one_site(one_site, select_method="all"):
    site_name = one_site["name"]
    site_url = one_site["url"]
    print(f"下载 {site_name} 中的PDF文件 ...")
    save_dir = os.path.join(SAVE_DIR, site_name)
    # 依次尝试不同下载方式，成功后立即停止
    success = False  # 标记是否已成功下载
    if select_method in ["all", "href_pdf", "application_pdf"]:
        for method in ["href_pdf", "application_pdf"]:
            if select_method != "all" and method != select_method:
                print(f"跳过 {method} 方法，因为已选择 {select_method} 方法。")
                continue
            print(f"使用 {method} 方法下载 {site_name} 中的PDF文件 ...")
            run_config = get_run_configs(name=method)
            status = asyncio.run(download_all_pdfs_from_url(run_config, site_url, save_dir))
            print(f"{site_name} 使用 {method} 下载结果：{status}")
            # 如果成功（status 为 True），则停止继续尝试
            if status:
                print(f"{site_name} 下载成功，停止尝试其他方法。")
                success = True
                break
            else:
                print(f"{site_name} 使用 {method} 方式未成功，尝试下一个方式。")
        else:
            # 如果所有方式都失败（for循环未break），打印提示
            print(f"❌ {site_name} 所有下载方式均未成功。")
    if select_method in ["all", "fetch_pdfs_by_url"]:
        # 如果所有方式都失败，则尝试 fetch_pdfs_from_page
        if not success:
            print(f"尝试 fetch_pdfs_from_page ...")
            status = asyncio.run(fetch_pdfs_from_page(site_url, save_dir))
            print(f"{site_name} 使用 fetch_pdfs_from_page 下载结果：{status}")

def download_all_sites():
    for one_site in tqdm(web_sites, desc="下载进度"):
        download_one_site(one_site)

if __name__ == "__main__":
    SAVE_DIR = "./downloaded_pdfs"
    # download_one_site(one_site = web_sites[-1], select_method="application_pdf")
    download_one_site(one_site = web_sites[-1], select_method="fetch_pdfs_by_url")
    # download_all_sites()
