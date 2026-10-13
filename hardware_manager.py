"""
Module Quản Lý Phần Cứng Raspberry Pi (Hardware Manager)
Điều khiển:
- Active Buzzer: GPIO 17
- Relay CW019 1 kênh: GPIO 27 (kích Low active_high=False)
- Driver Động cơ DC L298N: IN1=GPIO 22, IN2=GPIO 23 (PWM điều tốc)
Hỗ trợ Mock Mode tự động nếu chạy trên môi trường không có GPIO.
"""

import time
import threading

class HardwareManager:
    DUTY_LEVELS = {
        0: 0.0,
        1: 0.35,  # Mức 1: 35% (Quay chậm, vượt ngưỡng sụt áp ~1.5V của chip L298N)
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
        
        self.relay_state = False    # False = TẮT, True = BẬT
        self.motor_speed_level = 0  # 0 = Dừng, 1..5 = 5 mức tốc độ
        self.motor_duty = 0.0       # 0.0 -> 1.0 (0% -> 100%)
        self.is_hardware_active = False
        self.is_sos_running = False
        
        self.buzzer_device = None
        self.relay_device = None
        self.motor_device = None

        try:
            from gpiozero import Buzzer, OutputDevice, Motor
            self.buzzer_device = Buzzer(self.buzzer_pin)
            # Relay CW019 1 kênh: active_high=False (kích mức Low)
            self.relay_device = OutputDevice(self.relay_pin, active_high=False, initial_value=False)
            try:
                # Động cơ DC qua module L298N (IN1: forward, IN2: backward)
                self.motor_device = Motor(forward=self.motor_fwd, backward=self.motor_bwd, pwm=True)
                self.motor_device.stop()
                print(f"[Hardware Init] Motor L298N san sang: IN1=GPIO{self.motor_fwd}, IN2=GPIO{self.motor_bwd}")
            except Exception as e_motor:
                print(f"[Hardware Note] Khong khoi tao duoc Motor L298N ({e_motor}). Dung mo phong.")

            self.is_hardware_active = True
            print(f"[Hardware Init] GPIO KHOI TAO THANH CONG! Buzzer: GPIO{buzzer_pin}, Relay: GPIO{relay_pin}, Motor: IN1={motor_fwd}, IN2={motor_bwd}")
        except Exception as e:
            print(f"[Hardware Note] Khong dung duoc GPIO ({e}). Chuyen sang che do Mo Phong (Mock Mode).")

    # ----------------------------------------------------
    # Điều Khiển Động Cơ DC (L298N)
    # ----------------------------------------------------
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

        status_txt = "DUNG (0%)" if level == 0 else f"MUC {level}/5 ({int(self.motor_duty * 100)}% PWM)"
        print(f"[Motor Control L298N] -> {status_txt}")

    def stop_motor(self):
        """Dừng động cơ ngay lập tức"""
        self.set_motor_speed(0)

    # ----------------------------------------------------
    # Điều Khiển Relay CW019 & Đèn LED
    # ----------------------------------------------------
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

    # ----------------------------------------------------
    # Điều Khiển Active Buzzer (Âm Thanh Morse)
    # ----------------------------------------------------
    def beep_dot(self):
        """Tiếng Tít ngắn cho dấu Chấm (.) - 0.12s"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep, args=(0.12,), daemon=True).start()

    def beep_dash(self):
        """Tiếng Tè dài cho dấu Gạch (-) - 0.35s"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep, args=(0.35,), daemon=True).start()

    def beep_confirm(self):
        """Tiếng Tít kép khi dịch thành công 1 chữ cái hoặc bật chế độ"""
        if not self.is_sos_running:
            threading.Thread(target=self._beep_double, daemon=True).start()

    def beep_clear(self):
        """Tiếng Tít ngắn cảnh báo khi xóa mã hoặc dừng động cơ"""
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
            print("[Mock Buzzer] Double Beep (Xac nhan)")

    # ----------------------------------------------------
    # Báo Động SOS (Còi & Đèn chớp liên tục 5 giây)
    # ----------------------------------------------------
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
