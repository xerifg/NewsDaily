import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET
import re

import requests


# 一些科技新闻 RSS/Atom 源，你可以按需增删
TECH_FEEDS = {
    "Hacker News – Frontpage": "https://hnrss.org/frontpage",
    "The Verge – Tech": "https://www.theverge.com/rss/index.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
}

# 一些财经新闻 RSS 源（尽量选择公开可访问的）
FINANCE_FEEDS = {
    "Yahoo Finance – Top Stories": "https://finance.yahoo.com/rss/topstories",
    "CNBC – Top News": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "MarketWatch – Top Stories": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
}

# 过去多少小时的资讯
HOURS_WINDOW = 24

# 每个源最多取多少条
MAX_ITEMS_PER_SOURCE = 15

# 全局最多推送多少条，避免消息太长
MAX_TOTAL_ITEMS = 40

# 交给大模型总结时，每个类别最多提供多少篇（避免 prompt 过长）
MAX_ITEMS_FOR_LLM_PER_CATEGORY = 25


def parse_rss_datetime(date_str: str) -> Optional[datetime]:
    """
    尝试解析 RSS/Atom 中的时间字段。
    失败则返回 None。
    """
    if not date_str:
        return None

    s = date_str.strip()

    # 常见格式：RFC 2822 / RFC 822，比如 "Mon, 02 Mar 2026 10:00:00 GMT"
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            # 如果没有时区信息，默认为 UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # ISO8601（Atom 常见）：2026-03-02T10:00:00Z / 2026-03-02T10:00:00+00:00 / +0000
    try:
        iso = s
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"

        # 处理 +0000 / -0800 这种无冒号的时区偏移
        m = re.match(r"^(.*)([+-]\d{2})(\d{2})$", iso)
        if m:
            iso = f"{m.group(1)}{m.group(2)}:{m.group(3)}"

        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    return None


def is_within_last_hours(dt: datetime, hours: int) -> bool:
    now = datetime.now(timezone.utc)
    return now - dt <= timedelta(hours=hours)


def _pick_atom_link(entry: ET.Element) -> str:
    # Atom 的 link 可能有多个，优先 rel="alternate"
    for link_el in entry.findall("{*}link"):
        rel = (link_el.get("rel") or "").strip().lower()
        href = (link_el.get("href") or "").strip()
        if not href:
            continue
        if rel in ("", "alternate"):
            return href
    return ""


def fetch_feed(url: str, source_name: str, category: str) -> List[Dict]:
    """
    抓取单个 RSS/Atom 源，返回过去 HOURS_WINDOW 小时内的条目列表。
    每条数据包含：title, link, published_at, source, category。
    """
    print(f"Fetching feed: {source_name} ({url})")
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Failed to fetch {source_name}: {e}", file=sys.stderr)
        return []

    # 解析 XML
    try:
        root = ET.fromstring(resp.text)
    except Exception as e:
        print(f"[WARN] Failed to parse XML for {source_name}: {e}", file=sys.stderr)
        return []

    items: List[Dict] = []

    # 1) RSS: <item>
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not link:
            guid = (item.findtext("guid") or "").strip()
            if guid.startswith("http://") or guid.startswith("https://"):
                link = guid

        pub_raw = (item.findtext("pubDate") or "").strip()
        if not pub_raw:
            pub_raw = (item.findtext("{*}date") or "").strip()  # dc:date 等

        pub_dt = parse_rss_datetime(pub_raw)
        if pub_dt is None:
            # 没时间就先保守地过滤掉，避免推太旧
            continue

        if not is_within_last_hours(pub_dt, HOURS_WINDOW):
            continue

        if not title or not link:
            continue

        items.append(
            {
                "title": title,
                "link": link,
                "published_at": pub_dt,
                "source": source_name,
                "category": category,
            }
        )

    # 2) Atom: <entry>
    for entry in root.findall(".//{*}entry"):
        title = (entry.findtext("{*}title") or "").strip()
        href = _pick_atom_link(entry)
        updated_raw = (entry.findtext("{*}updated") or "").strip()
        if not updated_raw:
            updated_raw = (entry.findtext("{*}published") or "").strip()

        pub_dt = parse_rss_datetime(updated_raw)
        if pub_dt is None:
            continue

        if not is_within_last_hours(pub_dt, HOURS_WINDOW):
            continue

        if not title or not href:
            continue

        items.append(
            {
                "title": title,
                "link": href,
                "published_at": pub_dt,
                "source": source_name,
                "category": category,
            }
        )

    # 按时间倒序排，并限制数量
    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items[:MAX_ITEMS_PER_SOURCE]


