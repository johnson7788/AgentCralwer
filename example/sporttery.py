#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Date  : 2025/11/14 22:44
# @File  : sporttery.py
# @Author: johnson
# @Contact : github: johnson7788
# @Desc  :
"""
使用 Crawl4AI 抓取中国体彩竞足/竞篮实战数据（JS 渲染友好）
目标：
  - 竞彩足球实战数据：https://www.sporttery.cn/jc/zqszsc/
  - 竞彩篮球实战数据：https://www.sporttery.cn/jc/lqszsc/

特性：
  1) 通过 Crawl4AI 的 AsyncWebCrawler 渲染并抓取页面（可设置 stealth、UA、代理、headless）。
  2) 等待关键 CSS 元素出现（wait_for），必要时执行自定义 JS（js_code）。
  3) 将完整 HTML 保存，随后用 bs4 做健壮解析，导出 CSV / JSON。
  4) 可扩展为 JsonCssExtractionStrategy 精准提取（示例已预留）。

快速开始：
  pip install crawl4ai requests beautifulsoup4 lxml
  python crawl4ai_sporttery.py --outdir output

参数：
  --headless/--no-headless      是否无头（默认无头 true）
  --proxy                       代理，例如 http://127.0.0.1:7890
  --min-wait / --max-wait       礼貌等待（默认 0.8 ~ 1.8s）
  --timeout                     单次页面 arun 等待超时（默认 45s）
  --bypass-cache                绕过 Crawl4AI 缓存
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import os
import random
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List

from bs4 import BeautifulSoup

# Crawl4AI 核心
from crawl4ai import AsyncWebCrawler
from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig

FOOTBALL_URL = "https://www.sporttery.cn/jc/zqszsc/"
BASKETBALL_URL = "https://www.sporttery.cn/jc/lqszsc/"
BASE_URL = "https://www.sporttery.cn"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0 Safari/537.36"
)

@dataclass
class Match:
    sport: str  # football / basketball
    date: str | None
    time: str | None
    league: str | None
    home: str | None
    away: str | None
    handicap: str | None
    odds: str | None
    detail_url: str | None  # 详情页链接
    detail_html: str | None  # 详情页原始HTML
    detail_data: Dict[str, Any] | None  # 解析后的详情数据
    extra: Dict[str, Any]  # 主页面额外数据


def polite_wait(min_wait: float, max_wait: float):
    time.sleep(random.uniform(min_wait, max_wait))


def save_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def textify(node) -> str:
    if node is None:
        return ""
    return re.sub(r"\s+", " ", node.get_text(strip=True))


# ------------------------ Crawl4AI 抓取 ------------------------ #
async def crawl_html(url: str, headless: bool, proxy: str | None, timeout: int,
                     bypass_cache: bool) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": url,
    }

    bcfg = BrowserConfig(
        headless=headless,
        enable_stealth=True,
        user_agent=USER_AGENT,
        headers=headers,
        proxy=proxy or None,
        accept_downloads=False,
    )

    # 等待页面出现表格/列表类元素；部分站点为异步渲染
    run = CrawlerRunConfig(
        wait_for="css: #matchList .m-tab tr",
        wait_for_timeout=timeout * 1000,
        bypass_cache=bypass_cache,
        # 如需点击“加载更多”，可在此加入 js_code（示例）：
        # js_code="""
        #   const btn = document.querySelector('.load-more');
        #   if (btn) { btn.click(); await new Promise(r=>setTimeout(r,1200)); }
        # """,
    )

    async with AsyncWebCrawler(config=bcfg) as crawler:
        result = await crawler.arun(url=url, config=run)
        if not result.success:
            raise RuntimeError(f"Crawl4AI 抓取失败: {result.error_message}")
        # 添加更多信息用于调试
        print(f"  [DEBUG] 页面状态: {result.status_code if hasattr(result, 'status_code') else 'N/A'}")
        print(f"  [DEBUG] 页面长度: {len(result.html) if result.html else 0} 字符")
        print(f"  [DEBUG] 是否包含表格元素: {'table' in result.html if result.html else False}")
        # HTML 在 result.html；也可用 result.markdown / result.links / result.downloaded_files
        return result.html


# ------------------------ 解析逻辑（健壮 / 兜底） ------------------------ #

