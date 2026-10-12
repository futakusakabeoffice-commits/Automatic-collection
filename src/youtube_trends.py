"""YouTube Data API v3 で、過去7日以内に投稿された生成AI関連の動画を集める。

結果は data/youtube_YYYY-MM-DD.json に保存する。
一部のキーワードやAPI呼び出しが失敗しても止まらず、取れた分だけ保存する。

使い方:
    python src/youtube_trends.py
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

from common import ROOT, save_json, today_jst

API_BASE = "https://www.googleapis.com/youtube/v3"

# 検索キーワード（専門テーマが決まったらここに追加する）
KEYWORDS = ["生成AI", "ChatGPT", "Claude", "AI 仕事術"]

# 1キーワードあたりの検索件数（search.list の上限は50）
SEARCH_MAX_RESULTS = 50

# ランキングに残す本数（ショート・通常それぞれ）
TOP_N = 20

# ショート動画とみなす長さ（秒）
SHORT_MAX_SECONDS = 60

# APIのユニット消費量（公式の目安）
UNIT_SEARCH = 100
UNIT_LIST = 1
DAILY_QUOTA = 10000

TIMEOUT = 20


def estimate_units(num_keywords: int) -> int:
    """実行前に消費ユニットの見積もりを出す。

    search.list は1回100ユニット。videos.list と channels.list は
    50件ごとに1ユニットなので、最大件数で多めに見積もる。
    """
    max_videos = num_keywords * SEARCH_MAX_RESULTS
    list_calls = -(-max_videos // 50)  # 切り上げ
    return num_keywords * UNIT_SEARCH + list_calls * UNIT_LIST * 2


def parse_duration(iso: str):
    """ISO 8601 の長さ（例: PT1H2M3S）を秒に直す。読めなければ None。"""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return None
    d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def to_int(value):
    """APIの数値（文字列）を int に。非公開などで無い場合は None のままにする。"""
    return int(value) if value is not None else None


def api_get(endpoint: str, params: dict) -> dict:
    resp = requests.get(f"{API_BASE}/{endpoint}", params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        # APIのエラーメッセージを取り出して分かりやすくする
        try:
            msg = resp.json()["error"]["message"]
        except Exception:
            msg = resp.text[:200]
        raise RuntimeError(f"{endpoint} が失敗しました（HTTP {resp.status_code}）: {msg}")
    return resp.json()


def search_videos(api_key: str, keyword: str, published_after: str) -> list[str]:
    data = api_get(
        "search",
        {
            "key": api_key,
            "part": "id",
            "q": keyword,
            "type": "video",
            "order": "viewCount",
            "publishedAfter": published_after,
            "regionCode": "JP",
            "relevanceLanguage": "ja",
            "maxResults": SEARCH_MAX_RESULTS,
        },
    )
    return [item["id"]["videoId"] for item in data.get("items", [])]


def chunks(items: list, size: int = 50):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_videos(api_key: str, video_ids: list[str]) -> list[dict]:
    videos = []
    for chunk in chunks(video_ids):
        data = api_get(
            "videos",
            {"key": api_key, "part": "snippet,statistics,contentDetails", "id": ",".join(chunk)},
        )
        videos.extend(data.get("items", []))
    return videos


def fetch_channels(api_key: str, channel_ids: list[str]) -> dict:
    """チャンネルID → 登録者数（非公開なら None）の辞書を返す。"""
    result = {}
    for chunk in chunks(channel_ids):
        data = api_get("channels", {"key": api_key, "part": "statistics", "id": ",".join(chunk)})
        for item in data.get("items", []):
            stats = item.get("statistics", {})
            hidden = stats.get("hiddenSubscriberCount", False)
            result[item["id"]] = None if hidden else to_int(stats.get("subscriberCount"))
    return result


def build_record(video: dict, subscribers) -> dict:
    snippet = video.get("snippet", {})
    stats = video.get("statistics", {})
    seconds = parse_duration(video.get("contentDetails", {}).get("duration"))
    views = to_int(stats.get("viewCount"))
    # 登録者数が非公開・0 のときは伸び率を計算しない（推測で埋めない）
    ratio = round(views / subscribers, 2) if views is not None and subscribers else None
    return {
        "video_id": video["id"],
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle"),
        "channel_id": snippet.get("channelId"),
        "url": f"https://www.youtube.com/watch?v={video['id']}",
        "published_at": snippet.get("publishedAt"),
        "views": views,
        "likes": to_int(stats.get("likeCount")),
        "comments": to_int(stats.get("commentCount")),
        "subscribers": subscribers,
        "duration_seconds": seconds,
        "is_short": seconds is not None and seconds <= SHORT_MAX_SECONDS,
        "growth_ratio": ratio,
        "language": snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage"),
    }


def is_japanese(record: dict) -> bool:
    """日本語の動画かどうか。言語情報があればそれを、無ければタイトルの文字で判定する。"""
    lang = record.get("language")
    if lang:
        return lang.lower().startswith("ja")
    return bool(re.search(r"[぀-ヿ]", record.get("title") or ""))


def rank(records: list[dict]) -> list[dict]:
    ranked = [r for r in records if r["growth_ratio"] is not None]
    ranked.sort(key=lambda r: r["growth_ratio"], reverse=True)
    return ranked[:TOP_N]


def main() -> int:
    load_dotenv(ROOT / ".env")
    errors: list[str] = []
    now = datetime.now(timezone.utc)
    published_after = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "date": today_jst(),
        "fetched_at": now.isoformat(),
        "keywords": KEYWORDS,
        "estimated_units": estimate_units(len(KEYWORDS)),
        "normal_top": [],
        "short_top": [],
        "excluded_no_ratio": 0,
        "errors": errors,
    }
    print(f"消費ユニットの見積もり: 約{payload['estimated_units']} / 1日の上限 {DAILY_QUOTA}")

    api_key = os.getenv("YOUTUBE_API_KEY", "").strip()
    if not api_key or api_key == "ここにキーを貼る":
        errors.append("YouTube: .env に YOUTUBE_API_KEY が設定されていないため、動画を取得できませんでした")
        path = save_json(f"youtube_{payload['date']}.json", payload)
        print(errors[-1])
        print(f"保存しました: {path}")
        # 後続のスクリプトを止めないよう、失敗は JSON に記録して正常終了する
        return 0

    # 1. キーワードごとに検索（1つ失敗しても次へ進む）
    video_ids: list[str] = []
    for kw in KEYWORDS:
        try:
            ids = search_videos(api_key, kw, published_after)
            print(f"検索「{kw}」: {len(ids)}件")
            video_ids.extend(i for i in ids if i not in video_ids)
        except Exception as e:
            errors.append(f"YouTube検索「{kw}」: {e}")

    # 2. 動画の詳細とチャンネル登録者数を取得
    records: list[dict] = []
    if video_ids:
        try:
            videos = fetch_videos(api_key, video_ids)
            channel_ids = sorted({v["snippet"]["channelId"] for v in videos})
            try:
                subs = fetch_channels(api_key, channel_ids)
            except Exception as e:
                errors.append(f"YouTubeチャンネル情報: {e}")
                subs = {}
            records = [build_record(v, subs.get(v["snippet"]["channelId"])) for v in videos]
        except Exception as e:
            errors.append(f"YouTube動画詳細: {e}")

    # 3. 日本語の動画に絞って、ショートと通常に分けて並べる
    records = [r for r in records if is_japanese(r)]
    payload["excluded_no_ratio"] = sum(1 for r in records if r["growth_ratio"] is None)
    payload["normal_top"] = rank([r for r in records if not r["is_short"]])
    payload["short_top"] = rank([r for r in records if r["is_short"]])

    path = save_json(f"youtube_{payload['date']}.json", payload)
    print(f"通常動画 {len(payload['normal_top'])}本 / ショート {len(payload['short_top'])}本")
    for err in errors:
        print(f"[失敗] {err}")
    print(f"保存しました: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
