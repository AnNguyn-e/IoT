# Tài Liệu Hướng Dẫn: Hệ Thống Web Dashboard Nhận Diện Mã Morse (AI Hand Tracking)

Tài liệu này mô tả chi tiết kiến trúc, cách triển khai và quy trình vận hành ứng dụng Web Server nhận diện mã Morse bằng cử chỉ tay, phục vụ mục đích chạy thử nghiệm trên máy tính và triển khai thực tế trên **Raspberry Pi qua kết nối SSH**.

---

## 1. Tổng Quan Kiến Trúc Hệ Thống (Architecture)

```
+--------------------------------------------------------------------------+
|                       RASPBERRY PI / LAPTOP                             |
|                                                                          |
|  [Camera Module] ---> [HandTrackingModule (MediaPipe)]                   |
|                              |                                           |
|                              v                                           |
|                    [Morse Logic Engine]                                  |
|                 (1 ngón: ., 2 ngón: -, ...)                              |
|                              |                                           |
|                              v                                           |
|                 [Flask Web Server (Port 5000)]                           |
|                    /video_feed (MJPEG Stream)                            |
|                    /api/status (JSON Realtime)                           |
+--------------------------------------------------------------------------+
                                 | (Mạng LAN / Wi-Fi)
                                 v
+--------------------------------------------------------------------------+
|                  TRÌNH DUYỆT LAPTOP / SMARTPHONE                        |
|                                                                          |
|             URL: http://<IP_RASPBERRY_PI>:5000                           |
|  - Khung hình Camera thời gian thực (vẽ landmark bàn tay)                |
|  - Bảng mã Morse đang nhập, chữ cái đã dịch                              |
|  - Bảng tra cứu nhanh mã Morse A-Z, 0-9                                  |
|  - Nút chức năng: Thêm dấu cách, Xóa ký tự, Xóa toàn bộ, Sao chép       |
+--------------------------------------------------------------------------+
```

### Ưu điểm vượt trội so với hiển thị `cv2.imshow` truyền thống:
1. **Không cần màn hình gắn vào Pi (Headless)**: Không bị lỗi thiếu display server khi SSH (`cannot open display`).
2. **Không tiêu tốn tài nguyên Desktop GUI**: Tiết kiệm 200–300MB RAM và tài nguyên GPU trên Pi.
3. **Độ trễ thấp (Low Latency)**: Nén JPEG trực tiếp từng khung hình truyền qua giao thức MJPEG Multipart HTTP.
4. **Truy cập đa nền tảng**: Laptop, máy tính bảng hay điện thoại cùng mạng Wi-Fi đều mở trình duyệt xem được.

---

## 2. Danh Sách Các File Trong Dự Án