def _dedupe_and_sort(items: List[Dict]) -> List[Dict]:
    unique_map = {}
    for item in items:
        key = (item.get("title"), item.get("link"))
        if key not in unique_map:
            unique_map[key] = item

    unique_items = list(unique_map.values())
    unique_items.sort(key=lambda x: x["published_at"], reverse=True)
    return unique_items


def collect_news(feeds: Dict[str, str], category: str) -> List[Dict]:
    """
    抓取一个类别的所有 feeds，合并并排序。
    """
    all_items: List[Dict] = []
    for source_name, url in feeds.items():
        items = fetch_feed(url, source_name, category=category)
        all_items.extend(items)

    unique_items = _dedupe_and_sort(all_items)
    return unique_items[:MAX_TOTAL_ITEMS]


def collect_all_news() -> List[Dict]:
    tech = collect_news(TECH_FEEDS, category="科技")
    finance = collect_news(FINANCE_FEEDS, category="财经")
    return _dedupe_and_sort(tech + finance)[: (MAX_TOTAL_ITEMS * 2)]


def build_raw_markdown(news_items: List[Dict]) -> str:
    """
    把资讯列表转成 Markdown 文本（不走大模型），用于 Server 酱的 desp。
    """
    if not news_items:
        return "过去 24 小时内，未从配置的资讯源中抓取到符合条件的内容。"

    lines = []
    lines.append(f"过去 {HOURS_WINDOW} 小时资讯列表（UTC 时间）：")
    lines.append("")
    for idx, item in enumerate(news_items, start=1):
        pub_str = item["published_at"].astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        title = item["title"]
        link = item["link"]
        source = item["source"]
        category = item.get("category") or "未知"

        # 每条按 Markdown 列表输出
        lines.append(
            f"{idx}. **[{category}] {title}**  \n"
            f"   来源：{source}  \n"
            f"   时间：{pub_str}  \n"
            f"   链接：{link}"
        )

    return "\n".join(lines)


def _render_articles_for_llm(news_items: List[Dict]) -> str:
    """
    将文章列表渲染给大模型作为“材料”，每篇给一个稳定编号，便于在摘要中引用出处。
    """
    lines = []
    for i, item in enumerate(news_items, start=1):
        aid = f"A{i:02d}"
        item["aid"] = aid
        pub_str = item["published_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(
            f"{aid} | {item.get('category','未知')} | {item.get('source','')} | {pub_str} | {item.get('title','').strip()} | {item.get('link','').strip()}"
        )
    return "\n".join(lines)


