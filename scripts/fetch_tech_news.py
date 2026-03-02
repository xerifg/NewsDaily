import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict, Optional
import xml.etree.ElementTree as ET

import requests


# 一些科技新闻 RSS/Atom 源，你可以按需增删
FEEDS = {
    "Hacker News – Frontpage": "https://hnrss.org/frontpage",
    "The Verge – Tech": "https://www.theverge.com/rss/index.xml",
    "TechCrunch": "https://techcrunch.com/feed/",
}

# 过去多少小时的资讯
HOURS_WINDOW = 24

# 每个源最多取多少条
MAX_ITEMS_PER_SOURCE = 15

# 全局最多推送多少条，避免消息太长
MAX_TOTAL_ITEMS = 40


def parse_rss_datetime(date_str: str) -> Optional[datetime]:
    """
    尝试解析 RSS/Atom 中的时间字段。
    失败则返回 None。
    """
    if not date_str:
        return None

    # 常见格式：RFC 2822 / RFC 822，比如 "Mon, 02 Mar 2026 10:00:00 GMT"
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            # 如果没有时区信息，默认为 UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # 其他 ISO8601 等格式可以再按需扩展
    return None


def is_within_last_hours(dt: datetime, hours: int) -> bool:
    now = datetime.now(timezone.utc)
    return now - dt <= timedelta(hours=hours)


def fetch_feed(url: str, source_name: str) -> List[Dict]:
    """
    抓取单个 RSS/Atom 源，返回过去 HOURS_WINDOW 小时内的条目列表。
    每条数据包含：title, link, published_at, source。
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
        pub_raw = (item.findtext("pubDate") or "").strip()

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
            }
        )

    # 2) Atom: <entry>
    # 通常命名空间为 {http://www.w3.org/2005/Atom}
    # 为了简单，我们用通配方式查找
    for entry in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
        title = (entry.findtext("{http://www.w3.org/2005/Atom}title") or "").strip()
        link_el = entry.find("{http://www.w3.org/2005/Atom}link")
        href = link_el.get("href").strip() if link_el is not None else ""
        updated_raw = (
            entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
        ).strip()

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
            }
        )

    # 按时间倒序排，并限制数量
    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items[:MAX_ITEMS_PER_SOURCE]


def collect_news() -> List[Dict]:
    """
    抓取所有 FEEDS，合并成一个列表，并按时间排序。
    """
    all_items: List[Dict] = []
    for source_name, url in FEEDS.items():
        items = fetch_feed(url, source_name)
        all_items.extend(items)

    # 去重（按标题+链接简单去重）
    unique_map = {}
    for item in all_items:
        key = (item["title"], item["link"])
        if key not in unique_map:
            unique_map[key] = item

    unique_items = list(unique_map.values())
    unique_items.sort(key=lambda x: x["published_at"], reverse=True)

    return unique_items[:MAX_TOTAL_ITEMS]


def build_markdown(news_items: List[Dict]) -> str:
    """
    把资讯列表转成 Markdown 文本，用于 Server 酱的 desp。
    """
    if not news_items:
        return "过去 24 小时内，未从配置的科技资讯源中抓取到符合条件的内容。"

    lines = []
    lines.append(f"过去 {HOURS_WINDOW} 小时科技资讯汇总（UTC 时间）：")
    lines.append("")
    for idx, item in enumerate(news_items, start=1):
        pub_str = item["published_at"].astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
        title = item["title"]
        link = item["link"]
        source = item["source"]

        # 每条按 Markdown 列表输出
        lines.append(
            f"{idx}. **{title}**  \n"
            f"   来源：{source}  \n"
            f"   时间：{pub_str}  \n"
            f"   链接：{link}"
        )

    return "\n".join(lines)


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
    print("Collecting tech news from RSS feeds...")
    news_items = collect_news()
    print(f"Collected {len(news_items)} items within last {HOURS_WINDOW} hours.")

    desp = build_markdown(news_items)

    if news_items:
        first_time = news_items[0]["published_at"].astimezone(timezone.utc)
        first_time_str = first_time.strftime("%Y-%m-%d %H:%M UTC")
        title = f"科技资讯日报（最近 {HOURS_WINDOW} 小时，最新 {first_time_str}）"
    else:
        title = f"科技资讯日报（最近 {HOURS_WINDOW} 小时无更新）"

    # 避免标题过长
    if len(title) > 60:
        title = title[:57] + "..."

    print("Sending to ServerChan...")
    send_to_serverchan(title, desp)
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
