"""
Web Server & REST API Gateway (Flask)
- Cung cấp giao diện Web Dashboard (HTML5, CSS3, JS)
- Cung cấp Video Feed thời gian thực (MJPEG Multipart Stream)
- Cung cấp RESTful API giao tiếp 2 chiều với Raspberry Pi và Core Engine
"""

import sys
import time
from flask import Flask, Response, render_template, jsonify, request

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from morse_engine import MorseGestureEngine

# ----------------------------------------------------
# 1. Khởi tạo Flask Web App & Core Engine
# ----------------------------------------------------
app = Flask(__name__)

# Khởi tạo Engine điều khiển (Buzzer 17, Relay 27, Motor L298N IN1=22, IN2=23)
engine = MorseGestureEngine(buzzer_pin=17, relay_pin=27, motor_fwd=22, motor_bwd=23)

# ----------------------------------------------------
# 2. Định Tuyến Giao Diện & Video Stream
# ----------------------------------------------------
@app.route('/')
def index():
    """Giao diện Dashboard chính"""
    return render_template('index.html')

def generate_video_stream():
    """Bộ tạo luồng ảnh MJPEG gửi liên tục tới trình duyệt"""
    while True:
        frame = engine.get_latest_frame()
        if frame is not None:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
        time.sleep(0.04)

@app.route('/video_feed')
def video_feed():
    """Endpoint Stream Video MJPEG"""
    return Response(generate_video_stream(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')

# ----------------------------------------------------
# 3. REST API: Lấy Trạng Thái Hệ Thống
# ----------------------------------------------------
@app.route('/api/status')
def api_status():
    """Cung cấp dữ liệu thời gian thực cho Web Polling (250ms)"""
    return jsonify(engine.get_status())

# ----------------------------------------------------
# 4. REST API: Thao Tác Soạn Thảo & Dịch Mã Morse
# ----------------------------------------------------
@app.route('/api/clear', methods=['POST'])
def api_clear():
    engine.clear_all()
    return jsonify({"success": True})

@app.route('/api/backspace', methods=['POST'])
def api_backspace():
    engine.backspace()
    return jsonify({"success": True})

@app.route('/api/space', methods=['POST'])
def api_space():
    engine.add_space()
    return jsonify({"success": True})

# ----------------------------------------------------
# 5. REST API: Điều Khiển Relay CW019 & Còi Buzzer
# ----------------------------------------------------
@app.route('/api/relay/on', methods=['POST'])
def api_relay_on():
    engine.set_relay(True)
    return jsonify({"success": True, "relay_state": True})

@app.route('/api/relay/off', methods=['POST'])
def api_relay_off():
    engine.set_relay(False)
    return jsonify({"success": True, "relay_state": False})

@app.route('/api/relay/toggle', methods=['POST'])
def api_relay_toggle():
    new_state = engine.toggle_relay()
    return jsonify({"success": True, "relay_state": new_state})

@app.route('/api/buzzer/test', methods=['POST'])
def api_buzzer_test():
    engine.test_buzzer()
    return jsonify({"success": True})

@app.route('/api/sos', methods=['POST'])
def api_sos_trigger():
    engine.trigger_sos()
    return jsonify({"success": True})

# ----------------------------------------------------
# 6. REST API: Điều Khiển Động Cơ DC (L298N) & Chuyển Chế Độ
# ----------------------------------------------------
@app.route('/api/motor/set_speed', methods=['POST'])
def api_motor_set_speed():
    data = request.get_json(silent=True) or {}
    level = int(data.get('level', 1))
    engine.set_motor_speed(level)
    return jsonify({
        "success": True,
        "motor_speed": level,
        "motor_duty": engine.hw.motor_duty
    })

@app.route('/api/motor/stop', methods=['POST'])
def api_motor_stop():
    engine.stop_motor()
    return jsonify({"success": True})

@app.route('/api/mode/toggle', methods=['POST'])
def api_mode_toggle():
    new_mode = engine.toggle_mode()
    return jsonify({"success": True, "app_mode": new_mode})

# ----------------------------------------------------
# 7. Khởi Chạy Ứng Dụng
# ----------------------------------------------------
if __name__ == '__main__':
    # Khởi động luồng Engine AI và Camera ngầm
    engine.start()
    
    print("==================================================")
    print("[SERVER] WEB DASHBOARD MORSE & IOT DANG CHAY!")
    print("[SERVER] Buzzer: GPIO 17 | Relay: GPIO 27 | Motor L298N: IN1=GPIO22, IN2=GPIO23")
    print("[SERVER] Tinh nang: 2 Ban tay bat/tat Dong co, 1-5 ngons chinh 5 muc toc do")
    print("[SERVER] Mo trinh duyet tren laptop va truy cap:")
    print("         http://localhost:5000")
    print("         (Hoac tu may khac: http://<IP_RASPBERRY_PI>:5000)")
    print("==================================================")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
