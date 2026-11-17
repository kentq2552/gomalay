"""Crawl giá vé HAN <-> KUL và khách sạn Bukit Bintang từ Google Flights / Google Hotels.

Chỉ đọc giá, không đặt vé, không đăng nhập. Chạy thử:  python crawler.py
"""
import json
import re
import sys
import logging

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

log = logging.getLogger("crawler")

DEPART = "2027-04-30"
RETURN = "2027-05-02"
FLIGHT_URL = (
    "https://www.google.com/travel/flights/search?q=Flights%20to%20KUL%20from%20HAN%20on%20"
    f"{DEPART}%20through%20{RETURN}&curr=VND&hl=vi&gl=VN"
)
HOTEL_URL = (
    "https://www.google.com/travel/search?q=hotels%20Bukit%20Bintang%20Kuala%20Lumpur"
    f"&checkin={DEPART}&checkout={RETURN}&adults=1&curr=VND&hl=vi&gl=VN"
)
HOTEL_MAX_PRICE = 1_000_000
HOTEL_MIN_RATING = 4.5
# Ưu tiên chuyến về khởi hành trong khung giờ này (để còn chơi buổi sáng ngày cuối)
RETURN_PREFERRED = ("09:00", "16:00")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

LABEL_JS = """() => [...document.querySelectorAll('[aria-label]')]
  .map(e => e.getAttribute('aria-label'))
  .filter(t => /Chọn chuyến bay/.test(t) && /đồng Việt Nam/.test(t))"""

CLICK_JS = """(label) => {
  const el = [...document.querySelectorAll('[aria-label]')].find(e => e.getAttribute('aria-label') === label);
  if (!el) return false; el.click(); return true; }"""


def _num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s))


def parse_flight_label(label: str):
    m_price = re.search(r"từ (\d+) đồng", label)
    m_air = re.search(r"(?:Chuyến bay thẳng|Chuyến bay có \d+ điểm dừng) của (.+?)\. Rời", label)
    times = re.findall(r"lúc (\d{1,2}:\d{2})", label)
    if not (m_price and m_air and len(times) >= 2):
        return None
    return {
        "label": label,
        "price": int(m_price.group(1)),
        "airline": m_air.group(1).strip(),
        "dep": times[0],
        "arr": times[1],
        "direct": "Chuyến bay thẳng" in label,
    }


def _wait_labels(page, must_contain: str, timeout_s=25):
    for _ in range(timeout_s * 2):
        labels = page.evaluate(LABEL_JS)
        flights = [f for f in (parse_flight_label(l) for l in labels) if f and must_contain in f["label"]]
        if flights:
            page.wait_for_timeout(1500)  # chờ thêm cho danh sách tải đủ
            labels = page.evaluate(LABEL_JS)
            return [f for f in (parse_flight_label(l) for l in labels) if f and must_contain in f["label"]]
        page.wait_for_timeout(500)
    return []


def _check_blocked(page):
    url = page.url
    text = page.inner_text("body")[:2000]
    if "/sorry/" in url or "unusual traffic" in text or "lưu lượng truy cập bất thường" in text:
        raise RuntimeError("Google đang chặn truy cập tự động (trang CAPTCHA). Thử lại sau vài giờ.")
    if "consent.google" in url:
        raise RuntimeError("Google hiện trang đồng ý cookie, crawler chưa xử lý được.")


def crawl_flights(page):
    page.goto(FLIGHT_URL, wait_until="domcontentloaded", timeout=60000)
    _check_blocked(page)
    outs = _wait_labels(page, "Nội Bài lúc")
    outs = [f for f in outs if "Rời Cảng hàng không Quốc tế Nội Bài" in f["label"]]
    if not outs:
        raise RuntimeError("Không đọc được danh sách chuyến bay chiều đi.")

    # giá thấp nhất mỗi hãng, ưu tiên bay thẳng
    by_air = {}
    for f in sorted(outs, key=lambda f: (not f["direct"], f["price"])):
        by_air.setdefault(f["airline"], f)
    airlines = sorted(by_air.values(), key=lambda f: f["price"])

    direct = [f for f in outs if f["direct"]] or outs
    best_out = min(direct, key=lambda f: f["price"])
    page.evaluate(CLICK_JS, best_out["label"])

    backs = _wait_labels(page, "Rời Sân bay quốc tế Kuala Lumpur")
    best_back = None
    platforms, cheapest = [], best_out["price"]
    if backs:
        lo, hi = RETURN_PREFERRED
        direct_b = [f for f in backs if f["direct"]] or backs
        preferred = [f for f in direct_b if lo <= f["dep"].zfill(5) <= hi]
        best_back = min(preferred or direct_b, key=lambda f: f["price"])
        page.evaluate(CLICK_JS, best_back["label"])
        try:
            page.get_by_text("Tùy chọn đặt vé").first.wait_for(timeout=25000)
            # các đại lý hiện dần: chờ tới khi số nền tảng ổn định (tối đa ~20s)
            platforms, total_low, stable = [], None, 0
            for _ in range(14):
                page.wait_for_timeout(1500)
                p2, t2 = parse_booking_options(page.inner_text("body"))
                stable = stable + 1 if (len(p2) == len(platforms) and len(p2) >= 2) else 0
                platforms, total_low = p2, t2
                if stable >= 2:
                    break
            if total_low:
                cheapest = total_low
        except PWTimeout:
            log.warning("Không thấy mục 'Tùy chọn đặt vé'")

    if platforms:
        cheapest = min(cheapest, min(p["price"] for p in platforms))

    return {
        "cheapest": cheapest,
        "cheapestAirline": f'{best_out["airline"]} {best_out["dep"]}' + (f' / về {best_back["dep"]}' if best_back else ""),
        "cheapestPlatform": platforms[0]["name"] if platforms else "",
        "platformFlight": f'{best_out["airline"]}: đi {best_out["dep"]}→{best_out["arr"]}'
                          + (f', về {best_back["dep"]}→{best_back["arr"]}' if best_back else ""),
        "airlines": [{
            "airline": f["airline"],
            "out": f'{f["dep"]} → {f["arr"]}',
            "back": (f'{best_back["dep"]} → {best_back["arr"]}' if best_back and f is best_out else "theo lựa chọn"),
            "price": f["price"],
            "note": "Bay thẳng" if f["direct"] else "Có điểm dừng",
        } for f in airlines[:6]],
        "platforms": platforms,
    }


