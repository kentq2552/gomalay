"""Rà giá vé khứ hồi HAN <-> KUL cho nhiều cặp ngày (mỗi cuối tuần Thứ 6 -> CN, các dịp lễ).

Chạy:  .venv\\Scripts\\python.exe scan_dates.py
Kết quả ghi vào data/scan.json (index.html đọc để vẽ bảng so sánh).
Chỉ đọc giá "từ ... đồng" ở danh sách chiều đi (đã là giá khứ hồi), không bấm chọn chuyến,
nên nhanh (~10 giây/cặp ngày). Mỗi lần chạy nghỉ vài giây giữa các lượt để không bị Google chặn.
"""
import json
import logging
import random
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

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


# Khách sạn "ổn nhất": tìm theo view KLCC + hồ bơi tầng thượng, rồi chỉ giữ chỗ được chấm cao
# và có nhiều lượt đánh giá (đủ đông người ở để điểm số đáng tin).
HOTEL_QUERY = "hotels with KLCC view rooftop pool Bukit Bintang"
HOTEL_MIN_RATING = 4.3
HOTEL_MIN_REVIEWS = 1500
# Lựa chọn "view đẹp tầm 1 triệu"
MID_MIN_RATING = 4.1
MID_MIN_REVIEWS = 1000
MID_MAX_PRICE = 1_300_000


def hotel_url(dep, ret):
    return ("https://www.google.com/travel/search?q=" + quote(HOTEL_QUERY)
            + f"&checkin={dep}&checkout={ret}&adults=1&curr=VND&hl=vi&gl=VN")


def hotel_one(page, dep, ret):
    crawler.open_hotels(page, hotel_url(dep, ret), dep, ret)
    text = page.inner_text("body")
    for _ in range(2):  # đọc 3 trang kết quả
        nxt = page.get_by_text("Tiếp theo", exact=True)
        if not nxt.count():
            break
        try:
            nxt.first.click()
            page.wait_for_timeout(3500)
            text += "\n" + page.inner_text("body")
        except Exception:
            break
    seen, allh = set(), []
    for h in crawler.parse_hotels_all(text):
        if h["name"] in seen or re.search(r"hostel|capsule|backpacker|dorm|phòng ngủ|^căn hộ|^nhà ", h["name"], re.I):
            continue
        seen.add(h["name"])
        allh.append({"name": h["name"], "price": h["price"], "rating": h["rating"],
                     "reviews": crawler.review_count(h["reviews"])})
    nights = (date.fromisoformat(ret) - date.fromisoformat(dep)).days
    with_total = lambda h: {**h, "total": h["price"] * nights} if h else None
    # điểm cao nhất trước, hoà điểm thì chỗ nhiều đánh giá hơn
    rank = lambda h: (-h["rating"], -h["reviews"])
    good = sorted((h for h in allh if h["rating"] >= HOTEL_MIN_RATING and h["reviews"] >= HOTEL_MIN_REVIEWS), key=rank)
    # view đẹp tầm 1 triệu: nới tiêu chí một chút, giới hạn giá
    mid = sorted((h for h in allh if h["rating"] >= MID_MIN_RATING and h["reviews"] >= MID_MIN_REVIEWS
                  and h["price"] <= MID_MAX_PRICE), key=rank)
    if not good and not mid:
        return None
    out = {"nights": nights, "mid": with_total(mid[0] if mid else None)}
    if good:
        best = good[0]
        # lựa chọn đáng tiền: rẻ nhất trong nhóm đạt chuẩn (≥4,3★, nhiều đánh giá)
        cheap = min(good, key=lambda h: h["price"])
        out.update({**best, "total": best["price"] * nights,
                    "cheap": with_total(cheap) if cheap is not best else None,
                    "alts": good[1:3]})
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    hotels_only = "--hotels-only" in sys.argv  # giữ giá vé đã rà, chỉ rà thêm khách sạn
    missing_only = "--missing" in sys.argv     # chỉ rà khách sạn cho các dòng chưa có
    if hotels_only:
        old = json.loads(OUT.read_text(encoding="utf-8"))
        old_rows = {(r["depart"], r["return"]): r for r in old["rows"]}
        pairs = [(r["name"], r["depart"], r["return"]) for r in old["rows"]
                 if not (missing_only and r.get("hotel"))]
    else:
        pairs = date_pairs()
    results = []
    with sync_playwright() as p:
        browser = crawler._launch(p, True)
        ctx = browser.new_context(locale="vi-VN", user_agent=crawler.UA,
                                  viewport={"width": 1366, "height": 900}, timezone_id="Asia/Ho_Chi_Minh")
        page = ctx.new_page()
        for i, (name, dep, ret) in enumerate(pairs, 1):
            r = {}
            try:
                if hotels_only:
                    r = {k: v for k, v in old_rows[(dep, ret)].items()
                         if k not in ("name", "depart", "return", "hotel")}
                else:
                    r = scan_one(page, dep, ret) or {}
                    time.sleep(random.uniform(2, 4))
                r["hotel"] = hotel_one(page, dep, ret)
            except RuntimeError as e:
                log.error("Dừng: %s", e)
                break
            except Exception as e:
                log.warning("%s → %s lỗi: %s", dep, ret, e)
            row = {"name": name, "depart": dep, "return": ret, **r}
            results.append(row)
            h = r.get("hotel") or {}
            ks = lambda x: f'{x["name"]} {x["price"]:,} ₫/đêm ({x["rating"]}, {x["reviews"]})' if x else "không có"
            log.info("[%d/%d] %s %s→%s: vé %s | KS %s | tầm 1tr %s", i, len(pairs), name, dep, ret,
                     f'{r["cheapest"]:,} ₫' if r.get("cheapest") else "không có",
                     ks(h if h.get("name") else None), ks(h.get("mid")))
            time.sleep(random.uniform(3, 6))
        browser.close()
    if hotels_only:  # giữ nguyên các dòng không rà lần này (--missing, hoặc bị dừng giữa chừng)
        done = {(r["depart"], r["return"]) for r in results}
        results += [r for k, r in old_rows.items() if k not in done]
        results.sort(key=lambda r: r["depart"])
    data = {"scannedAt": datetime.now(VN_TZ).isoformat(timespec="minutes"),
            "hotelQuery": HOTEL_QUERY, "hotelMinRating": HOTEL_MIN_RATING,
            "hotelMinReviews": HOTEL_MIN_REVIEWS, "midMinRating": MID_MIN_RATING,
            "midMinReviews": MID_MIN_REVIEWS, "midMaxPrice": MID_MAX_PRICE, "rows": results}
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    # bản .js cho trường hợp mở index.html trực tiếp từ ổ đĩa
    OUT.with_suffix(".js").write_text("window.SCAN = " + json.dumps(data, ensure_ascii=False) + ";\n",
                                      encoding="utf-8")
    ok = [r for r in results if r.get("cheapest")]
    print(f"Xong {len(ok)}/{len(pairs)} cặp ngày → {OUT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
