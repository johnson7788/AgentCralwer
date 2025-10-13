#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/10/11 17:11
# @File  : stealth.py.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  :

from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)  # 打开真实浏览器窗口
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/127.0.0.0 Safari/537.36",
        locale="en-US,en;q=0.9",
    )
    page = context.new_page()
    page.goto("https://ir.tesla.com/")
    print(page.title())
    browser.close()
