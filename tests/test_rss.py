import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from unittest.mock import patch

from scripts import fetch_tech_news as news


class RssTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.docs = Path(self.temp.name)
        self.docs_patch = patch.object(news, "DOCS_DIR", self.docs)
        self.docs_patch.start()
        self.addCleanup(self.docs_patch.stop)
        self.site_patch = patch.object(news, "SITE_URL", "https://example.com/daily")
        self.site_patch.start()
        self.addCleanup(self.site_patch.stop)

    def channel(self):
        return ET.parse(self.docs / "feed.xml").getroot().find("channel")

    def test_archive_generates_full_html_and_valid_xml(self):
        body = ('## 新闻 & 技术\n\n**重点** [来源](https://example.com/?a=1&b=2)'
                '\n\n中文 🤖 <测试> ]]>\n\n' + '完整正文。' * 1000 + '\n\n末尾内容')
        news.archive_report("2026-09-16", "日报 & <AI>", body)
        channel = self.channel()
        self.assertEqual(channel.findtext("link"), "https://example.com/daily/")
        atom_link = channel.find("{http://www.w3.org/2005/Atom}link")
        self.assertEqual(atom_link.attrib["href"], "https://example.com/daily/feed.xml")
        item = channel.find("item")
        self.assertEqual(item.findtext("title"), "日报 & <AI>")
        self.assertEqual(item.findtext("guid"), "https://example.com/daily/2026-09-16.html")
        self.assertEqual(item.find("guid").attrib["isPermaLink"], "true")
        html = item.findtext("description")
        self.assertIn("<strong>重点</strong>", html)
        self.assertIn('href="https://example.com/?a=1&amp;b=2"', html)
        self.assertIn("中文 🤖", html)
        self.assertIn("末尾内容", html)
        self.assertNotIn('title:', html)
        self.assertIn("2026-09-16.html", (self.docs / "index.md").read_text(encoding="utf-8"))
        self.assertEqual(parsedate_to_datetime(item.findtext("pubDate")).tzinfo, timezone.utc)

    def test_recent_30_reports_sorted_and_dates_preserved(self):
        for offset in range(35):
            day = (datetime(2026, 8, 1) + timedelta(days=offset)).strftime("%Y-%m-%d")
            (self.docs / f"{day}.md").write_text(
                f"# 日报\n\n> 生成时间：{day} 01:23 UTC\n\n正文", encoding="utf-8",
            )
        (self.docs / "index.md").write_text("# 归档", encoding="utf-8")
        (self.docs / "notes.md").write_text("# 笔记", encoding="utf-8")
        news._update_rss_feed()
        items = self.channel().findall("item")
        self.assertEqual(len(items), 30)
        links = [item.findtext("link") for item in items]
        self.assertEqual(links, sorted(links, reverse=True))
        self.assertTrue(links[-1].endswith("2026-08-06.html"))
        self.assertEqual(items[0].findtext("title"), "技术日报（2026-09-04）")
        self.assertEqual(parsedate_to_datetime(items[0].findtext("pubDate")),
                         datetime(2026, 9, 4, 1, 23, tzinfo=timezone.utc))
        before = (self.docs / "feed.xml").read_bytes()
        news._update_rss_feed()
        self.assertEqual(before, (self.docs / "feed.xml").read_bytes())

    def test_same_day_rewrite_keeps_guid_without_duplicate(self):
        news.archive_report("2026-09-16", "日报", "原始内容")
        guid = self.channel().findtext("item/guid")
        news.archive_report("2026-09-16", "修订日报", "更新内容")
        items = self.channel().findall("item")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].findtext("guid"), guid)
        self.assertIn("更新内容", items[0].findtext("description"))

    def test_legacy_report_without_timestamp_uses_report_date(self):
        (self.docs / "2026-09-16.md").write_text("# 历史日报\n\n正文", encoding="utf-8")
        news._update_rss_feed()
        self.assertEqual(parsedate_to_datetime(self.channel().findtext("item/pubDate")),
                         datetime(2026, 9, 16, tzinfo=timezone.utc))

    def test_empty_archive_produces_valid_feed(self):
        news._update_rss_feed()
        self.assertEqual(self.channel().findall("item"), [])
        self.assertTrue(self.channel().findtext("title"))

    def test_rss_only_skips_news_collection_and_push(self):
        with patch.object(news.sys, "argv", ["fetch_tech_news.py", "--rss-only"]), \
                patch.object(news, "collect_all_news") as collect, \
                patch.object(news, "get_push_channels") as channels:
            news.main()
        self.assertTrue((self.docs / "feed.xml").exists())
        collect.assert_not_called()
        channels.assert_not_called()

    def test_rss_only_dry_run_does_not_write(self):
        with patch.object(news.sys, "argv", ["fetch_tech_news.py", "--rss-only", "--dry-run"]):
            news.main()
        self.assertFalse((self.docs / "feed.xml").exists())


if __name__ == "__main__":
    unittest.main()
