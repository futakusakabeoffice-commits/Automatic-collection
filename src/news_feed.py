"""公式ブログとAIニュースサイトから、過去24時間の記事を集める。

結果は data/news_YYYY-MM-DD.json に保存する。
各ソースは毎回「実際に取得でき、記事が読めるか」を確認してから使い、
取得できなかったソースは失敗として記録する（URLを推測で使い続けない）。

使い方:
    python src/news_feed.py           # ニュースを集める
    python src/news_feed.py --check   # 各ソースが取得できるかだけ確認する
"""

import re
import sys
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import feedparser
import requests

from common import save_json, today_jst

TIMEOUT = 20
HEADERS = {"User-Agent": "ai-research-bot/1.0 (+personal research)"}
HOURS = 24

# RSSのソース一覧。official=True は公式発表で、レポートで優先する。
# URLは毎回取得して確認し、読めなかったものは失敗として記録する。追加するときは --check で確認する。
RSS_SOURCES = [
    {"name": "OpenAI News", "url": "https://openai.com/news/rss.xml", "official": True},
    # 2026-10-12 ブラウザで取得できることを確認（旧URL /technology/ai/rss/ から移転済みのため新URLを使う）
    {"name": "Google AI Blog", "url": "https://blog.google/innovation-and-ai/technology/ai/rss/", "official": True},
    {"name": "Google Gemini Blog", "url": "https://blog.google/products/gemini/rss/", "official": True},
    {"name": "ITmedia AI+", "url": "https://rss.itmedia.co.jp/rss/2.0/aiplus.xml", "official": False},
]

# Anthropic は公式RSSが無いため、公式のサイトマップ（robots.txt で案内されている）から
# /news/ の記事を探し、各記事ページの公開日時（article:published_time）で絞り込む。
ANTHROPIC_SITEMAP = "https://www.anthropic.com/sitemap.xml"


def fetch(url: str) -> requests.Response:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp


def short_error(e: Exception) -> str:
    """例外メッセージを1行・短めにする。"""
    if isinstance(e, requests.exceptions.ConnectionError):
        return "接続できませんでした（ネットワークまたはURLを確認）"
    if isinstance(e, requests.exceptions.Timeout):
        return f"{TIMEOUT}秒以内に応答がありませんでした"
    if isinstance(e, requests.exceptions.HTTPError):
        return f"HTTP {e.response.status_code}"
    text = str(e).splitlines()[0] if str(e) else e.__class__.__name__
    return text[:160]


def clean_text(html: str, limit: int = 200) -> str:
    text = re.sub(r"<[^>]+>", "", html or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def entry_time(entry):
    """RSSの記事から公開日時（UTC）を取り出す。無ければ None。"""
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc)
    return None


def read_rss(source: dict, since: datetime) -> list[dict]:
    resp = fetch(source["url"])
    feed = feedparser.parse(resp.content)
    if not feed.entries:
        raise RuntimeError("RSSとして読めませんでした（記事が0件）")
    articles = []
    for entry in feed.entries:
        published = entry_time(entry)
        # 日時が無い記事は「過去24時間」か判断できないので入れない
        if published is None or published < since:
            continue
        articles.append(
            {
                "source": source["name"],
                "official": source["official"],
                "title": entry.get("title", "").strip(),
                "url": entry.get("link"),
                "published_at": published.isoformat(),
                "summary": clean_text(entry.get("summary", "")),
            }
        )
    return articles


def meta(html: str, prop: str):
    m = re.search(rf'<meta[^>]+(?:property|name)="{re.escape(prop)}"[^>]+content="([^"]*)"', html)
    return m.group(1) if m else None


def read_anthropic(since: datetime) -> list[dict]:
    resp = fetch(ANTHROPIC_SITEMAP)
    root = ElementTree.fromstring(resp.content)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    # まずサイトマップの更新日時で候補を絞る（公開日は更新日以前なので取りこぼさない）
    candidates = []
    for url in root.findall("sm:url", ns):
        loc = url.findtext("sm:loc", default="", namespaces=ns)
        lastmod = url.findtext("sm:lastmod", default="", namespaces=ns)
        if "/news/" not in loc or not lastmod:
            continue
        if datetime.fromisoformat(lastmod.replace("Z", "+00:00")) >= since:
            candidates.append(loc)

    articles = []
    for loc in candidates:
        html = fetch(loc).text
        published = meta(html, "article:published_time")
        if not published:
            continue
        published_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published_dt < since:
            continue  # 古い記事の更新なので対象外
        articles.append(
            {
                "source": "Anthropic News",
                "official": True,
                "title": meta(html, "og:title") or loc,
                "url": loc,
                "published_at": published_dt.isoformat(),
                "summary": clean_text(meta(html, "og:description") or ""),
            }
        )
    return articles


def check_sources() -> int:
    """各ソースが実際に取得できるかを表示する。"""
    ok_all = True
    for source in RSS_SOURCES:
        try:
            feed = feedparser.parse(fetch(source["url"]).content)
            status = f"OK（{len(feed.entries)}件）" if feed.entries else "NG（RSSとして読めない）"
            ok_all &= bool(feed.entries)
        except Exception as e:
            status = f"NG（{short_error(e)}）"
            ok_all = False
        print(f"{source['name']}: {status}  {source['url']}")
    try:
        fetch(ANTHROPIC_SITEMAP)
        print(f"Anthropic News: OK  {ANTHROPIC_SITEMAP}")
    except Exception as e:
        print(f"Anthropic News: NG（{short_error(e)}）  {ANTHROPIC_SITEMAP}")
        ok_all = False
    return 0 if ok_all else 1


def main() -> int:
    if "--check" in sys.argv:
        return check_sources()

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=HOURS)
    articles: list[dict] = []
    errors: list[str] = []
    ok_sources: list[str] = []

    try:
        found = read_anthropic(since)
        articles.extend(found)
        ok_sources.append("Anthropic News")
        print(f"Anthropic News: {len(found)}件")
    except Exception as e:
        errors.append(f"ニュース「Anthropic News」: {short_error(e)}")

    for source in RSS_SOURCES:
        try:
            found = read_rss(source, since)
            articles.extend(found)
            ok_sources.append(source["name"])
            print(f"{source['name']}: {len(found)}件")
        except Exception as e:
            errors.append(f"ニュース「{source['name']}」（{source['url']}）: {short_error(e)}")

    # 公式発表を先に、その中では新しい順
    articles.sort(key=lambda a: a["published_at"], reverse=True)
    articles.sort(key=lambda a: not a["official"])  # 安定ソートなので新しい順は保たれる

    payload = {
        "date": today_jst(),
        "fetched_at": now.isoformat(),
        "since": since.isoformat(),
        "ok_sources": ok_sources,
        "articles": articles,
        "errors": errors,
    }
    path = save_json(f"news_{payload['date']}.json", payload)
    for err in errors:
        print(f"[失敗] {err}")
    print(f"記事 {len(articles)}件を保存しました: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
