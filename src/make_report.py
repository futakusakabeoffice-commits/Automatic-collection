"""data/ に保存した YouTube とニュースの結果から、reports/YYYY-MM-DD.md を作る。

数字は取得できたものだけを使い、取得できなかったものは「—」や「取得失敗」と書く。
「動画ネタ案」は機械的に作った下書きなので、/daily-report の手順で見直す。

使い方:
    python src/make_report.py
"""

import re
import sys
from statistics import median

from common import REPORTS_DIR, load_json, today_jst

# タイトルからテーマを数えるための語（専門テーマが決まったら追加する）
THEME_WORDS = [
    "ChatGPT", "Claude", "Gemini", "Copilot", "エージェント", "プロンプト", "自動化",
    "Excel", "資料", "スライド", "議事録", "メール", "無料", "副業", "最新", "使い方",
]


# ---------- 表示用の小さな関数 ----------

def fmt_num(n) -> str:
    return f"{n:,}" if isinstance(n, int) else "—"


def fmt_ratio(r) -> str:
    return f"{r:.2f}倍" if isinstance(r, (int, float)) else "—"


def fmt_duration(sec) -> str:
    if not isinstance(sec, int):
        return "—"
    m, s = divmod(sec, 60)
    return f"{m}分{s:02d}秒" if m else f"{s}秒"


def cell(text) -> str:
    """表のセルを壊さないように | と改行を置き換える。"""
    return str(text or "—").replace("|", "｜").replace("\n", " ")


# ---------- 各セクション ----------

