"""Server theo dõi giá chuyến Hà Nội <-> Kuala Lumpur, chạy trên máy.

Mở dashboard: http://localhost:5055
- Tự crawl mỗi ngày từ RUN_HOUR giờ (hoặc ngay khi bật máy nếu hôm nay chưa có giá).
- Lỗi thì thử lại sau 1 tiếng, tối đa 3 lần/ngày.
"""
import logging
import subprocess
import threading
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler

from flask import Flask, jsonify, send_from_directory

from update_prices import BASE, DATA_FILE, load_rows, today, update

LOG_FILE = BASE / "logs" / "app.log"
PORT = 5055
# Đã có GitHub Actions crawl lúc 7h nên tắt lịch trên máy để khỏi xung đột file khi đồng bộ.
# Đặt True nếu chỉ dùng trên máy, không dùng GitHub.
AUTO_CRAWL = False
RUN_HOUR = 7
MAX_TRIES_PER_DAY = 3
RETRY_AFTER_S = 3600
DROP_ALERT_PCT = 5            # giảm >= 5% so với mức thấp nhất từng ghi thì báo
DROP_ALERT_PRICE = 5_500_000  # hoặc xuống dưới mức này

LOG_FILE.parent.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8"),
              logging.StreamHandler()],
)
log = logging.getLogger("app")

app = Flask(__name__, static_folder=None)
_lock = threading.Lock()
state = {"running": False, "last_run": None, "last_error": None, "tries": {}}


def safe_load() -> list:
    try:
        return load_rows()
    except Exception:
        log.exception("Không đọc được %s", DATA_FILE)
        return []


def notify(title: str, msg: str) -> None:
    """Hiện thông báo góc màn hình Windows (không cần cài thêm gì)."""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
        f"$n.ShowBalloonTip(15000,'{title}','{msg}','Info');Start-Sleep 16;$n.Dispose()"
    )
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        log.exception("Không hiện được thông báo")


def run_crawl() -> bool:
    if not _lock.acquire(blocking=False):
        return False
    state["running"] = True
    d = today()
    state["tries"][d] = state["tries"].get(d, 0) + 1
    try:
        log.info("Bắt đầu crawl (lần %s hôm nay)", state["tries"][d])
        rec, old = update()
        state["last_error"] = None
        log.info("Xong: rẻ nhất %s ₫ (%s)", f'{rec["cheapest"]:,}', rec.get("cheapestAirline"))
        if old:
            low = min(r["cheapest"] for r in old)
            if rec["cheapest"] <= low * (1 - DROP_ALERT_PCT / 100) or rec["cheapest"] < DROP_ALERT_PRICE:
                notify("GIÁ VÉ KL GIẢM", f'Khứ hồi còn {rec["cheapest"]:,} đ - mở localhost:{PORT}')
        return True
    except Exception as e:
        state["last_error"] = str(e)
        log.exception("Crawl lỗi")
        return False
    finally:
        state["running"] = False
        state["last_run"] = datetime.now().isoformat(timespec="minutes")
        _lock.release()


def scheduler_loop():
    last_attempt = 0.0
    while True:
        try:
            d = today()
            has_today = any(r.get("date") == d for r in safe_load())
            due = datetime.now().hour >= RUN_HOUR
            can_retry = time.time() - last_attempt >= RETRY_AFTER_S
            if due and not has_today and state["tries"].get(d, 0) < MAX_TRIES_PER_DAY and can_retry:
                last_attempt = time.time()
                run_crawl()
        except Exception:
            log.exception("Lỗi vòng lặp lịch")
        time.sleep(300)


@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/data/prices.json")
def prices_file():
    return send_from_directory(DATA_FILE.parent, DATA_FILE.name, max_age=0)


@app.get("/api/status")
def api_status():
    return jsonify({k: v for k, v in state.items() if k != "tries"} | {"runHour": RUN_HOUR})


@app.post("/api/refresh")
def api_refresh():
    if state["running"]:
        return jsonify({"started": False, "message": "Đang crawl rồi"}), 409
    threading.Thread(target=run_crawl, daemon=True).start()
    return jsonify({"started": True}), 202


if __name__ == "__main__":
    if AUTO_CRAWL:
        threading.Thread(target=scheduler_loop, daemon=True).start()
    log.info("Server chạy tại http://localhost:%s", PORT)
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
