from gpiozero import Buzzer
from time import sleep

bz = Buzzer(17)

print("Phát tiếng Tít (Dot)...")
bz.on()
sleep(0.0001)
bz.off()

sleep(0.5)

print("Phát tiếng Tè (Dash)...")
bz.on()
sleep(0.6)
bz.off()