def video_table(videos: list[dict], limit: int = 10) -> list[str]:
    if not videos:
        return ["（該当する動画を取得できませんでした）"]
    lines = [
        "| # | タイトル | チャンネル | 再生数 | 登録者数 | 伸び率 | URL |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for i, v in enumerate(videos[:limit], 1):
        lines.append(
            f"| {i} | {cell(v['title'])} | {cell(v['channel'])} | {fmt_num(v['views'])} | "
            f"{fmt_num(v['subscribers'])} | {fmt_ratio(v['growth_ratio'])} | {v['url']} |"
        )
    return lines


def find_patterns(videos: list[dict]) -> list[str]:
    """上位動画のタイトル・テーマ・長さの傾向を、数えられた事実だけで最大3つ出す。"""
    top = videos[:20]
    if len(top) < 3:
        return ["（動画が少ないため、傾向は出せませんでした）"]
    n = len(top)
    titles = [v["title"] or "" for v in top]
    patterns = []

    # タイトルの付け方
    bracket = sum(1 for t in titles if re.search(r"[【】\[\]]", t))
    number = sum(1 for t in titles if re.search(r"\d", t))
    avg_len = round(sum(len(t) for t in titles) / n)
    patterns.append(
        f"**タイトル**：上位{n}本のうち【】などの括弧入りが{bracket}本、数字入りが{number}本。"
        f"平均{avg_len}文字"
    )

    # テーマ（よく出る語の上位3つ）
    counts = {w: sum(1 for t in titles if w.lower() in t.lower()) for w in THEME_WORDS}
    common = [f"「{w}」{c}本" for w, c in sorted(counts.items(), key=lambda x: -x[1]) if c >= 2][:3]
    if common:
        patterns.append(f"**テーマ**：タイトルによく出る語は {'、'.join(common)}")

    # 長さ
    durations = [v["duration_seconds"] for v in top if isinstance(v.get("duration_seconds"), int)]
    if durations:
        patterns.append(
            f"**長さ**：中央値 {fmt_duration(int(median(durations)))}"
            f"（最短 {fmt_duration(min(durations))}／最長 {fmt_duration(max(durations))}）"
        )
    return patterns[:3]


def news_lines(articles: list[dict], limit: int = 5) -> list[str]:
    if not articles:
        return ["（過去24時間の記事を取得できませんでした）"]
    lines = []
    for a in articles[:limit]:
        tag = "【公式】" if a["official"] else ""
        summary = a["summary"] or "（要約なし）"
        lines.append(f"- {tag}**{a['title']}**（{a['source']}）")
        lines.append(f"  - {summary}")
        lines.append(f"  - {a['url']}")
    return lines


def idea_drafts(normal: list[dict], short: list[dict], articles: list[dict]) -> list[str]:
    """参考元が実在するものだけでネタ案の下書きを作る。参考元が無ければ作らない。"""
    refs = []
    for a in articles[:2]:
        refs.append(("news", a))
    for v in (normal[:2] + short[:1]):
        refs.append(("video", v))
    refs = refs[:3]
    if not refs:
        return ["（参考にできる動画・ニュースを取得できなかったため、ネタ案は作れませんでした）"]

    lines = []
    for i, (kind, ref) in enumerate(refs, 1):
        if kind == "news":
            title = f"【速報】{ref['title']}は仕事でどう使える？明日試せる使い方3つ"
            hook = f"「{ref['source']}が発表したこれ、実は明日の資料作りがラクになります」"
            source = f"ニュース：{ref['title']} {ref['url']}"
        else:
            title = f"{ref['title']}を会社員向けに試してみた（実務での使い方）"
            hook = "「再生数が伸びているこの使い方、あなたの仕事ならこう使えます」"
            source = (
                f"動画：{ref['title']}（{ref['channel']}、再生数{fmt_num(ref['views'])}、"
                f"伸び率{fmt_ratio(ref['growth_ratio'])}） {ref['url']}"
            )
        lines += [
            f"### ネタ案{i}",
            f"- **タイトル案**：{title}",
            f"- **冒頭3秒で言うこと**：{hook}",
            f"- **参考にした動画またはニュース**：{source}",
            "",
        ]
    return lines


def summary_lines(normal, short, articles, errors) -> list[str]:
    """今日の要点（3行以内）。"""
    lines = []
    if normal:
        v = normal[0]
        lines.append(f"- 通常動画の伸び率1位は「{v['title']}」（{fmt_ratio(v['growth_ratio'])}）")
    elif short:
        v = short[0]
        lines.append(f"- ショートの伸び率1位は「{v['title']}」（{fmt_ratio(v['growth_ratio'])}）")
    if articles:
        official = [a for a in articles if a["official"]]
        a = (official or articles)[0]
        lines.append(f"- 注目ニュース：{a['title']}（{a['source']}）")
    if errors:
        lines.append(f"- 取得に失敗した項目が{len(errors)}件あります（末尾参照）")
    return lines[:3] or ["- 今日は取得できたデータがありませんでした"]


# ---------- 組み立て ----------

def build_report(date: str) -> str:
    yt = load_json(f"youtube_{date}.json")
    news = load_json(f"news_{date}.json")
    errors: list[str] = []

    if yt is None:
        errors.append(f"YouTube: data/youtube_{date}.json が無いため、動画のデータがありません（youtube_trends.py 未実行か失敗）")
        normal, short = [], []
    else:
        normal, short = yt.get("normal_top", []), yt.get("short_top", [])
        errors += yt.get("errors", [])
        if yt.get("excluded_no_ratio"):
            errors.append(f"YouTube: 登録者数が非公開などで伸び率を計算できない動画 {yt['excluded_no_ratio']}本はランキングから除外")

    if news is None:
        errors.append(f"ニュース: data/news_{date}.json が無いため、ニュースのデータがありません（news_feed.py 未実行か失敗）")
        articles = []
    else:
        articles = news.get("articles", [])
        errors += news.get("errors", [])

    out = [f"# AIリサーチ日報 {date}", ""]
    out += ["## 1. 今日の要点", *summary_lines(normal, short, articles, errors), ""]
    out += ["## 2. 伸びている動画 TOP10", "", "伸び率 = 再生数 ÷ チャンネル登録者数（過去7日以内の投稿）", ""]
    out += ["### 通常動画", *video_table(normal), ""]
    out += ["### ショート動画（60秒以下）", *video_table(short), ""]
    out += ["## 3. 伸びている動画に共通するパターン", *[f"- {p}" for p in find_patterns(normal or short)], ""]
    out += ["## 4. 重要ニュース", *news_lines(articles), ""]
    out += ["## 5. 動画ネタ案3つ", "", *idea_drafts(normal, short, articles)]
    if errors:
        out += ["", "---", "", "## 取得に失敗した内容", *[f"- {e}" for e in errors], ""]
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    date = today_jst()
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"{date}.md"
    path.write_text(build_report(date), encoding="utf-8")
    print(f"レポートを作成しました: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
