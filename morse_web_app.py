import cv2
import time
import threading
import sys
import os

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from flask import Flask, Response, render_template, jsonify, request
from HandTrackingModule import HandDetector

app = Flask(__name__)

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
    '...---...': 'SOS'  # Hỗ trợ gõ trực tiếp chuỗi SOS
}

# ----------------------------------------------------
# 2. Quản lý Phần cứng: Active Buzzer (GPIO 17) & Relay CW019 (GPIO 27)
# ----------------------------------------------------
class HardwareManager:
    DUTY_LEVELS = {
        0: 0.0,
        1: 0.35,  # Mức 1: Quay chậm (35% - điện áp đủ vượt ngưỡng sụt áp của chip L298N)
        2: 0.50,  # Mức 2: 50%
        3: 0.65,  # Mức 3: 65%
        4: 0.80,  # Mức 4: 80%
        5: 1.00   # Mức 5: 100% (Tối đa)
    }

    def __init__(self, buzzer_pin=17, relay_pin=27, motor_fwd=22, motor_bwd=23):
        self.buzzer_pin = buzzer_pin
        self.relay_pin = relay_pin
        self.motor_fwd = motor_fwd
        self.motor_bwd = motor_bwd
        self.relay_state = False  # False = TẮT, True = BẬT
        self.motor_speed_level = 0  # 0 = Dừng, 1..5 = 5 mức tốc độ
        self.motor_duty = 0.0
        self.is_hardware_active = False
        self.buzzer_device = None
        self.relay_device = None
        self.motor_device = None
        self.is_sos_running = False
        
        try:
            from gpiozero import Buzzer, OutputDevice, Motor
            self.buzzer_device = Buzzer(self.buzzer_pin)
            # Module Relay CW019 1 kênh: mặc định active_high=False (kích mức Low)
            self.relay_device = OutputDevice(self.relay_pin, active_high=False, initial_value=False)
            try:
                # Module L298N: Chân IN1 cắm GPIO 22, Chân IN2 cắm GPIO 23
                self.motor_device = Motor(forward=self.motor_fwd, backward=self.motor_bwd, pwm=True)
                self.motor_device.stop()
                print(f"[Hardware Init] Motor L298N da ket noi tai IN1=GPIO{self.motor_fwd}, IN2=GPIO{self.motor_bwd}")
            except Exception as e_motor:
                print(f"[Hardware Note] Khong khoi tao duoc Motor L298N ({e_motor}). Dung mo phong.")
            self.is_hardware_active = True
            print(f"[Hardware Init] GPIO KHOI TAO THANH CONG! Buzzer: GPIO{buzzer_pin}, Relay: GPIO{relay_pin}, Motor L298N: IN1=GPIO{motor_fwd}, IN2=GPIO{motor_bwd}")
        except Exception as e:
            print(f"[Hardware Note] Khong dung duoc GPIO ({e}). Chuyen sang che do Mo Phong (Mock Mode).")

    def set_motor_speed(self, level: int):
        """Điều chỉnh 5 mức tốc độ động cơ qua L298N (0: Dừng, 1: 35% -> 5: 100%)"""
        level = max(0, min(5, int(level)))
        self.motor_speed_level = level
        self.motor_duty = self.DUTY_LEVELS.get(level, 0.0)
        if self.is_hardware_active and self.motor_device:
            try:
                if level == 0 or self.motor_duty <= 0.0:
                    self.motor_device.stop()
                else:
                    self.motor_device.forward(speed=self.motor_duty)
            except Exception as e:
                print(f"[Hardware Error] Motor L298N: {e}")
        status_txt = f"DUNG (0%)" if level == 0 else f"MUC {level}/5 ({int(self.motor_duty * 100)}% PWM)"
        print(f"[Motor Control L298N] -> {status_txt}")

    def stop_motor(self):
        """Dừng động cơ ngay lập tức"""
        self.set_motor_speed(0)

    def beep_dot(self):
        """Tiếng Tít ngắn cho dấu Chấm (.) - 0.12s"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep, args=(0.12,), daemon=True).start()

    def beep_dash(self):
        """Tiếng Tè dài cho dấu Gạch (-) - 0.35s"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep, args=(0.35,), daemon=True).start()

    def beep_confirm(self):
        """Tiếng Tít kép khi dịch thành công 1 chữ cái"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep_double, daemon=True).start()

    def beep_clear(self):
        """Tiếng Tít ngắn cảnh báo khi xóa mã hoặc xóa buffer"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep, args=(0.08,), daemon=True).start()

    def _beep(self, duration):
        if self.is_hardware_active and self.buzzer_device:
            try:
                self.buzzer_device.on()
                time.sleep(duration)
                self.buzzer_device.off()
            except Exception:
                pass
        else:
            print(f"[Mock Buzzer] Beep ({duration}s)")

    def _beep_double(self):
        if self.is_hardware_active and self.buzzer_device:
            try:
                self.buzzer_device.on()
                time.sleep(0.08)
                self.buzzer_device.off()
                time.sleep(0.06)
                self.buzzer_device.on()
                time.sleep(0.08)
                self.buzzer_device.off()
            except Exception:
                pass
        else:
            print("[Mock Buzzer] Double Beep (Dich chu)")

    def trigger_sos(self):
        """Báo động SOS: Còi kêu tít tít và Đèn LED chớp liên tục trong 5 giây"""
        def _sos_routine():
            self.is_sos_running = True
            print("[ALARM SOS] >> BAT DAU BAO DONG SOS TRONG 5 GIAY <<")
            end_time = time.time() + 5.0
            
            while time.time() < end_time:
                # BẬT còi & Relay đèn
                if self.is_hardware_active and self.buzzer_device:
                    try: self.buzzer_device.on()
                    except Exception: pass
                if self.is_hardware_active and self.relay_device:
                    try: self.relay_device.on()
                    except Exception: pass
                self.relay_state = True
                time.sleep(0.12)
                
                # TẮT còi & Relay đèn
                if self.is_hardware_active and self.buzzer_device:
                    try: self.buzzer_device.off()
                    except Exception: pass
                if self.is_hardware_active and self.relay_device:
                    try: self.relay_device.off()
                    except Exception: pass
                self.relay_state = False
                time.sleep(0.12)
                
            # Đảm bảo tắt hoàn toàn sau khi hết 5 giây
            self.set_relay(False)
            self.is_sos_running = False
            print("[ALARM SOS] >> KET THUC BAO DONG SOS (Cho phep tiep tuc cu chi) <<")

        threading.Thread(target=_sos_routine, daemon=True).start()

    def set_relay(self, state: bool):
        self.relay_state = state
        if self.is_hardware_active and self.relay_device:
            try:
                if state:
                    self.relay_device.on()
                else:
                    self.relay_device.off()
            except Exception as e:
                print(f"[Hardware Error] Relay: {e}")
        print(f"[Relay CW019] -> {'BAT (ON) - Den Sang' if state else 'TAT (OFF) - Den Tat'}")

    def toggle_relay(self):
        self.set_relay(not self.relay_state)

