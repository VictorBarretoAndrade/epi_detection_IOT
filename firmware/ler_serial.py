"""Reinicia o ESP32 e mostra o Monitor Serial por alguns segundos (sem prender a placa em reset).

    python firmware/ler_serial.py COM10            # 20 s
    python firmware/ler_serial.py COM10 --segundos 40
"""
import argparse
import sys
import time

import serial

p = argparse.ArgumentParser()
p.add_argument("porta")
p.add_argument("--segundos", type=float, default=20)
p.add_argument("--sem-reset", action="store_true")
a = p.parse_args()

s = serial.Serial()
s.port, s.baudrate, s.timeout = a.porta, 115200, 0.2
s.dtr = False  # IO0 em nivel alto = boot normal
s.rts = False
s.open()
if not a.sem_reset:
    s.rts = True  # EN em nivel baixo = reset
    time.sleep(0.15)
    s.rts = False
fim = time.monotonic() + a.segundos
try:
    while time.monotonic() < fim:
        linha = s.readline()
        if linha:
            sys.stdout.write(linha.decode("utf-8", "replace"))
            sys.stdout.flush()
finally:
    s.close()
