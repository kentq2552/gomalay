"""Rà giá vé khứ hồi HAN <-> KUL cho nhiều cặp ngày (mỗi cuối tuần Thứ 6 -> CN, các dịp lễ).

Chạy:  .venv\\Scripts\\python.exe scan_dates.py
Kết quả ghi vào data/scan.json (index.html đọc để vẽ bảng so sánh).
Chỉ đọc giá "từ ... đồng" ở danh sách chiều đi (đã là giá khứ hồi), không bấm chọn chuyến,
nên nhanh (~10 giây/cặp ngày). Mỗi lần chạy nghỉ vài giây giữa các lượt để không bị Google chặn.
"""
import json
import logging
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

import crawler

BASE = Path(__file__).resolve().parent
OUT = BASE / "data" / "scan.json"
VN_TZ = timezone(timedelta(hours=7))

HOLIDAYS = [
    ("Giỗ Tổ Hùng Vương", "2027-04-16", "2027-04-18"),
    ("30/4 – 1/5", "2027-04-30", "2027-05-02"),
    ("Quốc khánh 2/9", "2027-09-02", "2027-09-05"),
]
LAST_DATE = date(2027, 10, 15)  # Google Flights chỉ bán vé khoảng 11 tháng tới

log = logging.getLogger("scan")


def date_pairs():
    pairs = [(name, d, r) for name, d, r in HOLIDAYS]
    seen = {(d, r) for _, d, r in pairs}
    d = date.today() + timedelta(days=14)
    d += timedelta(days=(4 - d.weekday()) % 7)  # thứ Sáu kế tiếp
    while d <= LAST_DATE:
        dep, ret = d.isoformat(), (d + timedelta(days=2)).isoformat()
        if (dep, ret) not in seen:
            pairs.append(("Cuối tuần", dep, ret))
        d += timedelta(days=7)
    return sorted(pairs, key=lambda p: p[1])


def flight_url(dep, ret):
    return ("https://www.google.com/travel/flights/search?q=Flights%20to%20KUL%20from%20HAN%20on%20"
            f"{dep}%20through%20{ret}&curr=VND&hl=vi&gl=VN")


def scan_one(page, dep, ret):
    page.goto(flight_url(dep, ret), wait_until="domcontentloaded", timeout=60000)
    crawler._check_blocked(page)
    outs = crawler._wait_labels(page, "Nội Bài lúc")
    outs = [f for f in outs if "Rời Cảng hàng không Quốc tế Nội Bài" in f["label"]]
    if not outs:
        return None
    best = min(outs, key=lambda f: f["price"])
    direct = [f for f in outs if f["direct"]]
    best_d = min(direct, key=lambda f: f["price"]) if direct else None
    return {
        "cheapest": best["price"],
        "airline": f'{best["airline"]} {best["dep"]}',
        "direct": best["direct"],
        "cheapestDirect": best_d["price"] if best_d else None,
        "directAirline": f'{best_d["airline"]} {best_d["dep"]}' if best_d else "",
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    pairs = date_pairs()
    results = []
    with sync_playwright() as p:
        browser = crawler._launch(p, True)
        ctx = browser.new_context(locale="vi-VN", user_agent=crawler.UA,
                                  viewport={"width": 1366, "height": 900}, timezone_id="Asia/Ho_Chi_Minh")
        page = ctx.new_page()
        for i, (name, dep, ret) in enumerate(pairs, 1):
            try:
                r = scan_one(page, dep, ret)
            except RuntimeError as e:
                log.error("Dừng: %s", e)
                break
            except Exception as e:
                log.warning("%s → %s lỗi: %s", dep, ret, e)
                r = None
            row = {"name": name, "depart": dep, "return": ret, **(r or {})}
            results.append(row)
            log.info("[%d/%d] %s %s→%s: %s", i, len(pairs), name, dep, ret,
                     f'{r["cheapest"]:,} ₫ ({r["airline"]})' if r else "không có dữ liệu")
            time.sleep(random.uniform(3, 6))
        browser.close()
    data = {"scannedAt": datetime.now(VN_TZ).isoformat(timespec="minutes"), "rows": results}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    # bản .js cho trường hợp mở index.html trực tiếp từ ổ đĩa
    OUT.with_suffix(".js").write_text("window.SCAN = " + json.dumps(data, ensure_ascii=False) + ";\n",
                                      encoding="utf-8")
    ok = [r for r in results if r.get("cheapest")]
    print(f"Xong {len(ok)}/{len(pairs)} cặp ngày → {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
