"""
Module Xử Lý Logic Cốt Lõi (Core Engine)
- Nhận diện cử chỉ bàn tay qua Camera USB (MediaPipe HandTrackingModule)
- Quản lý Máy trạng thái 2 chế độ:
  1. Chế độ Dịch Mã Morse (1 ngón ., 2 ngón -, 0 ngón dịch, 4 ngón xóa buffer, 5 ngón xóa morse, SOS 5s lock)
  2. Chế độ Điều Khiển Động Cơ L298N (2 tay bật/dừng, 1-5 ngón chỉnh 5 mức tốc độ)
- Quản lý bộ đệm video stream (MJPEG) và trạng thái hệ thống Thread-safe
"""

import cv2
import time
import threading
import os
import numpy as np
from HandTrackingModule import HandDetector
from hardware_manager import HardwareManager

# ----------------------------------------------------
# 1. Từ điển mã Morse Chuẩn Quốc Tế
# ----------------------------------------------------
MORSE_DICT = {
    '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E',
    '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I', '.---': 'J',
    '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O',
    '.--.': 'P', '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T',
    '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y',
    '--..': 'Z',
    '-----': '0', '.----': '1', '..---': '2', '...--': '3', '....-': '4',
    '.....': '5', '-....': '6', '--...': '7', '---..': '8', '----.': '9',
    '...---...': 'SOS'
}