hw = HardwareManager(buzzer_pin=17, relay_pin=27, motor_fwd=22, motor_bwd=23)

# ----------------------------------------------------
# 3. Biến trạng thái toàn cục (Thread-safe)
# ----------------------------------------------------
state_lock = threading.Lock()
state = {
    "app_mode": "MORSE",         # "MORSE" (Dịch mã Morse) hoặc "MOTOR" (Điều khiển Động cơ)
    "current_morse": "",
    "decoded_text": "",
    "last_char": "",
    "num_fingers": 0,
    "num_hands": 0,
    "status_msg": "Hệ thống sẵn sàng (Chế độ Dịch Morse)",
    "relay_state": False,
    "motor_speed": 0,            # 0..5 (0: Dừng, 1: Chậm -> 5: Tối đa)
    "motor_duty": 0,             # 0..100% PWM
    "motor_running": False,
    "hardware_active": hw.is_hardware_active,
    "is_sos_active": False,
    "cooldown_remaining": 0.0
}

latest_frame = None
frame_lock = threading.Lock()
is_running = True

COOLDOWN_TIME = 0.8

# ----------------------------------------------------
# 4. Luồng Đọc Camera & Xử Lý AI Nhận Diện Bàn Tay
# ----------------------------------------------------
def get_working_camera():
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
            
    print("[Camera Discovery] !! Khong tim thay camera kha dung o cac index 0..4 !!")
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap

def camera_tracking_loop():
    global latest_frame, is_running, state
    
    cap = get_working_camera()
    detector = HandDetector(detectionCon=0.5, maxHands=2, modelComplexity=0)
    app_mode = "MORSE"  # "MORSE" hoặc "MOTOR"
    last_action_time = time.time()
    last_mode_toggle_time = 0.0
    last_speed_change_time = 0.0
    MODE_TOGGLE_COOLDOWN = 1.3  # Giây chống rung khi đưa 2 bàn tay
    SPEED_CHANGE_COOLDOWN = 0.5 # Giây chống rung khi đổi mức tốc độ
    sos_lock_until = 0.0  # Mốc thời gian khóa nhận diện khi SOS kích hoạt
    
    print("[Camera Loop] Bat dau doc camera va nhan dien tay (Co SOS, Motor 2 tay & 1-5 ngons)...")
    retry_count = 0
    prev_frame_time = time.time()
    
    import numpy as np
    while is_running:
        success, img = cap.read()
        if not success:
            retry_count += 1
            if retry_count % 30 == 0:
                print("[Camera Loop] Dang thu ket noi lai camera...")
                cap.release()
                cap = get_working_camera()
                
            placeholder = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(placeholder, "DANG KET NOI CAMERA...", (30, 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1)
            ret, buffer = cv2.imencode('.jpg', placeholder, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
            if ret:
                with frame_lock:
                    latest_frame = buffer.tobytes()
            with state_lock:
                state["status_msg"] = "Chua doc duoc khung hinh tu Camera USB."
            time.sleep(0.1)
            continue
            
        retry_count = 0
        img = cv2.flip(img, 1)
        current_time = time.time()
        
        # ----------------------------------------------------
        # KIỂM TRA TRẠNG THÁI KHÓA 5 GIÂY SOS
        # ----------------------------------------------------
        if current_time < sos_lock_until:
            rem = round(sos_lock_until - current_time, 1)
            with state_lock:
                state["is_sos_active"] = True
                state["status_msg"] = f"🚨 ĐANG BÁO ĐỘNG SOS ({rem}s)... Khóa nhận diện cử chỉ!"
                state["relay_state"] = hw.relay_state
                state["num_fingers"] = 0
                state["num_hands"] = 0
                
            # Vẽ thông báo cảnh báo SOS trực tiếp lên video stream
            cv2.putText(img, f"SOS ALARM ({rem}s)", (40, 110),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.putText(img, "GESTURE LOCKED!", (50, 145),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                        
            ret, buffer = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
            if ret:
                with frame_lock:
                    latest_frame = buffer.tobytes()
            time.sleep(0.04)
            continue  # Bỏ qua hoàn toàn việc nhận diện cử chỉ trong 5s này!
            
        # Hết 5s SOS: mở lại trạng thái bình thường
        with state_lock:
            if state["is_sos_active"]:
                state["is_sos_active"] = False
                state["status_msg"] = "Đã kết thúc báo động SOS. Hệ thống sẵn sàng nhận cử chỉ."

        # Nhận diện bàn tay (Hỗ trợ tối đa 2 bàn tay)
        hands, img = detector.findHands(img, draw=True)
        
        fps = int(1 / (current_time - prev_frame_time)) if (current_time - prev_frame_time) > 0 else 30
        prev_frame_time = current_time
        
        num_hands = len(hands) if hands else 0
        detected_num = 0
        
        # ----------------------------------------------------
        # 1. XỬ LÝ KHI ĐƯA CẢ 2 BÀN TAY LÊN:
        #    - Nếu đang Morse -> Bật ngay động cơ quay chậm (Mức 1), chuyển Motor mode
        #    - Nếu đang Motor -> Dừng động cơ (Mức 0), quay về Morse mode
        # ----------------------------------------------------
        if num_hands >= 2:
            with state_lock:
                state["num_hands"] = num_hands
                state["num_fingers"] = 0
                
            if current_time - last_mode_toggle_time > MODE_TOGGLE_COOLDOWN:
                last_mode_toggle_time = current_time
                if app_mode == "MORSE":
                    app_mode = "MOTOR"
                    hw.set_motor_speed(1)  # Khởi động quay chậm ban đầu (Mức 1: 25%)
                    hw.beep_confirm()
                    with state_lock:
                        state["app_mode"] = "MOTOR"
                        state["motor_speed"] = 1
                        state["motor_duty"] = int(hw.motor_duty * 100)
                        state["motor_running"] = True
                        state["status_msg"] = "🚀 ĐÃ BẬT ĐỘNG CƠ! Ban đầu: Mức 1 (Quay chậm). Đưa 1-5 ngón để chỉnh tốc độ."
                    print("[MODE CHANGE] -> BẬT ĐỘNG CƠ (Mức 1: 25%)")
                else:
                    app_mode = "MORSE"
                    hw.stop_motor()  # Dừng quay động cơ
                    hw.beep_clear()
                    with state_lock:
                        state["app_mode"] = "MORSE"
                        state["motor_speed"] = 0
                        state["motor_duty"] = 0
                        state["motor_running"] = False
                        state["status_msg"] = "🛑 ĐÃ DỪNG ĐỘNG CƠ. Trở về chế độ DỊCH MÃ MORSE."
                    print("[MODE CHANGE] -> DỪNG ĐỘNG CƠ, Trở về MORSE")

        # ----------------------------------------------------
        # 2. XỬ LÝ KHI ĐƯA 1 BÀN TAY:
        # ----------------------------------------------------
        elif num_hands == 1:
            hand1 = hands[0]
            fingers_array = detector.fingersUp(hand1)
            detected_num = sum(fingers_array)
            
            with state_lock:
                state["num_hands"] = 1
                state["num_fingers"] = detected_num

            # A. Nếu đang ở CHẾ ĐỘ ĐỘNG CƠ:
            #    Đưa lần lượt các ngón từ 1 đến 5 để điều chỉnh 5 mức tốc độ quay
            if app_mode == "MOTOR":
                if 1 <= detected_num <= 5:
                    if detected_num != hw.motor_speed_level and (current_time - last_speed_change_time > SPEED_CHANGE_COOLDOWN):
                        hw.set_motor_speed(detected_num)
                        hw.beep_dot()  # Tiếng pip nhẹ xác nhận đổi mức
                        last_speed_change_time = current_time
                        with state_lock:
                            state["motor_speed"] = detected_num
                            state["motor_duty"] = int(hw.motor_duty * 100)
                            state["motor_running"] = True
                            state["status_msg"] = f"⚙️ ĐỘNG CƠ: Mức {detected_num}/5 ({int(hw.motor_duty * 100)}% PWM) | Đưa 2 tay để dừng"

            # B. Nếu đang ở CHẾ ĐỘ DỊCH MÃ MORSE:
            else:
                with state_lock:
                    elapsed = current_time - last_action_time
                    if elapsed > COOLDOWN_TIME:
                        if detected_num == 1:
                            # 1 ngón: Dấu Chấm (.) -> Còi kêu Tít ngắn
                            state["current_morse"] += "."
                            state["status_msg"] = f"Dấu CHẤM (.) | Morse: {state['current_morse']}"
                            hw.beep_dot()
                            last_action_time = current_time
                            
                        elif detected_num == 2:
                            # 2 ngón: Dấu Gạch (-) -> Còi kêu Tè dài
                            state["current_morse"] += "-"
                            state["status_msg"] = f"Dấu GẠCH (-) | Morse: {state['current_morse']}"
                            hw.beep_dash()
                            last_action_time = current_time
                            
                        elif detected_num == 4:
                            # 4 ngón: XÓA TOÀN BỘ CHỮ ĐÃ DỊCH TRONG BUFFER
                            state["decoded_text"] = ""
                            state["last_char"] = ""
                            state["status_msg"] = "🗑️ ĐÃ XÓA TOÀN BỘ VĂN BẢN ĐÃ DỊCH (4 ngón)."
                            hw.beep_clear()
                            last_action_time = current_time
                            
                        elif detected_num == 5:
                            # 5 ngón: Xóa mã Morse đang gõ dở
                            state["current_morse"] = ""
                            state["status_msg"] = "Đã xóa mã Morse hiện tại (5 ngón)."
                            hw.beep_clear()
                            last_action_time = current_time
                            
                        elif detected_num == 0 and state["current_morse"]:
                            # 0 ngón (Nắm tay): Dịch mã sang chữ cái & Kiểm tra lệnh SOS/Thiết bị
                            char = MORSE_DICT.get(state["current_morse"], '?')
                            state["decoded_text"] += char
                            state["last_char"] = char
                            state["current_morse"] = ""
                            
                            # KIỂM TRA LỆNH SOS (Gõ ra chữ SOS hoặc từ kết thúc bằng SOS)
                            if char == 'SOS' or state["decoded_text"].endswith("SOS"):
                                sos_lock_until = current_time + 5.0
                                hw.trigger_sos()
                                state["status_msg"] = "🚨 PHÁT HIỆN LỆNH SOS! CÒI & ĐÈN BÁO ĐỘNG 5S (KHÓA NHẬN DIỆN)"
                                last_action_time = sos_lock_until
                            else:
                                hw.beep_confirm()
                                action_info = ""
                                if char == 'O':
                                    hw.set_relay(True)
                                    action_info = " -> BẬT RELAY (Đèn Sáng)"
                                elif char == 'F':
                                    hw.set_relay(False)
                                    action_info = " -> TẮT RELAY (Đèn Tắt)"
                                elif char == 'T':
                                    hw.toggle_relay()
                                    action_info = f" -> ĐẢO RELAY ({'BẬT' if hw.relay_state else 'TẮT'})"
                                elif state["decoded_text"].endswith("ON"):
                                    hw.set_relay(True)
                                    action_info = " -> BẬT RELAY"
                                elif state["decoded_text"].endswith("OFF"):
                                    hw.set_relay(False)
                                    action_info = " -> TẮT RELAY"
                                    
                                state["status_msg"] = f"Dịch: [{char}]{action_info}"
                                state["relay_state"] = hw.relay_state
                                last_action_time = current_time
        else:
            with state_lock:
                state["num_hands"] = 0
                state["num_fingers"] = 0
                
        # Cập nhật thông số vào state toàn cục
        with state_lock:
            state["app_mode"] = app_mode
            state["relay_state"] = hw.relay_state
            state["motor_speed"] = hw.motor_speed_level
            state["motor_duty"] = int(hw.motor_duty * 100)
            state["motor_running"] = (hw.motor_speed_level > 0)
            cur_m = state["current_morse"]
            txt = state["decoded_text"]
            r_text = "RELAY: ON" if hw.relay_state else "RELAY: OFF"

        # ----------------------------------------------------
        # 3. VẼ GIAO DIỆN TRỰC QUAN (HUD) LÊN STREAM CAMERA
        # ----------------------------------------------------
        if app_mode == "MOTOR":
            # HUD Chế độ ĐIỀU KHIỂN ĐỘNG CƠ
            cv2.rectangle(img, (5, 5), (315, 62), (25, 25, 25), -1)
            cv2.rectangle(img, (5, 5), (315, 62), (0, 215, 255), 1)
            cv2.putText(img, f"MODE: MOTOR [MUC {hw.motor_speed_level}/5]", (10, 24),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 255, 255), 2)
            bar = "#" * hw.motor_speed_level + "-" * (5 - hw.motor_speed_level)
            cv2.putText(img, f"PWM: [{bar}] ({int(hw.motor_duty * 100)}%)", (10, 42),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.44, (0, 255, 0) if hw.motor_speed_level > 0 else (120, 120, 120), 1)
            cv2.putText(img, "Dua 1-5 ngon: Toc do | 2 Tay: Dung", (10, 57),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, (200, 200, 200), 1)
        else:
            # HUD Chế độ DỊCH MÃ MORSE
            cv2.putText(img, f"FPS: {fps}", (10, 20),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
            cv2.putText(img, f"Morse: {cur_m}", (10, 45),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            cv2.putText(img, f"Text: {txt[-10:]}", (10, 225),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (50, 255, 50), 2)
            cv2.putText(img, "2 TAY: Bat Dong Co", (170, 225),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 255), 1)

        cv2.putText(img, r_text, (205, 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0) if hw.relay_state else (100, 100, 100), 1)
                    
        ret, buffer = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 50])
        if ret:
            with frame_lock:
                latest_frame = buffer.tobytes()
                
    cap.release()
    print("[Camera Loop] Đã giải phóng camera.")

# ----------------------------------------------------
# 5. Các Tuyến Định Tuyến Web & REST API
# ----------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')

def generate_video_stream():
    global latest_frame, is_running
    while is_running:
        with frame_lock:
            frame = latest_frame
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.04)

@app.route('/video_feed')
def video_feed():
    return Response(generate_video_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/api/status')
def api_status():
    with state_lock:
        state["relay_state"] = hw.relay_state
        state["motor_speed"] = hw.motor_speed_level
        state["motor_duty"] = int(hw.motor_duty * 100)
        state["motor_running"] = (hw.motor_speed_level > 0)
        state["hardware_active"] = hw.is_hardware_active
        return jsonify(state)

@app.route('/api/clear', methods=['POST'])
def api_clear():
    with state_lock:
        state["current_morse"] = ""
        state["decoded_text"] = ""
        state["last_char"] = ""
        state["status_msg"] = "Đã xóa toàn bộ dữ liệu."
    return jsonify({"success": True})

@app.route('/api/backspace', methods=['POST'])
def api_backspace():
    with state_lock:
        if state["decoded_text"]:
            state["decoded_text"] = state["decoded_text"][:-1]
            state["status_msg"] = "Đã xóa ký tự cuối."
    return jsonify({"success": True})

@app.route('/api/space', methods=['POST'])
def api_space():
    with state_lock:
        state["decoded_text"] += " "
        state["status_msg"] = "Đã thêm dấu cách."
    return jsonify({"success": True})

# API Điều khiển Phần cứng
@app.route('/api/relay/on', methods=['POST'])
def api_relay_on():
    hw.set_relay(True)
    with state_lock:
        state["relay_state"] = True
        state["status_msg"] = "Bật Relay (Web Command)"
    return jsonify({"success": True, "relay_state": True})

@app.route('/api/relay/off', methods=['POST'])
def api_relay_off():
    hw.set_relay(False)
    with state_lock:
        state["relay_state"] = False
        state["status_msg"] = "Tắt Relay (Web Command)"
    return jsonify({"success": True, "relay_state": False})

@app.route('/api/relay/toggle', methods=['POST'])
def api_relay_toggle():
    hw.toggle_relay()
    with state_lock:
        state["relay_state"] = hw.relay_state
        state["status_msg"] = f"Đảo Relay: {'BẬT' if hw.relay_state else 'TẮT'}"
    return jsonify({"success": True, "relay_state": hw.relay_state})

@app.route('/api/buzzer/test', methods=['POST'])
def api_buzzer_test():
    hw.beep_confirm()
    return jsonify({"success": True})

@app.route('/api/sos', methods=['POST'])
def api_sos_trigger():
    hw.trigger_sos()
    with state_lock:
        state["is_sos_active"] = True
        state["status_msg"] = "🚨 Kích hoạt Báo Động SOS thủ công từ Web (5s)"
    return jsonify({"success": True})

# API Điều khiển Động Cơ (DC Motor PWM)
@app.route('/api/motor/set_speed', methods=['POST'])
def api_motor_set_speed():
    data = request.get_json(silent=True) or {}
    level = int(data.get('level', 1))
    hw.set_motor_speed(level)
    hw.beep_dot()
    with state_lock:
        state["motor_speed"] = level
        state["motor_duty"] = int(hw.motor_duty * 100)
        state["motor_running"] = (level > 0)
        state["status_msg"] = f"⚙️ Động cơ: Mức {level}/5 ({int(hw.motor_duty * 100)}% PWM) (Web Command)"
    return jsonify({"success": True, "motor_speed": level, "motor_duty": hw.motor_duty})

@app.route('/api/motor/stop', methods=['POST'])
def api_motor_stop():
    hw.stop_motor()
    hw.beep_clear()
    with state_lock:
        state["motor_speed"] = 0
        state["motor_duty"] = 0
        state["motor_running"] = False
        state["status_msg"] = "🛑 Đã dừng động cơ (Web Command)"
    return jsonify({"success": True})

@app.route('/api/mode/toggle', methods=['POST'])
def api_mode_toggle():
    with state_lock:
        current_m = state.get("app_mode", "MORSE")
        if current_m == "MORSE":
            new_m = "MOTOR"
            hw.set_motor_speed(1)
            hw.beep_confirm()
            state["app_mode"] = "MOTOR"
            state["motor_speed"] = 1
            state["motor_duty"] = int(hw.motor_duty * 100)
            state["motor_running"] = True
            state["status_msg"] = "🚀 Chuyển sang CHẾ ĐỘ ĐỘNG CƠ (Mức 1 - Web Command)"
        else:
            new_m = "MORSE"
            hw.stop_motor()
            hw.beep_clear()
            state["app_mode"] = "MORSE"
            state["motor_speed"] = 0
            state["motor_duty"] = 0
            state["motor_running"] = False
            state["status_msg"] = "🛑 Dừng động cơ, Chuyển về CHẾ ĐỘ DỊCH MORSE (Web Command)"
    return jsonify({"success": True, "app_mode": new_m})

if __name__ == '__main__':
    cam_thread = threading.Thread(target=camera_tracking_loop, daemon=True)
    cam_thread.start()
    
    print("==================================================")
    print("[SERVER] WEB DASHBOARD MORSE & IOT DANG CHAY!")
    print("[SERVER] Buzzer: GPIO 17 | Relay: GPIO 27 | Motor L298N: IN1=GPIO22, IN2=GPIO23")
    print("[SERVER] Tinh nang: 2 Ban tay bat/tat Dong co, 1-5 ngons chinh 5 muc toc do")
    print("[SERVER] Mo trinh duyet tren laptop va truy cap:")
    print("         http://localhost:5000")
    print("         (Hoac tu may khac: http://<IP_RASPBERRY_PI>:5000)")
    print("==================================================")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