def summarize_with_deepseek(news_items: List[Dict]) -> Optional[str]:
    """
    使用 DeepSeek 对过去24小时的科技+财经资讯做汇总，并在每条汇总中附上出处链接。
    需要环境变量：
    - DEEPSEEK_API_KEY（必填）
    可选：
    - DEEPSEEK_MODEL（默认 deepseek-chat）
    - DEEPSEEK_BASE_URL（默认 https://api.deepseek.com）
    """
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return None

    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()

    tech = [x for x in news_items if x.get("category") == "科技"][:MAX_ITEMS_FOR_LLM_PER_CATEGORY]
    fin = [x for x in news_items if x.get("category") == "财经"][:MAX_ITEMS_FOR_LLM_PER_CATEGORY]
    picked = tech + fin
    picked = _dedupe_and_sort(picked)

    if not picked:
        return "过去 24 小时内未抓取到资讯，无法生成汇总。"

    material = _render_articles_for_llm(picked)
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    system = (
        "你是一名金融机构的宏观及多资产研究员，日常工作包括跟踪科技与宏观财经资讯，"
        "并为内部投研/资产配置讨论提供结构化的信息摘要和风险提示。\n"
        "【输出格式硬性要求，必须严格遵守】：\n"
        "1) 输出必须为中文 Markdown。\n"
        "2) 结构必须严格包含且仅包含以下三个一级标题，顺序也必须一致：\n"
        "   - ## 科技要点\n"
        "   - ## 财经要点\n"
        "   - ## 今日投资理财建议（非投资建议）\n"
        "3) 每个一级标题下必须有 3–8 条有编号的要点（使用“1. 2. 3.”这种 Markdown 列表编号），任何一级标题下都不允许为空。\n"
        "4) 每一条“科技要点/财经要点”都必须在末尾用“出处：”列出 1–3 个来源，"
        "   且必须使用 Axx 编号+Markdown 链接的形式，例如：出处：[A01](https://...) [A12](https://...)。\n"
        "5) “今日投资理财建议（非投资建议）”必须基于【财经要点】提炼，给出板块/主题层面的关注点与风险提示：\n"
        "   - 只能讨论行业、板块、资产类别或宏观主题，禁止提及具体个股或单一债券；\n"
        "   - 可以用不确定表达（如“可能/或许/需关注风险”）讨论哪些板块偏强/偏弱的条件与触发因素；\n"
        "   - 禁止推荐具体标的、禁止承诺收益、禁止使用确定性措辞（如“必涨/必跌”）。\n"
        "   - 每条建议也必须在末尾给出出处链接（同样用 Axx 编号+Markdown 链接）。\n"
        "6) 即使你认为材料较少或不够直接指向投资，也必须在“今日投资理财建议（非投资建议）”中给出以“风险提示/需关注点”为主的 3–6 条建议，"
        "可以偏保守、偏宏观，但绝对不能省略这一部分。\n"
        "7) 内容要尽量精炼，整体控制在约 2500–5000 中文字符以内。\n"
    )

    user = (
        f"当前时间（UTC）：{now_utc}\n"
        f"请根据以下过去 {HOURS_WINDOW} 小时文章材料进行汇总。材料格式为：编号 | 分类 | 来源 | 时间 | 标题 | 链接。\n"
        f"务必检查：输出中一定要依次出现“## 科技要点”“## 财经要点”“## 今日投资理财建议（非投资建议）”三个一级标题，"
        f"且每个标题下都要有 3–8 条编号要点，否则视为不符合要求。\n\n"
        f"{material}"
    )

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
        "max_tokens": 1400,
        "stream": False,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=40)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()
    except Exception as e:
        print(f"[WARN] DeepSeek 汇总失败，将回退为原始列表：{e}", file=sys.stderr)
        return None


def send_to_serverchan(title: str, desp: str) -> None:
    """
    调用 Server 酱 Turbo API 推送到微信。
    参考文档（2026）：https://sctapi.ftqq.com/{SendKey}.send
    参数：title（必填）、desp（可选，Markdown）
    """
    sendkey = os.environ.get("SERVERCHAN_SENDKEY")
    if not sendkey:
        raise RuntimeError(
            "环境变量 SERVERCHAN_SENDKEY 未设置，请在 GitHub 仓库 Secrets 中配置。"
        )

    url = f"https://sctapi.ftqq.com/{sendkey}.send"
    payload = {
        "title": title,
        "desp": desp,
    }

    try:
        resp = requests.post(url, data=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"请求 Server 酱 API 失败: {e}") from e

    try:
        data = resp.json()
    except Exception:
        print(
            f"[WARN] 无法解析 Server 酱返回为 JSON，原始内容：{resp.text[:500]}",
            file=sys.stderr,
        )
        return

    # Server 酱 Turbo 一般用 code / message 字段
    code = data.get("code")
    msg = data.get("message") or data.get("msg")
    if code != 0:
        raise RuntimeError(f"Server 酱返回错误 code={code}, message={msg}")
    else:
        print(f"Server 酱推送成功: {msg}")


def main() -> None:
    print("Collecting tech & finance news from RSS feeds...")
    news_items = collect_all_news()
    print(f"Collected {len(news_items)} items within last {HOURS_WINDOW} hours.")

    llm_summary = summarize_with_deepseek(news_items)
    if llm_summary:
        desp = llm_summary
        title = f"科技&财经汇总（最近 {HOURS_WINDOW} 小时）"
    else:
        desp = build_raw_markdown(news_items)
        title = f"科技&财经列表（最近 {HOURS_WINDOW} 小时）"

    # 避免标题过长
    if len(title) > 60:
        title = title[:57] + "..."

    # Server酱对内容长度有限制（不同版本/通道略有差异），这里做一个保守截断
    max_len = 28000
    if len(desp) > max_len:
        desp = desp[: (max_len - 40)].rstrip() + "\n\n（内容过长已截断）"

    print("Sending to ServerChan...")
    send_to_serverchan(title, desp)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
