import os
import sys
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import difflib
import re
import time

import requests
import yaml


# 浏览器 UA：很多站点（量子位、Google News 等）会拦截默认的 python-requests UA
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}

ROOT_DIR = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT_DIR / "docs"

# 固定板块顺序（与汇总 prompt 的输出结构绑定，不随配置变化）
CATEGORIES = ("AI", "自动驾驶", "机器人", "开源项目", "论文")

# 板块标题 emoji（用于回退列表路径，与 LLM 汇总的板块标题保持一致）
CATEGORY_EMOJI = {
    "AI": "🤖 AI 大模型",
    "自动驾驶": "🚗 自动驾驶",
    "机器人": "🦾 机器人与具身智能",
    "开源项目": "🔥 开源项目",
    "论文": "📄 论文速递",
}


# ============================================================
# 配置加载：数据源与限额全部来自 config/feeds.yaml
# ============================================================

def _resolve_config_path() -> Path:
    """配置路径优先级：--config 参数 > NEWS_CONFIG 环境变量 > 仓库默认路径。"""
    argv = sys.argv
    for i, arg in enumerate(argv):
        if arg == "--config" and i + 1 < len(argv):
            return Path(argv[i + 1])
    env = (os.environ.get("NEWS_CONFIG") or "").strip()
    if env:
        return Path(env)
    return ROOT_DIR / "config" / "feeds.yaml"


def _load_config() -> Dict[str, Any]:
    path = _resolve_config_path()
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在：{path}（可用 --config 或环境变量 NEWS_CONFIG 指定）")
    print(f"Loading config: {path}")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


_config = _load_config()

# 垂直源 / 综合源 / arXiv 源：列表项形如 {name, url, category?, max_items?}
VERTICAL_FEEDS: List[Dict[str, Any]] = _config.get("vertical_feeds", [])
GENERIC_FEEDS: List[Dict[str, Any]] = _config.get("generic_feeds", [])
ARXIV_FEEDS: List[Dict[str, Any]] = _config.get("arxiv_feeds", [])
PAPER_KEYWORDS: List[str] = _config.get("paper_keywords", [])

# 关键词分组：综合源条目按命中顺序归类（先判断更垂直的领域）
KEYWORD_GROUPS: List[tuple] = [
    (g["category"], g["keywords"]) for g in _config.get("keyword_groups", [])
]

# 内容类型分组：综合源条目区分“新闻资讯 / 技术方案”
# 命中技术方案关键词 → 技术方案；否则默认 新闻资讯
KIND_GROUPS: List[tuple] = [
    (g["kind"], g["keywords"]) for g in _config.get("kind_groups", [])
]

# 关注的开源仓库：repo -> 类别
GITHUB_REPOS: Dict[str, str] = {
    r["repo"]: r["category"] for r in _config.get("github_repos", [])
}

# HuggingFace Daily Papers / GitHub Trending 配置
HF_PAPERS_CONFIG: Dict[str, Any] = _config.get("huggingface_papers", {}) or {}
GITHUB_TRENDING_CONFIG: Dict[str, Any] = _config.get("github_trending", {}) or {}

_limits = _config.get("limits", {})

# 过去多少小时的资讯
HOURS_WINDOW: int = int(_limits.get("hours_window", 24))
# 每个源最多取多少条（可被单个源的 max_items 覆盖）
MAX_ITEMS_PER_SOURCE: int = int(_limits.get("max_items_per_source", 15))
# 每个类别最多保留多少条
MAX_ITEMS_PER_CATEGORY: int = int(_limits.get("max_items_per_category", 30))
# 交给大模型总结时，每个类别最多提供多少篇（避免 prompt 过长）
MAX_ITEMS_FOR_LLM_PER_CATEGORY: int = int(_limits.get("max_items_for_llm_per_category", 15))
# 标题相似度阈值：超过则认为是同一新闻（跨源去重）
TITLE_SIMILARITY_THRESHOLD: float = float(_limits.get("title_similarity_threshold", 0.82))


# ============================================================
# RSS / Atom 抓取
# ============================================================

def parse_rss_datetime(date_str: str) -> Optional[datetime]:
    """
    尝试解析 RSS/Atom 中的时间字段，失败则返回 None。
    """
    if not date_str:
        return None

    s = date_str.strip()

    # 常见格式：RFC 2822 / RFC 822
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # ISO8601（Atom 常见）
    try:
        iso = s
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"

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
    for link_el in entry.findall("{*}link"):
        rel = (link_el.get("rel") or "").strip().lower()
        href = (link_el.get("href") or "").strip()
        if not href:
            continue
        if rel in ("", "alternate"):
            return href
    return ""


def _clean_html(text: str) -> str:
    """去掉 HTML 标签，截断为短摘要。"""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:300]


