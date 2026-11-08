# M5Stack Core2 + Roller485 Lite x2
# PD gain optimization experiment (UIFlow2 MicroPython)
# Configure Wi-Fi and PC server address before use.
import M5
import time
import math
import gc
import network
import requests
import json
from machine import I2C, Pin
from unit import Roller485Unit

# Network settings (replace with your own values)
SSID = 'YOUR_WIFI_SSID'
PASSWORD = 'YOUR_WIFI_PASSWORD'
SERVER = 'http://192.168.1.100:8000'

# Control and evaluation settings
MAX_CURRENT = 400
PERIOD_US = 10000
FALL_ANGLE = 20.0
TAU = 0.5
TRIAL_MS = 10000
EVAL_START_MS = 2000

CYAN = 0x00FFFF
YELLOW = 0xFFFF00
BLUE = 0x0000FF
GREEN = 0x00FF00
PURPLE = 0x800080
RED = 0xFF0000

motor1 = None
motor2 = None

def show_color(color):
    M5.Lcd.fillScreen(color)

def stop_motors():
    for motor in (motor1, motor2):
        if motor is not None:
            motor.set_motor_max_current(0)
            motor.set_motor_output_state(0)

def initialize_motors():
    global motor1, motor2
    print('Initializing I2C...')
    i2c = I2C(0, scl=Pin(33), sda=Pin(32), freq=100000)
    print('I2C scan:', i2c.scan())
    print('Initializing motors...')
    motor1 = Roller485Unit(i2c, address=0x64, mode=Roller485Unit.I2C_MODE)
    motor2 = Roller485Unit(i2c, address=0x65, mode=Roller485Unit.I2C_MODE)
    motor1.set_motor_mode(3)
    motor2.set_motor_mode(3)
    stop_motors()
    print('Motor initialization OK')

def wait_button():
    released_count = 0
    while released_count < 10:
        M5.update()
        if not M5.BtnA.isPressed():
            released_count += 1
        else:
            released_count = 0
        time.sleep_ms(20)
    print('Waiting for button A...')
    pressed_count = 0
    while pressed_count < 3:
        M5.update()
        if M5.BtnA.isPressed():
            pressed_count += 1
        else:
            pressed_count = 0
        time.sleep_ms(20)
    print('Button A pressed')
    while True:
        M5.update()
        if not M5.BtnA.isPressed():
            break
        time.sleep_ms(20)

def connect_wifi():
    show_color(CYAN)
    print('Connecting WiFi...')
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(SSID, PASSWORD)
    for _ in range(30):
        if wlan.isconnected():
            print('WiFi:', wlan.ifconfig()[0])
            return
        time.sleep_ms(500)
    raise RuntimeError('WiFi connection failed')

def get_gains():
    response = None
    try:
        print('Requesting gains...')
        response = requests.get(SERVER + '/gains')
        if response.status_code != 200:
            raise RuntimeError('GET status ' + str(response.status_code))
        gains = json.loads(response.text)
        print('Gains:', gains)
        return gains
    finally:
        if response is not None:
            response.close()

def send_result(result):
    response = None
    try:
        print('Sending result...')
        response = requests.post(
            SERVER + '/result',
            data=json.dumps(result),
            headers={'Content-Type': 'application/json'}
        )
        print('POST status:', response.status_code)
        print('POST response:', response.text)
        return response.status_code == 200
    finally:
        if response is not None:
            response.close()

def calibrate():
    show_color(YELLOW)
    print('Calibrating...')
    print('Keep Core2 horizontal')
    angle_sum = 0.0
    gx_sum = 0.0
    n = 200
    for _ in range(n):
        ax, ay, az = M5.Imu.getAccel()
        gx, gy, gz = M5.Imu.getGyro()
        angle_sum += math.degrees(math.atan2(ay, az))
        gx_sum += gx
        time.sleep_ms(10)
    angle_offset = angle_sum / n
    gyro_offset = gx_sum / n
    print('Angle offset:', angle_offset)
    print('Gyro offset:', gyro_offset)
    return angle_offset, gyro_offset

def run_trial(kp, kd, angle_offset, gyro_offset):
    print('Starting trial...')
    ax, ay, az = M5.Imu.getAccel()
    angle = math.degrees(math.atan2(ay, az))
    sum_sq = 0.0
    samples = 0
    success = True
    elapsed_ms = 0
    gc.collect()
    gc.disable()
    try:
        motor1.set_motor_output_state(1)
        motor2.set_motor_output_state(1)
        show_color(GREEN)
        start_us = time.ticks_us()
        previous_us = start_us
        next_us = start_us
        while True:
            now_us = time.ticks_us()
            elapsed_ms = time.ticks_diff(now_us, start_us) // 1000
            if elapsed_ms >= TRIAL_MS:
                break
            dt = time.ticks_diff(now_us, previous_us) / 1000000.0
            previous_us = now_us
            if dt <= 0 or dt > 0.02:
                dt = PERIOD_US / 1000000.0
            ax, ay, az = M5.Imu.getAccel()
            gx, gy, gz = M5.Imu.getGyro()
            gx -= gyro_offset
            accel_angle = math.degrees(math.atan2(ay, az))
            alpha = TAU / (TAU + dt)
            angle = alpha * (angle + gx * dt) + (1 - alpha) * accel_angle
            theta = angle - angle_offset
            if abs(theta) > FALL_ANGLE:
                success = False
                print('FALL:', theta)
                break
            u = -kp * theta - kd * gx
            current = int(max(-MAX_CURRENT, min(MAX_CURRENT, u)))
            motor1.set_motor_max_current(current)
            motor2.set_motor_max_current(-current)
            if elapsed_ms >= EVAL_START_MS:
                sum_sq += theta * theta
                samples += 1
            next_us = time.ticks_add(next_us, PERIOD_US)
            while time.ticks_diff(next_us, time.ticks_us()) > 0:
                pass
    finally:
        stop_motors()
        gc.enable()
    if success and samples > 0:
        rms = math.sqrt(sum_sq / samples)
    else:
        rms = None
    return {
        'rms': rms,
        'success': success,
        'elapsed_ms': elapsed_ms,
        'samples': samples
    }

try:
    print('Program starting...')
    M5.begin()
    show_color(CYAN)
    print('M5 initialized')
    initialize_motors()
    connect_wifi()
    gains = get_gains()
    trial = int(gains['trial'])
    kp = float(gains['kp'])
    kd = float(gains['kd'])
    print('Trial:', trial)
    print('Kp:', kp)
    print('Kd:', kd)
    print('Place on horizontal jig')
    print('Press A to calibrate')
    show_color(YELLOW)
    wait_button()
    angle_offset, gyro_offset = calibrate()
    print('Place on inclined jig')
    print('Press A to start trial')
    show_color(BLUE)
    wait_button()
    result = run_trial(kp, kd, angle_offset, gyro_offset)
    result['trial'] = trial
    result['kp'] = kp
    result['kd'] = kd
    print('Result:', result)
    show_color(PURPLE)
    ok = send_result(result)
    if ok:
        print('Result uploaded successfully')
        show_color(GREEN)
    else:
        print('Result upload failed')
        show_color(RED)
except Exception as e:
    print('ERROR:', e)
    try:
        stop_motors()
    except Exception as motor_error:
        print('Motor stop error:', motor_error)
    try:
        show_color(RED)
    except Exception:
        pass
