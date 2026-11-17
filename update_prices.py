"""Crawl 1 lần và ghi vào data/prices.json. Dùng chung cho Flask (app.py) và GitHub Actions.

Chạy tay:  python update_prices.py
Thoát với mã 1 nếu lỗi (GitHub sẽ gửi email báo workflow thất bại).
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from crawler import crawl

BASE = Path(__file__).resolve().parent
DATA_FILE = BASE / "data" / "prices.json"
VN_TZ = timezone(timedelta(hours=7))  # giờ Việt Nam, đúng cả khi chạy trên server GitHub (UTC)

log = logging.getLogger("update")


def today() -> str:
    return datetime.now(VN_TZ).strftime("%Y-%m-%d")


def load_rows() -> list:
    if not DATA_FILE.exists():
        return []
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_rows(rows: list) -> None:
    DATA_FILE.parent.mkdir(exist_ok=True)
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, DATA_FILE)


def update() -> tuple[dict, list]:
    """Crawl, thay bản ghi của hôm nay (nếu có), lưu file. Trả về (bản ghi mới, các ngày cũ)."""
    rec = crawl()
    rec["date"] = today()
    rec["fetchedAt"] = datetime.now(VN_TZ).isoformat(timespec="minutes")
    old = [r for r in load_rows() if r.get("date") != rec["date"]]
    save_rows(sorted(old + [rec], key=lambda r: r["date"]))
    return rec, old


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        rec, old = update()
    except Exception as e:
        log.exception("Crawl lỗi: %s", e)
        sys.exit(1)
    msg = f"Rẻ nhất hôm nay {rec['cheapest']:,} ₫ ({rec.get('cheapestAirline')})"
    if old:
        prev = old[-1]["cheapest"]
        msg += f", so với {old[-1]['date']}: {rec['cheapest'] - prev:+,} ₫"
    print(msg)
    # Ghi tóm tắt lên trang kết quả của GitHub Actions
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### {msg}\n")
