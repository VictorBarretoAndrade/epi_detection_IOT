"""Testa so o ESP32 dos LEDs (nao precisa de camera nem YOLO).

    python testar_led.py ok                 # verde por 5 s (Wi-Fi, usa led.url do config.toml)
    python testar_led.py sem_epi            # vermelho por 5 s
    python testar_led.py ciclo              # alterna ok/sem_epi/vazio/erro
    python testar_led.py ok --serial COM5   # pelo cabo USB em vez do Wi-Fi
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))
from epi.config import carregar  # noqa: E402
from epi.decisao import Estado  # noqa: E402
from epi.sinalizador import SinalizadorHTTP, SinalizadorSerial  # noqa: E402


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("estado", choices=[e.value for e in Estado] + ["ciclo"])
    p.add_argument("--url", help="ex.: http://192.168.0.51")
    p.add_argument("--serial", help="ex.: COM5 (usa o cabo USB)")
    p.add_argument("--segundos", type=float, default=5.0)
    p.add_argument("--config", default=str(PASTA / "config.toml"))
    a = p.parse_args()

    cfg = carregar(a.config).led
    if a.serial:
        sinal = SinalizadorSerial(a.serial, cfg.baud)
        destino = a.serial
    else:
        destino = a.url or cfg.url
        sinal = SinalizadorHTTP(destino, timeout_s=2.0)

    sequencia = list(Estado) if a.estado == "ciclo" else [Estado(a.estado)]
    try:
        for estado in sequencia:
            print(f"Enviando '{estado.value}' para {destino} por {a.segundos:.0f} s ...")
            fim = time.monotonic() + a.segundos
            while time.monotonic() < fim:
                if not sinal.enviar(estado):
                    print("  ERRO: o ESP32 nao respondeu. Confira IP/porta e o Monitor Serial.")
                    return 1
                time.sleep(1.0)  # repete: o ESP32 volta a "sem sinal" apos 6 s sem mensagens
    finally:
        sinal.fechar()
    print("Pronto. Sem novas mensagens o ESP32 volta a piscar vermelho devagar (sem sinal).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
