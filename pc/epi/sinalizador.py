"""Envia o estado (ok / sem_epi / vazio / erro) para o ESP32 dos LEDs."""
from __future__ import annotations

import logging
import threading
import time

import requests

from .config import ConfigLed
from .decisao import Estado
from .rede import UrlResolvida

log = logging.getLogger(__name__)


class SinalizadorHTTP:
    """Wi-Fi: GET http://IP_DO_ESP32/estado?valor=ok"""

    def __init__(self, url_base: str, timeout_s: float):
        self.url = UrlResolvida(url_base.rstrip("/") + "/estado")
        self.timeout = timeout_s
        self.sessao = requests.Session()
        self._ok_anterior = True

    def enviar(self, estado: Estado) -> bool:
        try:
            r = self.sessao.get(self.url.atual, params={"valor": estado.value}, timeout=self.timeout)
            ok = r.status_code == 200
            erro = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ok, erro = False, type(e).__name__
            self.url.resolver()  # se usar .local, o IP pode ter mudado
        if ok != self._ok_anterior:  # loga so quando muda, para nao poluir o terminal
            if ok:
                log.info("ESP32 dos LEDs respondendo novamente")
            else:
                log.warning("Falha ao falar com o ESP32 dos LEDs em %s (%s)", self.url.original, erro)
        self._ok_anterior = ok
        return ok

    def fechar(self):
        self.sessao.close()


class EnvioEmSegundoPlano:
    """Envia o estado numa thread: o ciclo camera/YOLO nao espera a resposta do ESP32.

    - Estado mudou -> envia na hora.
    - Estado igual -> repete so a cada `batimento_s` (o ESP32 apaga apos 6 s sem sinal).
    O servidor HTTP do ESP32 so tem 16 conexoes TCP e cada uma leva ~2 min para liberar
    depois de fechada; mandando a cada analise (~5/s) ele trava por minutos.
    """

    def __init__(self, sinalizador, batimento_s: float = 1.0):
        self.sinalizador = sinalizador
        self.batimento_s = batimento_s
        self.pendente: Estado | None = None
        self.ultimo_enviado: Estado | None = None
        self.ultimo_envio = 0.0
        self.evento = threading.Event()
        self.lock = threading.Lock()
        self.rodando = True
        self.thread = threading.Thread(target=self._loop, name="envio-led", daemon=True)
        self.thread.start()

    def _loop(self):
        while self.rodando:
            self.evento.wait(timeout=0.2)
            self.evento.clear()
            with self.lock:
                estado = self.pendente
            if estado is None:
                continue
            agora = time.monotonic()
            if estado != self.ultimo_enviado or agora - self.ultimo_envio >= self.batimento_s:
                ok = self.sinalizador.enviar(estado)
                self.ultimo_enviado = estado if ok else None  # falhou: tenta de novo logo
                self.ultimo_envio = agora

    def enviar(self, estado: Estado) -> bool:
        with self.lock:
            self.pendente = estado
        self.evento.set()
        return True

    def fechar(self):
        self.rodando = False
        self.evento.set()
        self.thread.join(timeout=5)
        self.sinalizador.fechar()


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
        return EnvioEmSegundoPlano(SinalizadorHTTP(cfg.url, cfg.timeout_s))
    if cfg.modo == "serial":
        return SinalizadorSerial(cfg.porta_serial, cfg.baud)
    log.info("LEDs desativados (led.modo = nenhum)")
    return SinalizadorNulo()
