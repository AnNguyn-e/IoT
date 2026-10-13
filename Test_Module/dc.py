from gpiozero import Motor
from time import sleep

motor = Motor(forward=22, backward=23)

print("Đang kiểm tra động cơ...")
print("1. Quay TIẾN (100% tốc độ) trong 3 giây...")
motor.forward()
sleep(3)

print("2. Tạm DỪNG 1 giây...")
motor.stop()
sleep(1)

print("3. Quay LÙI (100% tốc độ) trong 3 giây...")
motor.backward()
sleep(3)

print("4. DỪNG hẳn.")
motor.stop()