def parse_table_like(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """尽力从页面抽取一个“比赛列表”结构；兼容 <table> 与 div 栅格。"""
    KEY_HINTS = [
        "赛事编号","联赛","主队","客队","VS","比赛开始时间",
        "胜平负","让球","比分","总进球","半全场","开售状态","比赛资讯"
    ]
    candidates = []
    for tbl in soup.find_all(["table", "div"]):
        header_text = " ".join(textify(h) for h in tbl.find_all(["th", "thead", "header"]))
        score = sum(h in header_text for h in KEY_HINTS)
        if score == 0:
            first_row = tbl.find(["tr", "div"]) or None
            if first_row:
                score = sum(h in textify(first_row) for h in KEY_HINTS)
        if score >= 2:
            candidates.append((score, tbl))
    candidates.sort(key=lambda x: x[0], reverse=True)

    for _, tbl in candidates:
        rows: List[Dict[str, Any]] = []
        # 1) 尝试拿到表头
        headers = []
        thead = tbl.find("thead")
        if thead:
            headers = [textify(th) for th in thead.find_all("th")]
        if not headers:
            tr0 = tbl.find("tr")
            if tr0:
                cells0 = tr0.find_all(["th", "td"])
                # 如果第一行全是 th，说明它是表头
                if cells0 and all(c.name == "th" for c in cells0):
                    headers = [textify(th) for th in cells0]

        # 2) 逐行遍历 tr，自身抽取+找链接（避免 idx 对不齐）
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue  # 过滤掉非数据行
            # 文本单元格
            cells_text = [textify(td) for td in tds]
            # 组装行
            if not headers:
                headers = [f"col_{i+1}" for i in range(len(cells_text))]
            row = {headers[i] if i < len(headers) else f"col_{i+1}": cells_text[i]
                   for i in range(len(cells_text))}
            # 3) 精准从第3列取比赛详情链接
            detail_link_tag = None
            if len(tds) >= 3:
                # 第三列是主客队列
                detail_link_tag = tds[2].select_one('a[href*="zqdz"], a[href*="lqdz"]')
            # 兜底：整行里找一次
            if not detail_link_tag:
                detail_link_tag = tr.select_one('a[href*="zqdz"], a[href*="lqdz"]')
            detail_url = None
            if detail_link_tag:
                href = detail_link_tag.get("href", "").strip()
                if href:
                    # 补全协议相对 URL
                    if href.startswith("//"):
                        detail_url = "https:" + href
                    else:
                        # 相对路径也补全到站点根（以当前站点为基准）
                        detail_url = urljoin("https://www.sporttery.cn/", href)
            row["detail_url"] = detail_url
            rows.append(row)

        # 如果这个候选表拿到了有效数据，就直接返回
        if rows:
            return rows

    return []


def map_row_to_match(row: Dict[str, Any], sport: str) -> Match:
    def first_key(d: Dict[str, Any], keys: List[str]) -> str | None:
        for k in keys:
            for kk in d.keys():
                if kk and (k in kk):
                    v = str(d.get(kk, "")).strip()
                    if v:
                        return v
        return None

    date = first_key(row, ["日期", "日期/时间", "比赛时间", "时间"])
    time_field = None
    if date and re.search(r"\d{1,2}:\d{2}", date):
        m = re.search(r"(\d{1,2}[-/年]\d{1,2}(?:[-/年]\d{1,2})?)\s*(\d{1,2}:\d{2})", date)
        if m:
            date, time_field = m.group(1), m.group(2)
    league = first_key(row, ["联赛", "赛事", "赛区", "赛事名称"])
    home = first_key(row, ["主队", "主", "主队名称"])
    away = first_key(row, ["客队", "客", "客队名称"])
    handicap = first_key(row, ["让球", "让分", "盘口", "让步"])
    odds = first_key(row, ["赔率", "胜", "平", "负", "主胜", "客胜", "让分胜", "让分负"])

    detail_url = row.get("detail_url")
    if detail_url and not detail_url.startswith("http"):
        detail_url = BASE_URL + detail_url if detail_url.startswith("/") else BASE_URL + "/" + detail_url

    return Match(
        sport=sport,
        date=date,
        time=time_field,
        league=league,
        home=home,
        away=away,
        handicap=handicap,
        odds=odds,
        detail_url=detail_url,
        detail_html=None,
        detail_data=None,
        extra={k: v for k, v in row.items() if k != "detail_url"},
    )


def parse_matches(html: str, sport: str) -> List[Match]:
    soup = BeautifulSoup(html, "lxml")
    rows = parse_table_like(soup)
    matches = [map_row_to_match(r, sport) for r in rows]

    # 兜底：尝试从 <script> 中挖 JSON
    if not matches:
        scripts = soup.find_all("script")
        json_candidates = []
        for sc in scripts:
            txt = sc.text or ""
            for m in re.finditer(r"(\{.*?\}|\[.*?\])", txt, flags=re.S):
                try:
                    obj = json.loads(m.group(1))
                    json_candidates.append(obj)
                except Exception:
                    pass
        def flatten(x):
            if isinstance(x, dict):
                yield x
                for v in x.values():
                    yield from flatten(v)
            elif isinstance(x, list):
                for v in x:
                    yield from flatten(v)
        for obj in json_candidates:
            for node in flatten(obj):
                keys = " ".join(map(str, node.keys())).lower()
                if any(k in keys for k in ["league", "home", "away", "host", "guest", "match", "odds"]):
                    matches.append(map_row_to_match({k: str(v) for k, v in node.items()}, sport))
    return matches


# ------------------------ 详情页解析 ------------------------ #

def parse_detail_html(html: str) -> Dict[str, Any]:
    """解析比赛详情页的HTML，提取球队信息、历史交锋、积分榜等数据"""
    if not html:
        return {}
    
    soup = BeautifulSoup(html, "lxml")
    result = {}
    
    try:
        # 提取对阵卡片信息
        match_card = soup.find('div', class_='m-matchCard')
        if match_card:
            card_data = {}
            
            # 提取比赛基本信息（周五303，欧篮联等）
            match_info = match_card.find('div', class_='u-top')
            if match_info:
                card_data['match_id'] = textify(match_info).strip()
            
            # 提取对阵双方信息
            teams = {}
            home_team = match_card.find('div', class_='u-middleRt')
            away_team = match_card.find('div', class_='u-middleLf')
            
            if home_team:
                home_link = home_team.find('a', href=True)
                if home_link:
                    teams['home'] = {
                        'name': textify(home_link).strip(),
                        'url': home_link['href']
                    }
            
            if away_team:
                away_link = away_team.find('a', href=True)
                if away_link:
                    teams['away'] = {
                        'name': textify(away_link).strip(),
                        'url': away_link['href']
                    }
            
            # 获取比赛时间
            match_time = match_card.find('div', class_='u-btm')
            if match_time:
                card_data['match_time'] = textify(match_time).strip()
            
            card_data['teams'] = teams
            result['match_card'] = card_data
        
        # 提取特征分析数据
        feature_analysis = soup.find('div', class_='m-featureAnalysis')
        if feature_analysis:
            features = {}
            
            # 提取左右两队的统计数据
            left_stats = feature_analysis.find('div', class_='m-left')
            right_stats = feature_analysis.find('div', class_='m-right')
            center_labels = feature_analysis.find('div', class_='m-center')
            
            if left_stats and right_stats and center_labels:
                left_conts = left_stats.find_all('div', class_='u-cont')
                right_conts = right_stats.find_all('div', class_='u-cont')
                label_conts = center_labels.find_all('div', class_='u-cont')
                
                stats_list = []
                for i in range(min(len(left_conts), len(right_conts), len(label_conts))):
                    left_text = textify(left_conts[i]).strip()
                    right_text = textify(right_conts[i]).strip()
                    label = textify(label_conts[i]).strip()
                    
                    stats_list.append({
                        'label': label,
                        'away': left_text,  # 客队数据
                        'home': right_text  # 主队数据
                    })
                
                features['stats'] = stats_list
            
            result['feature_analysis'] = features
        
        # 提取历史交锋数据
        history_section = soup.find('div', id='ls')
        if history_section:
            history_data = {}
            
            # 提取总体战绩
            history_title = history_section.find('div', class_='m-tableTitle')
            if history_title:
                history_data['summary'] = textify(history_title).strip()
            
            # 提取历史比赛列表
            history_table = history_section.find('table', class_='m-tableData')
            if history_table:
                matches = []
                for tr in history_table.find_all('tr')[1:]:  # 跳过表头
                    tds = tr.find_all('td')
                    if len(tds) >= 4:
                        match_info = {
                            'date': textify(tds[0]).strip(),
                            'league': textify(tds[1]).strip(),
                            'match': textify(tds[2]).strip(),
                            'score_trend': textify(tds[3]).strip()
                        }
                        matches.append(match_info)
                
                history_data['matches'] = matches
            
            result['history'] = history_data
        
        # 提取积分榜数据
        standings_section = soup.find('div', id='jfb')
        if standings_section:
            standings = {}
            
            # 提取双方积分榜
            tables = standings_section.find_all('table', class_='m-tableData-1')
            for i, table in enumerate(tables[:2]):  # 最多两个队
                team_standings = {}
                
                # 提取队名
                table_title = standings_section.find_all('div', class_='m-tableTitle')[i] if i < len(standings_section.find_all('div', class_='m-tableTitle')) else None
                if table_title:
                    team_name = table_title.find('span', class_='u-team')
                    if team_name:
                        team_standings['team'] = textify(team_name).strip()
                
                # 提取积分数据
                rows = table.find_all('tr')
                if len(rows) >= 2:
                    headers = [textify(th).strip() for th in rows[0].find_all('th')]
                    data = []
                    for row in rows[1:]:
                        cells = [textify(td).strip() for td in row.find_all('td')]
                        if len(cells) == len(headers):
                            data.append(dict(zip(headers, cells)))
                    team_standings['data'] = data
                
                standings[f'team_{i+1}'] = team_standings
            
            result['standings'] = standings
        
        # 提取比赛近况（最近10场）
        recent_section = soup.find('div', id='bsjk')
        if recent_section:
            recent_data = {}
            
            # 提取双方最近比赛
            match_tables = recent_section.find_all('table', class_='m-tableData')
            for i, table in enumerate(match_tables[:2]):  # 两个队
                team_matches = []
                
                # 提取队名和统计
                table_title = recent_section.find_all('div', class_='m-tableTitle-2')[i] if i < len(recent_section.find_all('div', class_='m-tableTitle-2')) else None
                if table_title:
                    team_name = table_title.find('span', class_='u-team')
                    stats_text = textify(table_title).strip()
                    recent_data[f'team_{i+1}_summary'] = stats_text
                
                # 提取比赛列表
                rows = table.find_all('tr')
                for row in rows[1:]:  # 跳过表头
                    tds = row.find_all('td')
                    if len(tds) >= 4:
                        match_info = {
                            'date': textify(tds[0]).strip(),
                            'league': textify(tds[1]).strip(),
                            'match': textify(tds[2]).strip(),
                            'result': textify(tds[3]).strip()
                        }
                        team_matches.append(match_info)
                
                recent_data[f'team_{i+1}_matches'] = team_matches
            
            result['recent_matches'] = recent_data
        
        # 提取赛程数据（如果有）
        schedule_section = soup.find('div', class_='m-schedule')
        if schedule_section:
            schedule_data = {}
            # 这里可以添加赛程解析逻辑
            result['schedule'] = schedule_data
        
    except Exception as e:
        print(f"解析详情页时出错: {e}")
        result['error'] = str(e)
    
    return result


# ------------------------ 导出 ------------------------ #

def export_csv(matches: List[Match], csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["sport", "date", "time", "league", "home", "away", "handicap", "odds", "detail_url", "extra"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for m in matches:
            row = asdict(m)
            row["extra"] = json.dumps(row["extra"], ensure_ascii=False)
            row["detail_url"] = m.detail_url or ""
            w.writerow(row)


def export_json(matches: List[Match], json_path: Path):
    json_path.parent.mkdir(parents=True, exist_ok=True)
    # 转换为可序列化的格式
    data = []
    for m in matches:
        match_dict = asdict(m)
        # 确保所有字段都是可序列化的
        if match_dict.get('detail_data'):
            match_dict['detail_data'] = match_dict['detail_data']
        data.append(match_dict)
    
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ------------------------ 主流程 ------------------------ #

async def crawl_detail_page(match: Match, headless: bool, proxy: str | None, 
                          timeout: int, bypass_cache: bool) -> Match:
    """抓取单个比赛的详情页"""
    if not match.detail_url:
        return match
    
    try:
        print(f"  正在抓取详情页: {match.detail_url}")
        html = await crawl_html(match.detail_url, headless=headless, proxy=proxy, 
                              timeout=timeout, bypass_cache=bypass_cache)
        
        # 解析详情页
        detail_data = parse_detail_html(html)
        
        # 更新match对象
        match.detail_html = html
        match.detail_data = detail_data
        
        print(f"  ✓ 详情页解析完成: {match.league or '未知赛事'}")
        
    except Exception as e:
        print(f"  ⚠ 抓取详情页失败 {match.detail_url}: {e}")
        match.detail_data = {"error": str(e)}
    
    # 礼貌等待
    polite_wait(0.5, 1.2)
    
    return match


async def run_once(url: str, sport: str, outdir: Path, headless: bool, proxy: str | None,
                   timeout: int, bypass_cache: bool, fetch_details: bool = False) -> List[Match]:
    html = await crawl_html(url, headless=headless, proxy=proxy, timeout=timeout, bypass_cache=bypass_cache)
    save_text(outdir / f"raw_{sport}.html", html)
    matches = parse_matches(html, sport)
    
    # 如果需要抓取详情页
    if fetch_details and matches:
        print(f"\n  开始抓取 {len(matches)} 个比赛的详情页...")
        # 并行抓取详情页（限制并发数）
        semaphore = asyncio.Semaphore(1)  # 限制并发数为3
        
        async def bounded_crawl(match):
            async with semaphore:
                return await crawl_detail_page(match, headless, proxy, timeout, bypass_cache)
        
        matches = await asyncio.gather(*[bounded_crawl(m) for m in matches])
        
        # 保存详情页原始HTML
        for i, match in enumerate(matches):
            if match.detail_html:
                safe_name = f"detail_{sport}_{i+1}_{match.league or 'match'}.html"
                safe_name = re.sub(r'[^\w\-_.]', '_', safe_name)[:100]  # 清理文件名
                save_text(outdir / safe_name, match.detail_html)
    
    return matches


def main():
    p = argparse.ArgumentParser(description="使用 Crawl4AI 抓取竞足/竞篮实战数据并导出")
    p.add_argument("--outdir", default="./output")
    p.add_argument("--headless", dest="headless", action="store_true", default=False)
    p.add_argument("--no-headless", dest="headless", action="store_false")
    p.add_argument("--proxy", default=os.environ.get("HTTP_PROXY"))
    p.add_argument("--min-wait", type=float, default=0.8)
    p.add_argument("--max-wait", type=float, default=1.8)
    p.add_argument("--timeout", type=int, default=45, help="wait_for 超时（秒）")
    p.add_argument("--bypass-cache", action="store_true", help="绕过 Crawl4AI 缓存")
    p.add_argument("--fetch-details", action="store_true", default=True,  help="抓取每个比赛的详情页数据")
    p.add_argument("--fetch-limit", type=int, default=0, help="限制抓取详情页的数量（0表示不限制）")
    args = p.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    all_matches: List[Match] = []

    async def _runner():
        for sport, url in [("football", FOOTBALL_URL), ("basketball", BASKETBALL_URL)]:
            polite_wait(args.min_wait, args.max_wait)
            try:
                print(f"\n正在抓取 {sport} 数据...")
                matches = await run_once(url, sport, outdir, args.headless, args.proxy, args.timeout, args.bypass_cache, args.fetch_details)
                if matches:
                    print(f"✓ [{sport}] 解析到 {len(matches)} 场")
                else:
                    print(f"⚠ [ {sport} ] 解析到 0 场，页面可能已更改或需要JavaScript交互")
                
                # 限制详情页数量
                if args.fetch_limit > 0:
                    matches = matches[:args.fetch_limit]
                    print(f"  由于 --fetch-limit {args.fetch_limit}, 只处理前 {len(matches)} 场比赛")
                
                all_matches.extend(matches)
            except Exception as e:
                print(f"✗ 抓取 {sport} 出错：{e}")

    asyncio.run(_runner())

    # 导出CSV和JSON
    if all_matches:
        export_json(all_matches, outdir / "sporttery_matches.json")
        print(f"✓ JSON导出完成： {outdir / 'sporttery_matches.json'}")
        
        # 如果抓取了详情页，导出详情数据
        if args.fetch_details:
            detail_count = sum(1 for m in all_matches if m.detail_data)
            print(f"✓ 成功抓取 {detail_count}/{len(all_matches)} 个比赛的详情页数据")
    else:
        print("⚠ 没有抓取到任何比赛数据")


if __name__ == "__main__":
    main()
