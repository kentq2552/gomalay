# Theo dõi giá chuyến Hà Nội ⇄ Kuala Lumpur (30/4 – 2/5/2027)

Crawl giá vé khứ hồi (Google Flights) và khách sạn Bukit Bintang dưới 1 triệu/đêm (Google Hotels) mỗi ngày, lưu vào `data/prices.json`, hiển thị trên `index.html` (biểu đồ lịch sử giá, hãng bay, khách sạn, lịch trình 3N2Đ, quán ăn).

## Chạy tự động trên GitHub (không cần bật máy)

`.github/workflows/crawl.yml` chạy lúc 7h sáng giờ Việt Nam mỗi ngày (GitHub có thể trễ 5–30 phút), crawl rồi commit `data/prices.json`.

1. GitHub Desktop → **File → Add local repository** → chọn thư mục này → *create a repository* → **Commit** → **Publish repository** (bỏ chọn *Keep this code private* nếu muốn dùng GitHub Pages miễn phí).
2. Trên github.com, vào repo → **Settings → Pages** → Source: *Deploy from a branch* → Branch: `main` / `(root)` → Save. Vài phút sau trang có ở `https://<tên-github>.github.io/<tên-repo>/`.
3. Repo → **Actions** → chọn *Crawl giá vé KL* → **Run workflow** để chạy thử ngay.
4. Trước khi sửa code trên máy, bấm **Fetch origin / Pull** trong GitHub Desktop để lấy dữ liệu giá mới nhất.

Workflow lỗi (ví dụ Google chặn) thì GitHub gửi email báo.

## Chạy trên máy

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
start-server.bat
```

Mở http://localhost:5055, bấm **Cập nhật ngay** để crawl. Trên Windows crawler dùng Microsoft Edge có sẵn. Lịch tự động trên máy đang tắt (`AUTO_CRAWL = False` trong `app.py`) vì GitHub đã lo.

## Giới hạn

- Google chỉ hiện giá bán trực tiếp của hãng cho trình duyệt tự động, nên phần so sánh đại lý dùng các nút mở nhanh trên trang.
- Google có thể đổi giao diện hoặc chặn truy cập tự động; khi đó cần sửa `crawler.py`.