def parse_booking_options(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    price_re = re.compile(r"^([\d\.]+)\s*₫$")
    platforms, total_low = [], None
    for i, line in enumerate(lines):
        if line == "Tổng giá thấp nhất" and i > 0 and price_re.match(lines[i - 1]):
            total_low = _num(lines[i - 1])
        if line.startswith("Đặt vé của "):
            name = line[len("Đặt vé của "):].replace("Hãng hàng không", " (hãng)").strip()
            for j in range(i + 1, min(i + 4, len(lines))):
                m = price_re.match(lines[j])
                if m:
                    if not any(p["name"] == name for p in platforms):
                        platforms.append({"name": name, "price": _num(m.group(1))})
                    break
    platforms.sort(key=lambda p: p["price"])
    return platforms, total_low


def crawl_hotels(page):
    page.goto(HOTEL_URL, wait_until="domcontentloaded", timeout=60000)
    _check_blocked(page)
    try:
        page.get_by_text("Xem giá").first.wait_for(timeout=25000)
    except PWTimeout:
        pass
    page.wait_for_timeout(2500)
    return parse_hotels(page.inner_text("body"))


def parse_hotels(text: str):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    price_re = re.compile(r"^([\d\.]+)\s*₫$")
    rating_re = re.compile(r"^(\d),(\d)$")
    reviews_re = re.compile(r"^\(([\d,\.]+\s*N?)\)$")
    skip = ("GIÁ TỐT", "Rẻ hơn", "cho khách sạn", "Đã giảm", "Ưu đãi")
    hotels = []
    for i, line in enumerate(lines):
        m = rating_re.match(line)
        if not (m and i + 1 < len(lines) and reviews_re.match(lines[i + 1])):
            continue
        rating = float(f"{m.group(1)}.{m.group(2)}")
        # lùi lại tìm giá
        p_idx = next((j for j in range(i - 1, max(i - 5, -1), -1) if price_re.match(lines[j])), None)
        if p_idx is None:
            continue
        price = _num(lines[p_idx])
        n_idx = next((j for j in range(p_idx - 1, max(p_idx - 4, -1), -1)
                      if not lines[j].startswith(skip)), None)
        if n_idx is None:
            continue
        name = lines[n_idx]
        around = " ".join(lines[i + 1:i + 4])
        hotels.append({
            "name": name,
            "price": price,
            "rating": rating,
            "reviews": reviews_re.match(lines[i + 1]).group(1),
            "why": "Vị trí rất lý tưởng" if "Địa điểm rất lý tưởng" in around else "",
            "chain": name.lower().startswith("kingston hotel"),
        })
    seen, out = set(), []
    for h in sorted(hotels, key=lambda h: h["price"]):
        if (h["name"] in seen or h["price"] >= HOTEL_MAX_PRICE or h["rating"] < HOTEL_MIN_RATING
                or re.search(r"hostel|capsule|backpacker|dorm", h["name"], re.I)):
            continue
        seen.add(h["name"])
        out.append(h)
    return out[:7]


def _launch(p, headless: bool):
    """Windows: dùng Edge/Chrome có sẵn trên máy. Linux (GitHub Actions): Chromium của Playwright."""
    channels = ["msedge", "chrome", None] if sys.platform == "win32" else [None]
    last = None
    for ch in channels:
        try:
            return p.chromium.launch(headless=headless, channel=ch) if ch else p.chromium.launch(headless=headless)
        except Exception as e:
            last = e
            log.warning("Không mở được trình duyệt %s: %s", ch or "chromium", str(e).splitlines()[0])
    raise RuntimeError(f"Không mở được trình duyệt nào: {last}")


def crawl(headless: bool = True) -> dict:
    with sync_playwright() as p:
        browser = _launch(p, headless)
        ctx = browser.new_context(locale="vi-VN", user_agent=UA, viewport={"width": 1366, "height": 900},
                                  timezone_id="Asia/Ho_Chi_Minh")
        page = ctx.new_page()
        try:
            rec = crawl_flights(page)
            try:
                rec["hotels"] = crawl_hotels(page)
            except Exception as e:  # khách sạn lỗi thì vẫn giữ giá vé
                log.warning("Lỗi crawl khách sạn: %s", e)
                rec["hotels"] = []
            return rec
        finally:
            browser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(crawl(), ensure_ascii=False, indent=1))
