# HƯỚNG DẪN CÀI ĐẶT VÀ CHẠY CHƯƠNG TRÌNH (FOREIGN CAPITAL RADAR)

Hướng dẫn từng bước để chạy bộ kiểm thử tự động (`pytest`) và giao diện Dashboard (`streamlit`) trên máy cá nhân (hỗ trợ cả **Windows** và **macOS/Linux**).

## Bước 1: Mở Terminal tại thư mục gốc dự án

1. Mở thư mục gốc `foreign-capital-radar/` (nơi chứa tệp `requirements.txt` và thư mục `src/`) trong **VS Code**.
2. Mở Terminal tích hợp bằng tổ hợp phím **`Ctrl + ~`** (phím dấu ngã bên dưới `Esc`) hoặc vào menu **Terminal → New Terminal**.
3. Kiểm tra bạn đang đứng đúng thư mục gốc bằng lệnh `ls` (hoặc `dir` trên Windows) — kết quả phải thấy `src`, `tests`, `requirements.txt`.

## Bước 2: Tạo và kích hoạt môi trường ảo (Khuyên dùng)

Việc dùng môi trường ảo (`venv`) giúp thư viện đồ án không bị xung đột với các dự án Python khác.

* **Trên Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```
  *(Nếu PowerShell báo lỗi chặn chạy script `ExecutionPolicy`, hãy chạy lệnh sau 1 lần rồi kích hoạt lại: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`)*

* **Trên macOS / Linux / Git Bash:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

Khi thấy chữ `(.venv)` xuất hiện ở đầu dòng lệnh là bạn đã kích hoạt thành công.

## Bước 3: Cài đặt các thư viện cần thiết

Chạy lệnh sau để cài toàn bộ thư viện trong `requirements.txt` (quá trình này mất khoảng 1–2 phút):

```bash
pip install -r requirements.txt
```

## Bước 4: Chạy kiểm thử tự động (`pytest`)

Để tránh lỗi hệ điều hành không nhận diện lệnh `pytest` trực tiếp, bạn nên chạy qua `python -m`:

```bash
python -m pytest tests/ -v
```

**Kết quả kỳ vọng:** Terminal hiển thị **5 passed** màu xanh lá (xác nhận toàn bộ hàm làm sạch dữ liệu, tính FPR/FMS/Room Squeeze, cảnh báo và luồng E2E đều hoạt động chính xác).

## Bước 5: Khởi chạy Giao diện Dashboard (`streamlit`)

Chạy lệnh sau tại thư mục gốc dự án:

```bash
python -m streamlit run src/app/main.py
```

1. Sau vài giây, trình duyệt web sẽ tự động mở tại địa chỉ **`http://localhost:8501`** (nếu không tự mở, bạn copy đường dẫn này dán vào Chrome/Edge).
2. Ở thanh bên trái (Sidebar), chọn chế độ **`Dữ liệu mô phỏng (SIMULATED_MOCK)`** và bấm nút **🔄 Khởi tạo / Làm mới dữ liệu mô phỏng**.
3. Toàn bộ KPI, Bản đồ nhiệt ngành, Ma trận hành vi, Biểu đồ nội ngày và Thẻ cảnh báo sẽ hiển thị ngay lập tức.
4. Để dừng ứng dụng khi xem xong, quay lại cửa sổ Terminal và nhấn **`Ctrl + C`**.

## Xử lý nhanh các lỗi thường gặp

| Lỗi gặp phải | Nguyên nhân & Cách khắc phục |
| :--- | :--- |
| `ModuleNotFoundError: No module named 'src'` | Bạn chưa đứng ở thư mục gốc dự án. Dùng lệnh `cd` quay về thư mục chứa `requirements.txt` rồi chạy lại `python -m pytest tests/ -v`. |
| `'pytest' is not recognized...` | Dùng `python -m pytest tests/ -v` và `python -m streamlit run src/app/main.py` thay vì gọi trực tiếp tên lệnh. |
| `No module named 'pandas'` hoặc `'streamlit'` | Chưa cài thư viện hoặc chưa kích hoạt đúng môi trường `.venv`. Chạy lại Bước 2 và Bước 3. |
| Lỗi liên quan đến `ZoneInfoNotFoundError` (trên Windows) | Cài bổ sung gói múi giờ: `pip install tzdata`. |