| Tên File | Chức Năng |
| :--- | :--- |
| [morse_web_app.py](file:///d:/H%E1%BB%8Dc%20Ph%E1%BA%A7n/C%C3%B4ng%20ngh%E1%BB%87%20IoT/morse_web_app.py) | **File chính (Web Server)**: Chạy Flask, luồng camera nền, API thời gian thực và giao diện web hiện đại. |
| [HandTrackingModule.py](file:///d:/H%E1%BB%8Dc%20Ph%E1%BA%A7n/C%C3%B4ng%20ngh%E1%BB%87%20IoT/HandTrackingModule.py) | Module nhận diện 21 điểm khớp bàn tay và đếm ngón tay mở/gập bằng MediaPipe. |
| [morse_app.py](file:///d:/H%E1%BB%8Dc%20Ph%E1%BA%A7n/C%C3%B4ng%20ngh%E1%BB%87%20IoT/morse_app.py) | Phiên bản chạy ứng dụng cửa sổ OpenCV truyền thống trên máy tính. |
| [requirements.txt](file:///d:/H%E1%BB%8Dc%20Ph%E1%BA%A7n/C%C3%B4ng%20ngh%E1%BB%87%20IoT/requirements.txt) | Danh sách thư viện: `opencv-python`, `mediapipe==0.10.21`, `flask`. |
| [README.md](file:///d:/H%E1%BB%8Dc%20Ph%E1%BA%A7n/C%C3%B4ng%20ngh%E1%BB%87%20IoT/README.md) | Giải thích nguyên nhân các lỗi kỹ thuật ban đầu và cách khắc phục. |

---

## 3. Quy Tắc Cử Chỉ Điều Khiển

| Cử Chỉ Tay | Chức Năng | Ghi Chú |
| :---: | :--- | :--- |
| **Giơ 1 ngón** | Nhập dấu **Chấm (.)** | Ví dụ: Chỉ giơ 1 ngón trỏ |
| **Giơ 2 ngón** | Nhập dấu **Gạch ngang (-)** | Ví dụ: Giơ ngón trỏ và ngón giữa |
| **Nắm tay (0 ngón)** | **Dịch sang chữ cái** | Chuyển chuỗi Morse vừa nhập thành ký tự và ghép vào câu |
| **Xòe cả 5 ngón** | **Xóa mã đang nhập** | Hủy chuỗi Morse đang gõ dở nếu lỡ nhập sai |

> **Thời gian chờ (Cooldown):** Hệ thống cài đặt khoảng nghỉ an toàn là `1.3 giây` giữa các cử chỉ liên tiếp để tránh nhận diện trùng lặp nhiều lần cho một thao tác.

---

## 4. Hướng Dẫn Vận Hành

### Bước 1: Chạy thử nghiệm trên Laptop
Mở Git Bash hoặc Terminal trong thư mục dự án và thực hiện:
```bash
# Kích hoạt môi trường ảo
source /c/morse_env/Scripts/activate

# Khởi chạy Web Server
python morse_web_app.py
```

Sau khi terminal thông báo server đã khởi động, mở trình duyệt bất kỳ (Chrome, Edge, Cốc Cốc) và truy cập:
```
http://localhost:5000
```

---

### Bước 2: Triển khai lên Raspberry Pi qua SSH

#### 1. Cài đặt các thư viện trên Raspberry Pi:
Trong phiên làm việc SSH trên Pi:
```bash
# Cài các gói hệ thống hỗ trợ OpenCV & MediaPipe (nếu Pi chưa có)
sudo apt update
sudo apt install -y python3-pip python3-opencv libatlas-base-dev

# Cài đặt thư viện Python
pip install opencv-python flask mediapipe
```

#### 2. Khởi chạy server trên Pi:
```bash
python3 morse_web_app.py
```

#### 3. Xem hình ảnh trên màn hình Laptop:
1. Xem địa chỉ IP của Pi (gõ `hostname -I` trên terminal của Pi, ví dụ: `192.168.1.50`).
2. Mở trình duyệt trên Laptop (cùng kết nối vào Wi-Fi với Pi), truy cập:
```
http://192.168.1.50:5000
```
Màn hình laptop sẽ hiển thị toàn bộ luồng video từ camera của Raspberry Pi kèm bảng dịch mã Morse theo thời gian thực!

---

## 5. Danh Sách API của Hệ Thống

Dành cho nhu cầu mở rộng hoặc kết nối với các thiết bị IoT khác (ESP32, LoRa, MQTT):

- **`GET /video_feed`**: Luồng video liên tục định dạng `multipart/x-mixed-replace; boundary=frame`.
- **`GET /api/status`**: Trả về dữ liệu trạng thái hiện thời dạng JSON:
  ```json
  {
    "num_fingers": 1,
    "current_morse": ".-",
    "last_char": "A",
    "decoded_text": "HELLO",
    "status_msg": "Đã nhận dấu CHẤM (.) - Mã: .-"
  }
  ```
- **`POST /api/clear`**: Xóa toàn bộ chuỗi Morse và văn bản đã dịch.
- **`POST /api/space`**: Thêm dấu cách (khoảng trắng giữa các từ).
- **`POST /api/backspace`**: Xóa 1 ký tự cuối cùng của văn bản.
