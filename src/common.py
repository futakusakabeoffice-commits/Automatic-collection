"""各スクリプトで共通して使う設定と小さな関数。"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# プロジェクトのルート（src/ の1つ上）
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

# レポートの日付は日本時間で決める
JST = timezone(timedelta(hours=9))


def today_jst() -> str:
    """今日の日付（日本時間）を YYYY-MM-DD で返す。"""
    return datetime.now(JST).strftime("%Y-%m-%d")


def save_json(name: str, payload: dict) -> Path:
    """data/ に JSON を保存し、保存先のパスを返す。"""
    DATA_DIR.mkdir(exist_ok=True)
    path = DATA_DIR / name
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_json(name: str):
    """data/ の JSON を読む。無ければ None を返す。"""
    path = DATA_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