class MorseGestureEngine:
    def __init__(self, buzzer_pin=17, relay_pin=27, motor_fwd=22, motor_bwd=23):
        # Khởi tạo phần cứng
        self.hw = HardwareManager(buzzer_pin, relay_pin, motor_fwd, motor_bwd)
        
        # Biến trạng thái toàn cục (Thread-safe)
        self.app_mode = "MORSE"          # "MORSE" hoặc "MOTOR"
        self.state_lock = threading.Lock()
        self.state = {
            "app_mode": self.app_mode,
            "current_morse": "",
            "decoded_text": "",
            "last_char": "",
            "num_fingers": 0,
            "num_hands": 0,
            "status_msg": "Hệ thống sẵn sàng (Chế độ Dịch Morse)",
            "relay_state": False,
            "motor_speed": 0,            # 0..5 (0: Dừng, 1: 35% -> 5: 100%)
            "motor_duty": 0,             # 0..100% PWM
            "motor_running": False,
            "hardware_active": self.hw.is_hardware_active,
            "is_sos_active": False,
            "cooldown_remaining": 0.0,
            "two_hands_holding": False,
            "hold_progress": 0.0,        # 0.0 -> 1.0
            "hold_seconds": 0.0          # 0.0 -> 1.0s
        }
        
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.is_running = True
        
        # Cấu hình ngưỡng thời gian
        self.COOLDOWN_TIME = 0.8
        self.HOLD_MODE_DURATION = 1.0     # Bắt buộc giữ cả 2 bàn tay liên tục trong 1.0s mới đổi Mode
        self.MODE_TOGGLE_COOLDOWN = 1.2   # Chống lặp sau khi vừa chuyển Mode
        self.SPEED_CHANGE_COOLDOWN = 0.5  # Chống rung khi đổi cấp tốc độ ngón tay
        self.sos_lock_until = 0.0
        self.last_mode_switch_time = 0.0  # Mốc thời gian chuyển mode gần nhất

        self.cam_thread = None

    def get_working_camera(self):
        """Tự động phát hiện camera USB hoạt động ổn định trên Raspberry Pi hoặc Laptop"""
        env_idx = os.getenv("CAMERA_INDEX")
        candidate_indices = [int(env_idx)] if env_idx is not None else [0, 1, 2, 3, 4, -1]
        
        for idx in candidate_indices:
            if idx == -1:
                break
            cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            if not cap.isOpened():
                cap = cv2.VideoCapture(idx)
                
            if cap.isOpened():
                ret, frame = cap.read()
                if ret and frame is not None:
                    print(f"[Camera Discovery] >> Tim thay camera hoat dong tai index: {idx}")
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    return cap
                cap.release()
                
        print("[Camera Discovery] !! Khong tim thay camera kha dung o index 0..4 !!")
        cap = cv2.VideoCapture(0)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _tracking_loop(self):
        """Vòng lặp chạy ngầm đọc camera, xử lý AI nhận diện cử chỉ và điều khiển ngoại vi"""
        cap = self.get_working_camera()
        detector = HandDetector(detectionCon=0.5, maxHands=2, modelComplexity=0)
        
        last_action_time = time.time()
        last_speed_change_time = 0.0
        two_hands_start_time = None
        last_two_hands_seen_time = 0.0
        mode_switched_for_current_hold = False
        
        print(f"[Core Engine] Vong lap AI bat dau (Giu 2 tay 1s de doi Mode, L298N 1-5 ngons)...")
        retry_count = 0
        prev_frame_time = time.time()
        
        while self.is_running:
            success, img = cap.read()
            if not success:
                retry_count += 1
                if retry_count % 30 == 0:
                    print("[Core Engine] Dang thu ket noi lai camera...")
                    cap.release()
                    cap = self.get_working_camera()
                    
                placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
                cv2.putText(placeholder, "DANG KET NOI CAMERA...", (30, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
                ret, buffer = cv2.imencode('.jpg', placeholder, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                if ret:
                    with self.frame_lock:
                        self.latest_frame = buffer.tobytes()
                with self.state_lock:
                    self.state["status_msg"] = "Chua doc duoc khung hinh tu Camera USB."
                time.sleep(0.1)
                continue
                
            retry_count = 0
            img = cv2.flip(img, 1)
            current_time = time.time()
            
            # ----------------------------------------------------
            # 1. KIỂM TRA KHÓA 5 GIÂY BÁO ĐỘNG SOS
            # ----------------------------------------------------
            if current_time < self.sos_lock_until:
                rem = round(self.sos_lock_until - current_time, 1)
                with self.state_lock:
                    self.state["is_sos_active"] = True
                    self.state["status_msg"] = f"🚨 ĐANG BÁO ĐỘNG SOS ({rem}s)... Khóa nhận diện cử chỉ!"
                    self.state["relay_state"] = self.hw.relay_state
                    self.state["num_fingers"] = 0
                    self.state["num_hands"] = 0
                    
                cv2.putText(img, f"SOS ALARM ({rem}s)", (40, 110),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                cv2.putText(img, "GESTURE LOCKED!", (50, 145),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                            
                ret, buffer = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
                if ret:
                    with self.frame_lock:
                        self.latest_frame = buffer.tobytes()
                time.sleep(0.04)
                continue
                
            with self.state_lock:
                if self.state["is_sos_active"]:
                    self.state["is_sos_active"] = False
                    self.state["status_msg"] = "Đã kết thúc báo động SOS. Hệ thống sẵn sàng nhận cử chỉ."

            # ----------------------------------------------------
            # 2. SUY LUẬN AI BÀN TAY (Hỗ trợ tối đa 2 bàn tay)
            # ----------------------------------------------------
            hands, img = detector.findHands(img, draw=True)
            
            fps = int(1 / (current_time - prev_frame_time)) if (current_time - prev_frame_time) > 0 else 30
            prev_frame_time = current_time
            
            num_hands = len(hands) if hands else 0
            detected_num = 0
            
            # A. KHI ĐƯA CẢ 2 BÀN TAY CÙNG LÚC:
            #    YÊU CẦU GIỮ LIÊN TỤC TRONG ĐỦ 1.0 GIÂY MỚI CHUYỂN ĐỔI CHẾ ĐỘ
            pct = 0.0
            hold_duration = 0.0
            
            if num_hands >= 2:
                last_two_hands_seen_time = current_time
                if two_hands_start_time is None:
                    two_hands_start_time = current_time
                    mode_switched_for_current_hold = False
                    
                hold_duration = current_time - two_hands_start_time
                pct = min(1.0, hold_duration / self.HOLD_MODE_DURATION)
                
                with self.state_lock:
                    self.state["num_hands"] = num_hands
                    self.state["num_fingers"] = 0
                    self.state["two_hands_holding"] = True
                    self.state["hold_progress"] = round(pct, 2)
                    self.state["hold_seconds"] = round(hold_duration, 1)
                    
                if not mode_switched_for_current_hold:
                    if hold_duration >= self.HOLD_MODE_DURATION and (current_time - self.last_mode_switch_time >= self.MODE_TOGGLE_COOLDOWN):
                        # ĐÃ GIỮ ĐỦ 1.0S -> CHUYỂN CHẾ ĐỘ NGAY (CHỈ 1 LẦN CHO MỖI LẦN ĐƯA TAY)
                        mode_switched_for_current_hold = True
                        self.last_mode_switch_time = current_time
                        
                        if self.app_mode == "MORSE":
                            self.app_mode = "MOTOR"
                            self.hw.set_motor_speed(1)  # Khởi động quay chậm ban đầu (Mức 1: 35%)
                            self.hw.beep_confirm()
                            with self.state_lock:
                                self.state["app_mode"] = "MOTOR"
                                self.state["motor_speed"] = 1
                                self.state["motor_duty"] = int(self.hw.motor_duty * 100)
                                self.state["motor_running"] = True
                                self.state["current_morse"] = ""
                                self.state["status_msg"] = "🚀 ĐÃ BẬT ĐỘNG CƠ L298N! (Giữ đủ 1.0s). Mức 1: Quay chậm."
                            print("[MODE CHANGE] -> BẬT ĐỘNG CƠ L298N (Giữ 2 tay đủ 1.0s)")
                        else:
                            self.app_mode = "MORSE"
                            self.hw.stop_motor()  # Dừng quay động cơ
                            self.hw.beep_clear()
                            with self.state_lock:
                                self.state["app_mode"] = "MORSE"
                                self.state["motor_speed"] = 0
                                self.state["motor_duty"] = 0
                                self.state["motor_running"] = False
                                self.state["current_morse"] = ""
                                self.state["status_msg"] = "🛑 ĐÃ DỪNG ĐỘNG CƠ! (Giữ đủ 1.0s). Trở về Dịch Morse."
                            print("[MODE CHANGE] -> DỪNG ĐỘNG CƠ, Trở về MORSE (Giữ 2 tay đủ 1.0s)")
                    else:
                        # Đang đếm thời gian giữ 2 tay
                        target_action = "BẬT ĐỘNG CƠ" if self.app_mode == "MORSE" else "DỪNG ĐỘNG CƠ"
                        pct_int = int(pct * 100)
                        with self.state_lock:
                            self.state["status_msg"] = f"⏳ Đang giữ 2 tay: {round(hold_duration, 1)}s/1.0s ({pct_int}%) để {target_action}..."
                else:
                    # Đã chuyển chế độ xong, thông báo người dùng hạ tay xuống
                    with self.state_lock:
                        self.state["status_msg"] = f"✓ Đã chuyển sang chế độ {self.app_mode}! Hạ 2 tay xuống để bắt đầu điều khiển."
            else:
                # Nếu không nhìn thấy 2 tay trong hơn 0.25s thì mới reset bộ đếm giữ tay
                if current_time - last_two_hands_seen_time > 0.25:
                    two_hands_start_time = None
                    mode_switched_for_current_hold = False
                    with self.state_lock:
                        self.state["two_hands_holding"] = False
                        self.state["hold_progress"] = 0.0
                        self.state["hold_seconds"] = 0.0

            # B. KHI ĐƯA 1 BÀN TAY (và an toàn: cách lúc giơ 2 tay ít nhất 0.35s để tránh nhận nhầm khi hạ tay):
            if num_hands == 1 and (current_time - last_two_hands_seen_time > 0.35):
                hand1 = hands[0]
                fingers_array = detector.fingersUp(hand1)
                detected_num = sum(fingers_array)
                
                with self.state_lock:
                    self.state["num_hands"] = 1
                    self.state["num_fingers"] = detected_num

                # B.1. Nếu đang ở CHẾ ĐỘ ĐỘNG CƠ: Chỉnh 5 mức tốc độ từ 1 đến 5 ngón
                if self.app_mode == "MOTOR":
                    if 1 <= detected_num <= 5:
                        if detected_num != self.hw.motor_speed_level and (current_time - last_speed_change_time > self.SPEED_CHANGE_COOLDOWN):
                            self.hw.set_motor_speed(detected_num)
                            self.hw.beep_dot()
                            last_speed_change_time = current_time
                            with self.state_lock:
                                self.state["motor_speed"] = detected_num
                                self.state["motor_duty"] = int(self.hw.motor_duty * 100)
                                self.state["motor_running"] = True
                                self.state["status_msg"] = f"⚙️ ĐỘNG CƠ: Mức {detected_num}/5 ({int(self.hw.motor_duty * 100)}% PWM) | Giữ 2 tay 1s để dừng"

                # B.2. Nếu đang ở CHẾ ĐỘ DỊCH MÃ MORSE:
                else:
                    with self.state_lock:
                        elapsed = current_time - last_action_time
                        if elapsed > self.COOLDOWN_TIME:
                            if detected_num == 1:
                                self.state["current_morse"] += "."
                                self.state["status_msg"] = f"Dấu CHẤM (.) | Morse: {self.state['current_morse']}"
                                self.hw.beep_dot()
                                last_action_time = current_time
                                
                            elif detected_num == 2:
                                self.state["current_morse"] += "-"
                                self.state["status_msg"] = f"Dấu GẠCH (-) | Morse: {self.state['current_morse']}"
                                self.hw.beep_dash()
                                last_action_time = current_time
                                
                            elif detected_num == 4:
                                self.state["decoded_text"] = ""
                                self.state["last_char"] = ""
                                self.state["status_msg"] = "🗑️ ĐÃ XÓA TOÀN BỘ VĂN BẢN ĐÃ DỊCH (4 ngón)."
                                self.hw.beep_clear()
                                last_action_time = current_time
                                
                            elif detected_num == 5:
                                self.state["current_morse"] = ""
                                self.state["status_msg"] = "Đã xóa mã Morse hiện tại (5 ngón)."
                                self.hw.beep_clear()
                                last_action_time = current_time
                                
                            elif detected_num == 0 and self.state["current_morse"]:
                                char = MORSE_DICT.get(self.state["current_morse"], '?')
                                self.state["decoded_text"] += char
                                self.state["last_char"] = char
                                self.state["current_morse"] = ""
                                
                                if char == 'SOS' or self.state["decoded_text"].endswith("SOS"):
                                    self.sos_lock_until = current_time + 5.0
                                    self.hw.trigger_sos()
                                    self.state["status_msg"] = "🚨 PHÁT HIỆN LỆNH SOS! CÒI & ĐÈN BÁO ĐỘNG 5S (KHÓA NHẬN DIỆN)"
                                    last_action_time = self.sos_lock_until
                                else:
                                    self.hw.beep_confirm()
                                    action_info = ""
                                    if char == 'O':
                                        self.hw.set_relay(True)
                                        action_info = " -> BẬT RELAY (Đèn Sáng)"
                                    elif char == 'F':
                                        self.hw.set_relay(False)
                                        action_info = " -> TẮT RELAY (Đèn Tắt)"
                                    elif char == 'T':
                                        self.hw.toggle_relay()
                                        action_info = f" -> ĐẢO RELAY ({'BẬT' if self.hw.relay_state else 'TẮT'})"
                                    elif self.state["decoded_text"].endswith("ON"):
                                        self.hw.set_relay(True)
                                        action_info = " -> BẬT RELAY"
                                    elif self.state["decoded_text"].endswith("OFF"):
                                        self.hw.set_relay(False)
                                        action_info = " -> TẮT RELAY"
                                        
                                    self.state["status_msg"] = f"Dịch: [{char}]{action_info}"
                                    self.state["relay_state"] = self.hw.relay_state
                                    last_action_time = current_time
            elif num_hands == 0:
                with self.state_lock:
                    self.state["num_hands"] = 0
                    self.state["num_fingers"] = 0

            # Cập nhật thông số vào state
            with self.state_lock:
                self.state["app_mode"] = self.app_mode
                self.state["relay_state"] = self.hw.relay_state
                self.state["motor_speed"] = self.hw.motor_speed_level
                self.state["motor_duty"] = int(self.hw.motor_duty * 100)
                self.state["motor_running"] = (self.hw.motor_speed_level > 0)
                cur_m = self.state["current_morse"]
                txt = self.state["decoded_text"]
                r_text = "RELAY: ON" if self.hw.relay_state else "RELAY: OFF"

            # ----------------------------------------------------
            # 3. VẼ GIAO DIỆN HUD LÊN HÌNH ẢNH
            # ----------------------------------------------------
            if self.app_mode == "MOTOR":
                cv2.rectangle(img, (5, 5), (315, 62), (25, 25, 25), -1)
                cv2.rectangle(img, (5, 5), (315, 62), (0, 215, 255), 1)
                cv2.putText(img, f"MODE: MOTOR [MUC {self.hw.motor_speed_level}/5]", (10, 24),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 215, 255), 2)
                bar = "#" * self.hw.motor_speed_level + "-" * (5 - self.hw.motor_speed_level)
                cv2.putText(img, f"PWM: [{bar}] ({int(self.hw.motor_duty * 100)}%)", (10, 42),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 255, 0) if self.hw.motor_speed_level > 0 else (120, 120, 120), 1)
                cv2.putText(img, "Dua 1-5 ngon: Toc do | 2 Tay (1s): Dung", (10, 57),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
            else:
                cv2.putText(img, f"FPS: {fps}", (10, 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
                cv2.putText(img, f"Morse: {cur_m}", (10, 45),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                cv2.putText(img, f"Text: {txt[-10:]}", (10, 225),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (50, 255, 50), 2)
                cv2.putText(img, "2 TAY (1s): Bat Dong Co", (150, 225),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1)

            cv2.putText(img, r_text, (205, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0) if self.hw.relay_state else (100, 100, 100), 1)

            # Vẽ thanh tiến trình giữ 2 bàn tay chuyển mode (Nổi bật ở giữa màn hình camera)
            if num_hands >= 2:
                box_x1, box_y1 = 15, 80
                box_x2, box_y2 = 305, 145
                cv2.rectangle(img, (box_x1, box_y1), (box_x2, box_y2), (15, 15, 15), -1)
                
                if mode_switched_for_current_hold:
                    cv2.rectangle(img, (box_x1, box_y1), (box_x2, box_y2), (0, 255, 0), 2)
                    cv2.putText(img, f"DA CHUYEN -> {self.app_mode}!", (box_x1 + 10, box_y1 + 28),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 0), 2)
                    cv2.putText(img, "Ha 2 tay xuong de bat dau", (box_x1 + 10, box_y1 + 52),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 255, 200), 1)
                else:
                    cv2.rectangle(img, (box_x1, box_y1), (box_x2, box_y2), (0, 215, 255), 2)
                    target_txt = "BAT DONG CO" if self.app_mode == "MORSE" else "DUNG DONG CO"
                    cv2.putText(img, f"GIU 2 TAY 1s: {target_txt}", (box_x1 + 10, box_y1 + 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 215, 255), 1)
                    
                    bar_w = 260
                    bar_h = 16
                    bx = box_x1 + 15
                    by = box_y1 + 32
                    filled_w = int(bar_w * pct)
                    cv2.rectangle(img, (bx, by), (bx + bar_w, by + bar_h), (50, 50, 50), -1)
                    cv2.rectangle(img, (bx, by), (bx + filled_w, by + bar_h), (0, 255, 0), -1)
                    cv2.rectangle(img, (bx, by), (bx + bar_w, by + bar_h), (255, 255, 255), 1)
                    
                    cv2.putText(img, f"{round(hold_duration, 1)}s/1.0s ({int(pct * 100)}%)", (bx + 65, by + 12),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 0, 0), 1)

            ret, buffer = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
            if ret:
                with self.frame_lock:
                    self.latest_frame = buffer.tobytes()
                    
        cap.release()
        print("[Core Engine] Đã giải phóng camera.")

    def start(self):
        """Khởi động luồng xử lý AI và Camera ngầm"""
        if self.cam_thread is None or not self.cam_thread.is_alive():
            self.is_running = True
            self.cam_thread = threading.Thread(target=self._tracking_loop, daemon=True)
            self.cam_thread.start()

    def stop(self):
        """Dừng luồng AI"""
        self.is_running = False
        self.hw.stop_motor()

    # ----------------------------------------------------
    # Các Phương Thức Dịch Vụ Cung Cấp Cho Web Server
    # ----------------------------------------------------
    def get_latest_frame(self):
        with self.frame_lock:
            return self.latest_frame

    def get_status(self):
        with self.state_lock:
            self.state["relay_state"] = self.hw.relay_state
            self.state["motor_speed"] = self.hw.motor_speed_level
            self.state["motor_duty"] = int(self.hw.motor_duty * 100)
            self.state["motor_running"] = (self.hw.motor_speed_level > 0)
            self.state["hardware_active"] = self.hw.is_hardware_active
            return dict(self.state)

    def clear_all(self):
        with self.state_lock:
            self.state["current_morse"] = ""
            self.state["decoded_text"] = ""
            self.state["last_char"] = ""
            self.state["status_msg"] = "Đã xóa toàn bộ dữ liệu."

    def backspace(self):
        with self.state_lock:
            if self.state["decoded_text"]:
                self.state["decoded_text"] = self.state["decoded_text"][:-1]
                self.state["status_msg"] = "Đã xóa ký tự cuối."

    def add_space(self):
        with self.state_lock:
            self.state["decoded_text"] += " "
            self.state["status_msg"] = "Đã thêm dấu cách."

    def set_motor_speed(self, level: int):
        self.hw.set_motor_speed(level)
        self.hw.beep_dot()
        with self.state_lock:
            self.state["motor_speed"] = level
            self.state["motor_duty"] = int(self.hw.motor_duty * 100)
            self.state["motor_running"] = (level > 0)
            self.state["status_msg"] = f"⚙️ Động cơ: Mức {level}/5 ({int(self.hw.motor_duty * 100)}% PWM) (Web Command)"

    def stop_motor(self):
        self.hw.stop_motor()
        self.hw.beep_clear()
        with self.state_lock:
            self.state["motor_speed"] = 0
            self.state["motor_duty"] = 0
            self.state["motor_running"] = False
            self.state["status_msg"] = "🛑 Đã dừng động cơ (Web Command)"

    def toggle_mode(self):
        with self.state_lock:
            self.last_mode_switch_time = time.time()
            if self.app_mode == "MORSE":
                self.app_mode = "MOTOR"
                self.hw.set_motor_speed(1)
                self.hw.beep_confirm()
                self.state["app_mode"] = "MOTOR"
                self.state["motor_speed"] = 1
                self.state["motor_duty"] = int(self.hw.motor_duty * 100)
                self.state["motor_running"] = True
                self.state["current_morse"] = ""
                self.state["status_msg"] = "🚀 Chuyển sang CHẾ ĐỘ ĐỘNG CƠ (Mức 1 - Web Command)"
            else:
                self.app_mode = "MORSE"
                self.hw.stop_motor()
                self.hw.beep_clear()
                self.state["app_mode"] = "MORSE"
                self.state["motor_speed"] = 0
                self.state["motor_duty"] = 0
                self.state["motor_running"] = False
                self.state["current_morse"] = ""
                self.state["status_msg"] = "🛑 Dừng động cơ, Chuyển về CHẾ ĐỘ DỊCH MORSE (Web Command)"
        return self.app_mode

    def set_relay(self, state: bool):
        self.hw.set_relay(state)
        with self.state_lock:
            self.state["relay_state"] = state
            self.state["status_msg"] = f"{'Bật' if state else 'Tắt'} Relay (Web Command)"

    def toggle_relay(self):
        self.hw.toggle_relay()
        with self.state_lock:
            self.state["relay_state"] = self.hw.relay_state
            self.state["status_msg"] = f"Đảo Relay: {'BẬT' if self.hw.relay_state else 'TẮT'}"
        return self.hw.relay_state

    def test_buzzer(self):
        self.hw.beep_confirm()

    def trigger_sos(self):
        self.hw.trigger_sos()
        with self.state_lock:
            self.state["is_sos_active"] = True
            self.state["status_msg"] = "🚨 Kích hoạt Báo Động SOS thủ công từ Web (5s)"
