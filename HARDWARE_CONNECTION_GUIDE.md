# HƯỚNG DẪN ĐẤU NỐI & ĐIỀU KHIỂN MODULE PHẦN CỨNG BẰNG MÃ MORSE

Tài liệu này hướng dẫn chi tiết sơ đồ chân, cách đấu nối phần cứng trên **Raspberry Pi** và tập lệnh cử chỉ tay để điều khiển **Active Buzzer**, **Module Relay CW019** và **Bóng đèn LED**.

---

## 1. SƠ ĐỒ ĐẤU NỐI DÂY TRÊN RASPBERRY PI (WIRING DIAGRAM)

### 1.1. Module Active Buzzer 2 chân (Còi báo âm thanh)
Còi phát ra âm thanh "Tít" cho dấu Chấm (`.`), "Tè" cho dấu Gạch (`-`), và "Tít kép" khi hoàn thành 1 chữ cái.

| Chân trên Buzzer | Chân trên Raspberry Pi | Số chân vật lý (Physical Pin) | Chức năng |
| :---: | :---: | :---: | :--- |
| **Chân dài (+)** | **GPIO 17** | **Chân 11** | Nhận tín hiệu điều khiển (3.3V) từ code |
| **Chân ngắn (-)** | **GND** | **Chân 6** (hoặc 9, 14, 20) | Nối đất để khép kín mạch |

---

### 1.2. Module Relay 1 kênh CW019 (Đóng/ngắt thiết bị)
Relay dùng để đóng ngắt mạch điện đèn LED mô phỏng thiết bị 220V trong nhà thông minh.

| Chân trên Relay CW019 | Chân trên Raspberry Pi | Số chân vật lý (Physical Pin) | Chức năng |
| :---: | :---: | :---: | :--- |
| **Chân + (VCC)** | **5V Power** | **Chân 2** (hoặc 4) | Cấp nguồn nuôi cuộn cảm Relay |
| **Chân - (GND)** | **GND** | **Chân 9** (hoặc 6, 14) | Nối đất khép kín nguồn |
| **Chân S (Signal)** | **GPIO 27** | **Chân 13** | Nhận tín hiệu đóng/ngắt mạch |

> **⚠️ Lưu ý cực kỳ quan trọng cho Relay CW019:**
> - Kiểm tra cục **Jumper (chốt nhựa nhỏ màu đen/xanh)** trên Relay:
>   - Đang cắm nối giữa chân **giữa** và chân **L (Low Trigger)**: Code mặc định `active_high=False` sẽ kích mở Relay khi chân GPIO 27 ở mức Thấp (0V).
>   - Nếu cắm nối sang chân **H (High Trigger)**: Hãy đổi `active_high=True` trong hàm `HardwareManager` nếu Relay không nhảy.

---

### 1.3. Mạch Bóng Đèn LED qua Relay (Mô phỏng bóng 220V)
Lấy nguồn 3.3V từ Pi chờ sẵn ở chân `COM`, khi Relay đóng tiếp điểm sang `NO`, điện sẽ cấp sang bóng đèn LED.

| Từ vị trí | Đến vị trí | Chức năng |
| :--- | :--- | :--- |
| **Chân số 1 (3.3V)** trên Pi | **Lỗ ốc COM** trên Relay | Dẫn nguồn 3.3V chờ sẵn tại Relay |
| **Lỗ ốc NO (Normally Open)** trên Relay | **Chân dài (+) của LED** (qua Điện trở 220Ω - 1kΩ) | Dẫn điện sang LED khi Relay đóng |
| **Chân ngắn (-) của LED** | **Chân GND (Chân 14)** trên Pi | Khép kín mạch điện để LED sáng |

### 1.4. Module Driver Động Cơ L298N (Cầu H Điều Tốc & Đảo Chiều)
Module L298N điều khiển động cơ DC thông qua 2 chân điều khiển logic **IN1** và **IN2** (kết hợp xung PWM):

| Chân trên Module L298N | Chân trên Raspberry Pi / Nguồn ngoài | Số chân vật lý Pi | Chức năng |
| :---: | :---: | :---: | :--- |
| **IN1 (Forward)** | **GPIO 22** | **Chân 15** | Tín hiệu điều khiển chiều quay tiến (hỗ trợ xung PWM) |
| **IN2 (Backward)** | **GPIO 23** | **Chân 16** | Tín hiệu điều khiển chiều quay lùi (hỗ trợ xung PWM) |
| **ENA** | Giữ nguyên cục Jumper nối với 5V (hoặc nối vào 5V) | - | Kích hoạt kênh động cơ A |
| **VCC (+12V / +5V Power)** | Nguồn 5V trên Pi (Chân 2/4) hoặc Nguồn Pin ngoài | Chân 2 hoặc 4 | Cấp nguồn công suất cho động cơ quay |
| **GND** | **GND** | **Chân 6, 9, 14 hoặc 20** | **Bắt buộc nối chung GND** giữa Pi và nguồn L298N |
| **OUT1 & OUT2** | **2 đầu dây Động cơ DC** | - | Nối vào 2 cực của motor |

---

## 2. TẬP LỆNH ĐIỀU KHIỂN THIẾT BỊ BẰNG CỬ CHỈ TAY & MÃ MORSE

Hệ thống hoạt động với 2 chế độ thông minh có thể chuyển đổi linh hoạt bằng cử chỉ tay:

### 2.1. Cử chỉ chuyển đổi Chế độ Động cơ & Điều chỉnh 5 mức tốc độ:
| Cử chỉ Bàn Tay | Chế độ Hoạt Động | Hành vi Hệ Thống & Động Cơ L298N | Còi Buzzer Phản Hồi |
| :---: | :---: | :--- | :--- |
| **Đưa CẢ 2 BÀN TAY** (Khi đang Morse) | Chuyển sang **ĐIỀU KHIỂN ĐỘNG CƠ** | **Lập tức bật động cơ quay chậm ban đầu (Mức 1: 35% PWM)** | Kêu **Tít kép** xác nhận |
| **Giơ 1 ngón** (Trong chế độ Động cơ) | Chế độ Động cơ | Điều chỉnh tốc độ quay **Mức 1 (35% PWM)** - Quay chậm | Kêu Tít nhẹ |
| **Giơ 2 ngón** (Trong chế độ Động cơ) | Chế độ Động cơ | Điều chỉnh tốc độ quay **Mức 2 (50% PWM)** | Kêu Tít nhẹ |
| **Giơ 3 ngón** (Trong chế độ Động cơ) | Chế độ Động cơ | Điều chỉnh tốc độ quay **Mức 3 (65% PWM)** | Kêu Tít nhẹ |
| **Giơ 4 ngón** (Trong chế độ Động cơ) | Chế độ Động cơ | Điều chỉnh tốc độ quay **Mức 4 (80% PWM)** | Kêu Tít nhẹ |
| **Xòe 5 ngón** (Trong chế độ Động cơ) | Chế độ Động cơ | Điều chỉnh tốc độ quay **Mức 5 (100% PWM)** - Tối đa | Kêu Tít nhẹ |
| **Đưa LẠI CẢ 2 BÀN TAY** (Khi đang Động cơ)| Quay về **DỊCH MÃ MORSE** | **LẬP TỨC DỪNG ĐỘNG CƠ (0% PWM)** và trở về chế độ dịch mã | Kêu Tít ngắn báo dừng |

### 2.2. Cử chỉ trong Chế độ Dịch Mã Morse (Mặc định):
| Cử chỉ / Ký tự | Mã Morse | Hành vi Còi (Buzzer GPIO 17) | Hành vi Relay CW019 (GPIO 27) & Đèn LED |
| :---: | :---: | :--- | :--- |
| **Giơ 1 ngón** | `.` | Còi kêu **"Tít" ngắn (0.12s)** | Giữ nguyên trạng thái |
| **Giơ 2 ngón** | `-` | Còi kêu **"Tè" dài (0.35s)** | Giữ nguyên trạng thái |
| **Nắm tay (0 ngón)** | *Dịch ký tự* | Còi kêu **Tít kép** | Dịch mã Morse sang chữ & Thực thi lệnh thiết bị |
| **Giơ 4 ngón** | *Xóa Buffer* | Còi kêu **"Tít" ngắn (0.08s)** | **XÓA TOÀN BỘ VĂN BẢN ĐÃ DỊCH** trong Buffer |
| **Giơ 5 ngón** | *Xóa Morse* | Còi kêu **"Tít" ngắn (0.08s)** | **XÓA MÃ MORSE** đang nhập dở |
| **Lệnh `SOS`** | `...---...` (hoặc ghép S-O-S) | **Còi kêu "Tít Tít" liên tục 5s** | **ĐÈN LED NHẤP NHÁY LIÊN TỤC 5S** (Đồng thời **KHÓA CỬ CHỈ 5S**) |
| **Chữ `O` (ON)** | `---` | Còi kêu **Tít kép** báo dịch xong | **BẬT RELAY $\rightarrow$ Đèn LED SÁNG** |
| **Chữ `F` (OFF)** | `..-.` | Còi kêu **Tít kép** báo dịch xong | **TẮT RELAY $\rightarrow$ Đèn LED TẮT** |
| **Chữ `T` (TOGGLE)**| `-` | Còi kêu **Tít kép** báo dịch xong | **ĐẢO TRẠNG THÁI** (Đang tắt $\rightarrow$ Bật, Bật $\rightarrow$ Tắt) |
| **Từ `ON`** | `--- -.` | Còi kêu **Tít kép** | **BẬT RELAY $\rightarrow$ Đèn LED SÁNG** |
| **Từ `OFF`** | `--- ..-. ..-.`| Còi kêu **Tít kép** | **TẮT RELAY $\rightarrow$ Đèn LED TẮT** |

---

## 3. CÁCH CÀI ĐẶT THƯ VIỆN & CHẠY TRÊN RASPBERRY PI

### Bước 1: Cài thư viện `gpiozero` trên Pi (nếu chưa có)
Trong môi trường ảo `(env)` của Python 3.11 trên Pi:
```bash
pip install gpiozero
```
*(Nếu Pi dùng Debian Bookworm/Trixie yêu cầu thư viện pin, có thể cài thêm: `pip install lgpio` hoặc `sudo apt install python3-gpiozero`)*.

### Bước 2: Đồng bộ file mã nguồn mới lên Pi
Từ Git Bash trên máy tính:
```bash
cd "/d/Học Phần/Công nghệ IoT"
scp morse_web_app.py generic@192.168.137.219:~/labs/week09/
```

### Bước 3: Khởi chạy chương trình
Trên terminal Raspberry Pi:
```bash
python morse_web_app.py
```
👉 Terminal sẽ in thông báo:
```text
[Hardware Init] GPIO KHOI TAO THANH CONG! Buzzer: GPIO17, Relay: GPIO27
```

Mở trình duyệt `http://192.168.137.219:5000`:
- Bạn có thể **bấm các nút "Bật Đèn", "Tắt Đèn", "Test Còi"** trực tiếp trên web.
- Hoặc giơ ngón tay ra trước camera để điều khiển hoàn toàn bằng cử chỉ tay!