def _sanitize_xml(text: str) -> str:
    """去掉 XML 非法控制字符，避免解析失败。"""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)


def _classify_by_keywords(title: str, summary: str) -> Optional[str]:
    """按关键词分组为综合源条目归类，未命中返回 None。"""
    text = f"{title} {summary}".lower()
    for category, keywords in KEYWORD_GROUPS:
        if any(kw in text for kw in keywords):
            return category
    return None


def _classify_kind(title: str, summary: str) -> str:
    """为综合源条目区分内容类型：命中技术方案关键词 → 技术方案，否则默认 新闻资讯。"""
    text = f"{title} {summary}".lower()
    for kind, keywords in KIND_GROUPS:
        if kind == "技术方案" and any(kw in text for kw in keywords):
            return "技术方案"
    return "新闻资讯"


def fetch_feed(
    url: str,
    source_name: str,
    category: Optional[str] = None,
    keyword_classify: bool = False,
    paper_filter: bool = False,
    max_items: Optional[int] = None,
    kind: Optional[str] = None,
    hours: int = HOURS_WINDOW,
) -> List[Dict]:
    """
    抓取单个 RSS/Atom 源，返回过去 hours 小时内的条目列表。

    - category 不为 None：条目归属该类别（垂直源）
    - keyword_classify=True：按关键词分组自动归类，未命中的条目丢弃（综合源）
    - paper_filter=True：按论文关键词过滤（arXiv 源）
    - max_items：该源单独限量（默认 MAX_ITEMS_PER_SOURCE）
    - kind：内容类型（新闻资讯 / 技术方案）；为 None 时综合源用 _classify_kind 自动判定
    """
    print(f"Fetching feed: {source_name} ({url})")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Failed to fetch {source_name}: {e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(_sanitize_xml(resp.text))
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
            pub_raw = (item.findtext("{*}date") or "").strip()

        pub_dt = parse_rss_datetime(pub_raw)
        if pub_dt is None:
            continue

        if not is_within_last_hours(pub_dt, hours):
            continue

        if not title or not link:
            continue

        # Google News 标题可能带多级 " - 来源" 后缀（如 " - CHOSUNBIZ - Chosunbiz"、
        # " - daily-sun.com"），循环剥离利于去重与阅读
        if "Google News" in source_name:
            while True:
                new = re.sub(r"\s+-\s+[^\s-][^-]{0,39}$", "", title).strip()
                new = re.sub(
                    r"\s+-\s+[A-Za-z0-9][\w\-.]*\.(?:com|net|org|co|io|ai|news|biz)$",
                    "",
                    new,
                ).strip()
                if new == title:
                    break
                title = new

        summary = _clean_html(item.findtext("description") or "")

        item_category = category
        item_kind = kind
        if keyword_classify:
            item_category = _classify_by_keywords(title, summary)
            if item_category is None:
                continue
            item_kind = _classify_kind(title, summary)
        if paper_filter:
            text = f"{title} {summary}".lower()
            if not any(kw in text for kw in PAPER_KEYWORDS):
                continue

        items.append(
            {
                "title": title,
                "link": link,
                "published_at": pub_dt,
                "source": source_name,
                "category": item_category,
                "kind": item_kind,
                "summary": summary,
            }
        )

    # 2) Atom: <entry>
    for entry in root.findall(".//{*}entry"):
        title = (entry.findtext("{*}title") or "").strip()
        href = _pick_atom_link(entry)
        updated_raw = (entry.findtext("{*}published") or "").strip()
        if not updated_raw:
            updated_raw = (entry.findtext("{*}updated") or "").strip()

        pub_dt = parse_rss_datetime(updated_raw)
        if pub_dt is None:
            continue

        if not is_within_last_hours(pub_dt, hours):
            continue

        if not title or not href:
            continue

        summary = _clean_html(entry.findtext("{*}summary") or "")

        item_category = category
        item_kind = kind
        if keyword_classify:
            item_category = _classify_by_keywords(title, summary)
            if item_category is None:
                continue
            item_kind = _classify_kind(title, summary)
        if paper_filter:
            text = f"{title} {summary}".lower()
            if not any(kw in text for kw in PAPER_KEYWORDS):
                continue

        items.append(
            {
                "title": title,
                "link": href,
                "published_at": pub_dt,
                "source": source_name,
                "category": item_category,
                "kind": item_kind,
                "summary": summary,
            }
        )

    items.sort(key=lambda x: x["published_at"], reverse=True)
    limit = max_items if max_items is not None else MAX_ITEMS_PER_SOURCE
    return items[:limit]


# ============================================================
# GitHub Releases 抓取
# ============================================================

def fetch_github_releases() -> List[Dict]:
    """
    抓取关注仓库过去 24 小时内发布的新 Release。
    """
    token = (os.environ.get("GITHUB_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: List[Dict] = []
    for repo, category in GITHUB_REPOS.items():
        print(f"Fetching releases: {repo}")
        try:
            resp = requests.get(
                f"https://api.github.com/repos/{repo}/releases?per_page=5",
                headers=headers,
                timeout=15,
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[WARN] Failed to fetch releases for {repo}: {e}", file=sys.stderr)
            continue

        for rel in resp.json():
            pub_raw = rel.get("published_at") or ""
            pub_dt = parse_rss_datetime(pub_raw)
            if pub_dt is None or not is_within_last_hours(pub_dt, HOURS_WINDOW):
                continue

            tag = rel.get("tag_name") or ""
            name = rel.get("name") or tag
            items.append(
                {
                    "title": f"{repo} 发布 {tag}" + (f"：{name}" if name and name != tag else ""),
                    "link": rel.get("html_url") or f"https://github.com/{repo}/releases",
                    "published_at": pub_dt,
                    "source": "GitHub Releases",
                    "category": category,
                    "kind": "新闻资讯",
                    "summary": _clean_html(rel.get("body") or ""),
                }
            )

    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items


# ============================================================
# HuggingFace Daily Papers 抓取（公开 API，无需鉴权）
# ============================================================

def fetch_hf_daily_papers() -> List[Dict]:
    """
    抓取 HuggingFace Daily Papers 社区热榜（按赞数排序的滚动榜单），
    取命中论文关键词的 Top N，不做时间窗过滤——热榜本身代表"当前最热"。
    """
    if not HF_PAPERS_CONFIG.get("enabled", False):
        return []
    max_items = int(HF_PAPERS_CONFIG.get("max_items", 8))
    use_kw_filter = bool(HF_PAPERS_CONFIG.get("filter_by_keywords", True))

    print("Fetching feed: HuggingFace Daily Papers (https://huggingface.co/api/daily_papers)")
    try:
        resp = requests.get(
            "https://huggingface.co/api/daily_papers", headers=HEADERS, timeout=20
        )
        resp.raise_for_status()
        papers = resp.json()
    except Exception as e:
        print(f"[WARN] Failed to fetch HuggingFace Daily Papers: {e}", file=sys.stderr)
        return []

    items: List[Dict] = []
    for entry in papers:
        paper = entry.get("paper") or {}
        # 榜单时间跨度可达数周，只用于展示，不参与时间窗过滤
        pub_dt = parse_rss_datetime(paper.get("publishedAt") or entry.get("publishedAt") or "")

        title = (entry.get("title") or paper.get("title") or "").strip()
        pid = (paper.get("id") or "").strip()
        if not title or not pid:
            continue

        summary = _clean_html(paper.get("summary") or entry.get("summary") or "")
        if use_kw_filter:
            text = f"{title} {summary}".lower()
            if not any(kw in text for kw in PAPER_KEYWORDS):
                continue

        upvotes = paper.get("upvotes")
        items.append(
            {
                "title": title + (f"（👍{upvotes}）" if upvotes else ""),
                "link": f"https://huggingface.co/papers/{pid}",
                "published_at": pub_dt or datetime.now(timezone.utc),
                "source": "HuggingFace Papers",
                "category": "论文",
                "summary": summary,
            }
        )

    # 按赞数降序，取 Top N
    def _upvotes(x: Dict) -> int:
        m = re.search(r"（👍(\d+)）", x["title"])
        return int(m.group(1)) if m else 0
    items.sort(key=_upvotes, reverse=True)
    return items[:max_items]


# ============================================================
# GitHub Trending 抓取（每日热榜，HTML 解析）
# ============================================================

def fetch_github_trending() -> List[Dict]:
    """
    抓取 GitHub Trending 每日热榜，独立输出为「开源项目」板块。
    默认不过滤关键词，直接取当日最火的 top N 仓库；
    配置 filter_by_keywords=true 时才按关键词过滤后再取前 N。
    热榜无发布时间，统一记为抓取时刻。
    """
    if not GITHUB_TRENDING_CONFIG.get("enabled", False):
        return []
    max_items = int(GITHUB_TRENDING_CONFIG.get("max_items", 10))
    filter_by_keywords = bool(GITHUB_TRENDING_CONFIG.get("filter_by_keywords", False))
    keywords = [str(k).lower() for k in GITHUB_TRENDING_CONFIG.get("keywords", [])]

    print("Fetching feed: GitHub Trending (https://github.com/trending?since=daily)")
    try:
        resp = requests.get(
            "https://github.com/trending?since=daily", headers=HEADERS, timeout=20
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[WARN] Failed to fetch GitHub Trending: {e}", file=sys.stderr)
        return []

    now = datetime.now(timezone.utc)
    items: List[Dict] = []
    for row in re.findall(r'<article class="Box-row">.*?</article>', resp.text, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([^"/]+/[^"/]+)"', row)
        if not m:
            continue
        repo = m.group(1)

        desc_m = re.search(r'<p class="col-9[^"]*">\s*(.*?)\s*</p>', row, re.S)
        desc = _clean_html(desc_m.group(1)) if desc_m else ""

        if filter_by_keywords and keywords:
            text = f"{repo} {desc}".lower()
            if not any(kw in text for kw in keywords):
                continue

        stars_m = re.search(r"([\d,]+)\s+stars today", row)
        stars_today = (stars_m.group(1) if stars_m else "").replace(",", "")
        title = f"{repo}" + (f"（今日 +{stars_today} stars）" if stars_today else "")

        items.append(
            {
                "title": title,
                "link": f"https://github.com/{repo}",
                "published_at": now,
                "source": "GitHub Trending",
                "category": "开源项目",
                "summary": desc,
            }
        )

    # 按“今日新增 star”降序取最火的前 N 名（页面顺序并非严格按涨幅排序）
    items.sort(
        key=lambda x: int(re.search(r"\+(\d+) stars", x["title"]).group(1))
        if re.search(r"\+(\d+) stars", x["title"])
        else 0,
        reverse=True,
    )
    return items[:max_items]


# ============================================================
# 去重与汇总
# ============================================================

def _normalize_title(title: str) -> str:
    """归一化标题：小写、去掉非字母数字/中日韩字符，用于相似度比较。"""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", (title or "").lower())


def _dedupe_and_sort(items: List[Dict]) -> List[Dict]:
    """
    去重：
    1) 完全相同的 (title, link)；
    2) 归一化后标题相同；
    3) 标题相似度超过阈值（跨源同一新闻的不同措辞/译文）。
    """
    kept: List[Dict] = []
    seen_keys = set()
    seen_norm = set()

    for item in items:
        key = (item.get("title"), item.get("link"))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        norm = _normalize_title(item.get("title") or "")
        if not norm or norm in seen_norm:
            continue

        # 与已保留条目做相似度比较（数量有限，O(n^2) 可接受）
        duplicate = False
        for k in kept:
            k_norm = _normalize_title(k.get("title") or "")
            if k_norm and difflib.SequenceMatcher(None, norm, k_norm).ratio() >= TITLE_SIMILARITY_THRESHOLD:
                duplicate = True
                break
        if duplicate:
            continue

        seen_norm.add(norm)
        kept.append(item)

    kept.sort(key=lambda x: x["published_at"], reverse=True)
    return kept


def collect_all_news() -> List[Dict]:
    """
    抓取所有数据源：垂直 RSS + 关键词归类的综合源 + arXiv 论文
    + GitHub Releases + HuggingFace Daily Papers + GitHub Trending。
    """
    all_items: List[Dict] = []

    for feed in VERTICAL_FEEDS:
        all_items.extend(
            fetch_feed(
                feed["url"],
                feed["name"],
                category=feed.get("category"),
                max_items=feed.get("max_items"),
                kind=feed.get("kind"),
            )
        )

    for feed in GENERIC_FEEDS:
        all_items.extend(
            fetch_feed(
                feed["url"],
                feed["name"],
                keyword_classify=True,
                max_items=feed.get("max_items"),
            )
        )

    # arXiv 分类 RSS：工作日更新，周末为空属正常
    for feed in ARXIV_FEEDS:
        all_items.extend(
            fetch_feed(
                feed["url"],
                feed["name"],
                category="论文",
                paper_filter=True,
                max_items=feed.get("max_items"),
            )
        )

    all_items.extend(fetch_github_releases())
    all_items.extend(fetch_hf_daily_papers())
    all_items.extend(fetch_github_trending())

    unique_items = _dedupe_and_sort(all_items)

    # 每个类别限量
    result: List[Dict] = []
    for category in CATEGORIES:
        result.extend([x for x in unique_items if x.get("category") == category][:MAX_ITEMS_PER_CATEGORY])

    return result


def build_raw_markdown(news_items: List[Dict]) -> str:
    """
    把资讯列表转成 Markdown 文本（不走大模型），用于 Server 酱的 desp。
    """
    if not news_items:
        return "过去 24 小时内，未从配置的资讯源中抓取到符合条件的内容。"

    lines = [f"过去 {HOURS_WINDOW} 小时资讯列表（UTC 时间）：", ""]
    for category in CATEGORIES:
        cat_items = [x for x in news_items if x.get("category") == category]
        if not cat_items:
            continue
        lines.append(f"## {CATEGORY_EMOJI.get(category, category)}")
        lines.append("")

        # 板块内按内容类型分组：新闻资讯 / 技术方案（论文/开源项目板块不分）
        kind_order = ["新闻资讯", "技术方案"]
        kind_labels = {
            "新闻资讯": "📰 新闻资讯",
            "技术方案": "🧠 算法技术方案",
        }
        grouped = False
        for k in kind_order:
            sub = [x for x in cat_items if (x.get("kind") or "") == k]
            if not sub:
                continue
            grouped = True
            lines.append(f"**{kind_labels[k]}**")
            lines.append("")
            for idx, item in enumerate(sub, start=1):
                pub_str = item["published_at"].astimezone(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
                lines.append(
                    f"{idx}. **{item['title']}**  \n"
                    f"   来源：{item['source']}  \n"
                    f"   时间：{pub_str}  \n"
                    f"   链接：{item['link']}"
                )
            lines.append("")
        if not grouped:
            # 兜底：条目无 kind 时平铺输出
            for idx, item in enumerate(cat_items, start=1):
                pub_str = item["published_at"].astimezone(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
                lines.append(
                    f"{idx}. **{item['title']}**  \n"
                    f"   来源：{item['source']}  \n"
                    f"   时间：{pub_str}  \n"
                    f"   链接：{item['link']}"
                )
            lines.append("")

    return "\n".join(lines)


def _render_articles_for_llm(news_items: List[Dict]) -> str:
    """
    将文章列表渲染给大模型作为“材料”，每篇给一个稳定编号。
    """
    lines = []
    for i, item in enumerate(news_items, start=1):
        aid = f"A{i:02d}"
        item["aid"] = aid
        pub_str = item["published_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        summary = (item.get("summary") or "")[:200]
        lines.append(
            f"{aid} | {item.get('category','未知')} | {item.get('kind') or '-'} | "
            f"{item.get('source','')} | {pub_str} | "
            f"{item.get('title','').strip()} | {summary} | {item.get('link','').strip()}"
        )
    return "\n".join(lines)


def summarize_with_deepseek(news_items: List[Dict]) -> Optional[str]:
    """
    使用 DeepSeek 对过去24小时的 AI/自动驾驶/机器人资讯做分板块汇总。
    需要环境变量：DEEPSEEK_API_KEY（必填）
    可选：DEEPSEEK_MODEL（默认 deepseek-chat）、DEEPSEEK_BASE_URL
    """
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        return None

    base_url = (os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    model = (os.environ.get("DEEPSEEK_MODEL") or "deepseek-chat").strip()

    # 每个类别限量后合并，避免 prompt 过长
    picked: List[Dict] = []
    for category in CATEGORIES:
        picked.extend(
            [x for x in news_items if x.get("category") == category][:MAX_ITEMS_FOR_LLM_PER_CATEGORY]
        )

    if not picked:
        return "过去 24 小时内未抓取到资讯，无法生成汇总。"

    material = _render_articles_for_llm(picked)
    now_dt_utc = datetime.now(timezone.utc)
    now_utc = now_dt_utc.strftime("%Y-%m-%d %H:%M UTC")
    report_date = now_dt_utc.strftime("%Y-%m-%d")

    system = (
        "你是一名自动驾驶与具身智能领域的技术情报分析师，长期跟踪 AI 大模型、自动驾驶、"
        "机器人/具身智能三大方向的技术进展、行业动态与前沿论文，"
        "为工程团队提供结构化的每日技术简报。请严格按指定格式输出中文 Markdown。\n"
        "\n"
        "【输出格式硬性要求，必须严格遵守】：\n"
        "1) 第一行必须是：**📅 YYYY-MM-DD AI·自动驾驶·机器人技术日报**（使用加粗，日期为当日日期）。\n"
        "2) 第二块必须是分隔线：---（单独一行）。\n"
        "3) 分隔线后第一块必须是“日报内容摘要”：用加粗标题 **📋 日报内容摘要** 开头，"
        "下面用 3–4 条编号要点（每条一行、一句话）概括当日整份日报最核心的内容"
        "（可跨板块归纳，如重大模型发布、关键技术突破、值得关注的开源项目/论文），作为导读。\n"
        "4) 日报内容摘要之后，必须依次出现且仅出现以下五个板块（用加粗文本作为板块标题，顺序固定）：\n"
        "   - **🤖 AI 大模型**\n"
        "   - **🚗 自动驾驶**\n"
        "   - **🦾 机器人与具身智能**\n"
        "   - **🔥 开源项目**\n"
        "   - **📄 论文速递**\n"
        "5) 每个板块标题下允许先写 1 段不带序号的“导语/总括”（可选，1–2 句），导语之后必须开始子组输出。\n"
        "6) **🤖 AI 大模型** / **🚗 自动驾驶** / **🦾 机器人与具身智能** 三个板块内必须按以下两个子组组织内容"
        "（子组标题用加粗，顺序固定）：\n"
        "   - **📰 新闻资讯**：行业动态、产品/模型发布、融资、合作、政策法规等\n"
        "   - **🧠 算法技术方案**：模型/算法/架构、训练推理、工程实践、技术解析、论文技术解读等\n"
        "   每个子组下用独立编号列表（各自从 1 开始，使用 Markdown 编号“1. 2. 3.”）；"
        "子组无材料则省略该子组；整个板块无任何材料时只写一行“今日无重要动态。”，不得编造内容。\n"
        "   **🔥 开源项目** 与 **📄 论文速递** 板块不分子组。\n"
        "7) 每条要点必须严格由三行组成（顺序固定）：\n"
        "   - 第 1 行：编号 + 加粗标题（例如：1. **端到端方案新进展：xxx**）\n"
        "   - 第 2 行：摘要（1–2 句中文），必须换行呈现，不要与标题同一行。\n"
        "   - 第 3 行：来源链接，格式为“🔗 [查看原文](链接)”，链接必须原样取自材料行最后一列的链接字段，严禁编造或改写。\n"
        "8) **🚗 自动驾驶** 板块应优先覆盖：端到端/世界模型/VLA 等技术路线进展、量产与 Robotaxi 落地、"
        "政策法规、供应链（芯片/激光雷达）动态。\n"
        "9) **📄 论文速递** 板块基于材料中 category=论文 的条目，每条要点第 1 行为论文中文译名（可在括号内保留英文原名），"
        "第 2 行为一句话概括其核心贡献，第 3 行为来源链接（arXiv/HF 论文页，取自材料链接字段）。\n"
        "10) **🔥 开源项目** 板块基于材料中 category=开源项目 的条目（GitHub Trending 当日最火仓库），"
        "按热度从高到低最多输出 10 条；每条要点第 1 行为仓库全名（格式：owner/repo，保留英文原名并加粗），"
        "第 2 行为该仓库的一句话中文简介并结合其 star 涨幅点出其为什么火，"
        "第 3 行为该仓库的 GitHub 链接（格式：🔗 [GitHub](链接)，取自材料链接字段）。\n"
        "11) 严禁编造链接/URL：正文与摘要版中的所有链接都必须原样取自材料行最后一列的链接字段，"
        "不得猜测、改写或伪造；不要在正文中出现 Axx 编号。\n"
        "12) 全文控制在约 1800–5000 中文字符以内，避免超长（含链接行）。\n"
        "13) 全文结束后，另起一行输出分隔标记 <<<DIGEST>>>（该标记必须单独占一行，标记前不留空行），"
        "随后输出“摘要版”：摘要版第一块必须同样是 **📋 日报内容摘要**（同样 3–4 条要点），"
        "之后每个板块最多 4 条、每条一行，每条以 “📰”（新闻资讯）或 “🧠”（算法技术方案）开头区分两类，"
        "格式：📰 **标题**：一句话概括 [原文](链接)，链接取自材料；"
        "但 **🔥 开源项目** 板块例外：最多输出 10 条、每条一行直接列仓库全名（格式 **owner/repo**）"
        "并附 GitHub 链接（格式：[GitHub](链接)）；"
        "摘要版不写导语、不写编号、无材料的板块省略；摘要版总长不超过 700 字符"
        "（微信推送超限时脚本会自动降级为“日报内容摘要+链接”）；"
        "不得包含 Axx 编号，不得编造链接。\n"
    )

    user = (
        f"当前时间（UTC）：{now_utc}\n"
        f"日报日期：{report_date}\n"
        f"请根据以下过去 {HOURS_WINDOW} 小时文章材料生成技术日报。材料格式为：编号 | 分类 | 类型(新闻资讯/技术方案/-) | 来源 | 时间 | 标题 | 摘要 | 链接。\n"
        f"强约束：第一行日期必须使用 {report_date}；所有链接必须原样取自材料链接字段，严禁编造链接；不要在正文中出现 Axx 编号。\n\n"
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
        "max_tokens": 2400,
        "stream": False,
    }

    # 简单重试机制
    max_retries = 2
    last_err: Optional[Exception] = None

    for attempt in range(1, max_retries + 2):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            return content.strip()
        except Exception as e:
            last_err = e
            print(
                f"[WARN] 第 {attempt} 次调用 DeepSeek 失败：{e}",
                file=sys.stderr,
            )
            if attempt <= max_retries:
                time.sleep(3)

    print(
        f"[WARN] DeepSeek 汇总多次失败，将回退为原始列表：{last_err}",
        file=sys.stderr,
    )
    return None


# 摘要分隔标记：LLM 输出“全文 <<<DIGEST>>> 摘要版”
DIGEST_SEPARATOR = "<<<DIGEST>>>"


def split_report_and_digest(content: str) -> Tuple[str, Optional[str]]:
    """
    拆分 LLM 输出为 (全文, 摘要版)。
    无分隔标记或某一部分为空时，摘要返回 None（推送回退为全文）。
    """
    if DIGEST_SEPARATOR in content:
        report, _, digest = content.partition(DIGEST_SEPARATOR)
        report = report.strip()
        digest = digest.strip()
        if report and digest:
            return report, digest
        if report:
            # 分隔标记后摘要为空：只取全文部分
            return report, None
    return content.strip(), None


def _extract_daily_summary(report: str) -> Optional[str]:
    """
    从全文头部提取“📋 日报内容摘要”块（含标题行，不含后续板块）。
    LLM 生成的摘要版若漏掉了摘要块，推送时用它补上。
    """
    m = re.search(
        r"(\*\*📋 日报内容摘要\*\*.*?)(?=\*\*🤖|\*\*🚗|\*\*🦾|\*\*🔥|\*\*📄)",
        report,
        re.S,
    )
    if not m:
        return None
    block = m.group(1).strip()
    return block or None


# ============================================================
# 日报归档：写入 docs/YYYY-MM-DD.md，自动维护 docs/index.md
# ============================================================

# 日报站点地址（GitHub Pages）：推送摘要里的“查看完整日报”链接指向这里。
# 可用环境变量 SITE_URL 覆盖（未开启 Pages 时可改为仓库地址等）。
SITE_URL = (os.environ.get("SITE_URL") or "https://xerifg.github.io/NewsDaily").rstrip("/")

# 推送正文的字节安全上限（UTF-8）。
# 微信服务号模板消息的 desp 约 1KB，超限微信端会把正文截断（实测截断点约 1000 字节）。
# 走 Server酱 企业微信等长内容通道时，可配置环境变量 PUSH_MAX_BYTES 调大。
MAX_PUSH_BYTES = int(os.environ.get("PUSH_MAX_BYTES") or "1000")


def _build_push_desp(digest: Optional[str], report: str, full_url: str) -> str:
    """
    组装推送内容，保证末尾“查看完整日报”链接完整、且不触发微信端截断：
    1) 正常路径：摘要版（以日报内容摘要开头）+ 链接；字节数在安全范围内直接推送。
    2) 超限降级：只推“日报内容摘要 + 链接”。
    3) 极端情况：摘要块也超限时按字节截断，链接始终完整保留。
    """
    suffix = f"\n\n📖 [查看完整日报]({full_url})"

    if digest:
        # 摘要版必须以“日报内容摘要”开头；LLM 漏输出时从全文提取补上
        if "📋 日报内容摘要" not in digest:
            summary_block = _extract_daily_summary(report)
            if summary_block:
                digest = f"{summary_block}\n\n{digest}"
        content = digest
        fallback = _extract_daily_summary(report) or digest
    else:
        content = report
        fallback = report

    if len((content + suffix).encode("utf-8")) <= MAX_PUSH_BYTES:
        return content + suffix

    # 超限：降级为“日报内容摘要 + 链接”
    if len((fallback + suffix).encode("utf-8")) <= MAX_PUSH_BYTES:
        return fallback + suffix

    # 仍超限：按字节截断，末尾链接保持完整
    # 预算要留足省略号(3字节)与 UTF-8 截断边界余量，保证结果不超上限
    budget = MAX_PUSH_BYTES - len(suffix.encode("utf-8")) - 12
    truncated = (
        fallback.encode("utf-8")[:budget].decode("utf-8", errors="ignore").rstrip()
    )
    return truncated + "…" + suffix


def archive_report(report_date: str, title: str, desp: str) -> Path:
    """
    把日报（未截断全文）归档为 Markdown，并刷新归档索引。
    """
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    path = DOCS_DIR / f"{report_date}.md"

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = f"# {title}\n\n> 生成时间：{now_utc}\n\n---\n\n"
    path.write_text(header + desp.rstrip() + "\n", encoding="utf-8")
    print(f"Archived report to {path}")

    _update_archive_index()
    return path


def _update_archive_index() -> None:
    """按日期倒序刷新 docs/index.md。"""
    files = sorted(
        (f for f in DOCS_DIR.glob("*.md") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem)),
        reverse=True,
    )
    lines = [
        "# 日报归档",
        "",
        "按日期倒序排列。开启 GitHub Pages（Settings → Pages → 分支选 main，目录选 /docs）后，本目录即可作为历史日报检索站点。",
        "",
    ]
    lines.extend(f"- [{f.stem}]({f.name})" for f in files)
    (DOCS_DIR / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# 推送渠道：抽象接口 + 渠道注册表
# 新增渠道：继承 PushChannel 实现 push()，并在 CHANNELS 注册，
# 然后把渠道名加进环境变量 PUSH_CHANNELS 即可。
# ============================================================

class PushChannel:
    """推送渠道基类。"""

    name = "base"

    def is_configured(self) -> bool:
        """返回该渠道所需的环境变量等是否就绪。"""
        return True

    def push(self, title: str, desp: str) -> None:
        """推送一条消息，失败应抛出异常。"""
        raise NotImplementedError


class ServerChanChannel(PushChannel):
    """Server 酱 Turbo API 推送到微信。需要环境变量 SERVERCHAN_SENDKEY。"""

    name = "serverchan"

    def is_configured(self) -> bool:
        return bool((os.environ.get("SERVERCHAN_SENDKEY") or "").strip())

    def push(self, title: str, desp: str) -> None:
        sendkey = (os.environ.get("SERVERCHAN_SENDKEY") or "").strip()
        url = f"https://sctapi.ftqq.com/{sendkey}.send"
        try:
            resp = requests.post(url, data={"title": title, "desp": desp}, timeout=15)
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

        code = data.get("code")
        msg = data.get("message") or data.get("msg")
        if code != 0:
            raise RuntimeError(f"Server 酱返回错误 code={code}, message={msg}")
        print(f"Server 酱推送成功: {msg}")


CHANNELS: Dict[str, type] = {
    ServerChanChannel.name: ServerChanChannel,
}


def get_push_channels() -> List[PushChannel]:
    """
    按环境变量 PUSH_CHANNELS（逗号分隔，默认 serverchan）实例化推送渠道。
    未配置/未知的渠道跳过并告警。
    """
    raw = (os.environ.get("PUSH_CHANNELS") or "serverchan").strip()
    names = [x.strip() for x in raw.split(",") if x.strip()]

    channels: List[PushChannel] = []
    for name in names:
        cls = CHANNELS.get(name)
        if cls is None:
            print(
                f"[WARN] 未知推送渠道 {name!r}，可用渠道：{', '.join(CHANNELS)}",
                file=sys.stderr,
            )
            continue
        ch = cls()
        if ch.is_configured():
            channels.append(ch)
        else:
            print(f"[WARN] 渠道 {name!r} 未配置（缺少所需环境变量），跳过", file=sys.stderr)
    return channels


# ============================================================
# 主流程
# ============================================================

def main() -> None:
    dry_run = "--dry-run" in sys.argv

    print("Collecting AI / autonomous driving / robotics news...")
    news_items = collect_all_news()
    print(f"Collected {len(news_items)} items within last {HOURS_WINDOW} hours.")
    for category in CATEGORIES:
        n = len([x for x in news_items if x.get("category") == category])
        print(f"  - {category}: {n} items")

    now_dt_utc = datetime.now(timezone.utc)
    report_date = now_dt_utc.strftime("%Y-%m-%d")

    llm_summary = summarize_with_deepseek(news_items)
    if llm_summary:
        report, digest = split_report_and_digest(llm_summary)
        title = f"AI·自动驾驶·机器人技术日报（{report_date}）"
    else:
        report = build_raw_markdown(news_items)
        digest = None
        title = f"AI·自动驾驶·机器人列表（最近 {HOURS_WINDOW} 小时）"

    if len(title) > 60:
        title = title[:57] + "..."

    # 归档保存未截断全文（推送渠道的长度限制只影响推送内容）
    if dry_run:
        print(f"\n[DRY RUN] Would archive to docs/{report_date}.md")
    else:
        archive_report(report_date, title, report)

    # 推送内容：摘要版超长时自动降级为“日报内容摘要 + 全文链接”，绕开微信端截断；
    # 无摘要版则回退为全文（同样受字节上限保护，链接始终完整）
    full_url = f"{SITE_URL}/{report_date}.html"
    desp_for_push = _build_push_desp(digest, report, full_url)

    if dry_run:
        print("\n===== DRY RUN: archive content =====")
        print(report)
        print("\n===== DRY RUN: push title =====")
        print(title)
        print("\n===== DRY RUN: push desp =====")
        print(desp_for_push)
        print("\n===== DRY RUN: push channels =====")
        print(", ".join(c.name for c in get_push_channels()) or "(none)")
        return

    channels = get_push_channels()
    if not channels:
        raise RuntimeError(
            "没有可用的推送渠道：请配置环境变量 PUSH_CHANNELS 及对应渠道所需的环境变量。"
        )

    failures = []
    for ch in channels:
        print(f"Pushing via {ch.name}...")
        try:
            ch.push(title, desp_for_push)
        except Exception as e:
            print(f"[WARN] 渠道 {ch.name} 推送失败：{e}", file=sys.stderr)
            failures.append(ch.name)

    if failures:
        raise RuntimeError(f"部分渠道推送失败：{', '.join(failures)}")
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
