"""Envia o estado (ok / sem_epi / vazio / erro) para o ESP32 dos LEDs."""
from __future__ import annotations

import logging
import time

import requests

from .config import ConfigLed
from .decisao import Estado

log = logging.getLogger(__name__)


class SinalizadorHTTP:
    """Wi-Fi: GET http://IP_DO_ESP32/estado?valor=ok"""

    def __init__(self, url_base: str, timeout_s: float):
        self.url = url_base.rstrip("/") + "/estado"
        self.timeout = timeout_s
        self.sessao = requests.Session()
        self._ok_anterior = True

    def enviar(self, estado: Estado) -> bool:
        try:
            r = self.sessao.get(self.url, params={"valor": estado.value}, timeout=self.timeout)
            ok = r.status_code == 200
            erro = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ok, erro = False, type(e).__name__
        if ok != self._ok_anterior:  # loga so quando muda, para nao poluir o terminal
            if ok:
                log.info("ESP32 dos LEDs respondendo novamente")
            else:
                log.warning("Falha ao falar com o ESP32 dos LEDs em %s (%s)", self.url, erro)
        self._ok_anterior = ok
        return ok

    def fechar(self):
        self.sessao.close()


class SinalizadorSerial:
    """Cabo USB: escreve 'ok\\n' na porta serial do ESP32 (nao precisa de Wi-Fi)."""

    def __init__(self, porta: str, baud: int):
        import serial  # pyserial

        self.ser = serial.Serial()
        self.ser.port = porta
        self.ser.baudrate = baud
        self.ser.timeout = 0
        self.ser.write_timeout = 1
        # Evita que abrir a porta reinicie o ESP32 (DTR/RTS ligados ao EN/IO0).
        self.ser.dtr = False
        self.ser.rts = False
        try:
            self.ser.open()
        except serial.SerialException as e:
            raise RuntimeError(
                f"Nao abriu {porta}. Feche o Monitor Serial do PlatformIO/Arduino e confira a porta. ({e})"
            ) from e
        time.sleep(0.1)
        log.info("Sinalizador serial em %s", porta)

    def enviar(self, estado: Estado) -> bool:
        try:
            self.ser.write(f"{estado.value}\n".encode())
            self.ser.reset_input_buffer()  # descarta as respostas "OK ..." do ESP32
            return True
        except Exception as e:  # noqa: BLE001 - serial pode lancar varios tipos
            log.warning("Falha na serial: %s", e)
            return False

    def fechar(self):
        self.ser.close()


class SinalizadorNulo:
    def enviar(self, estado: Estado) -> bool:
        return True

    def fechar(self):
        pass


def criar_sinalizador(cfg: ConfigLed):
    if cfg.modo == "http":
        log.info("LEDs via Wi-Fi em %s", cfg.url)
        return SinalizadorHTTP(cfg.url, cfg.timeout_s)
    if cfg.modo == "serial":
        return SinalizadorSerial(cfg.porta_serial, cfg.baud)
    log.info("LEDs desativados (led.modo = nenhum)")
    return SinalizadorNulo()
