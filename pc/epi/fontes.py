"""De onde vem a imagem: ESP32-CAM (HTTP), webcam do PC ou uma pasta de fotos."""
from __future__ import annotations

import logging
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import cv2
import numpy as np
import requests

from .config import Config
from .rede import UrlResolvida

log = logging.getLogger(__name__)


class ErroCamera(Exception):
    pass


class FonteESP32CAM:
    def __init__(self, url: str, flash: bool, timeout_s: float, resolucao: str = "", qualidade: int = 0):
        self.url = UrlResolvida(url)
        self.params = {"flash": "1"} if flash else None
        self.timeout = (min(2.0, timeout_s), timeout_s)  # (conectar, ler)
        self.sessao = requests.Session()
        self.ajustes = {}
        if resolucao:
            self.ajustes["resolucao"] = resolucao
        if qualidade:
            self.ajustes["qualidade"] = str(qualidade)
        self.precisa_ajustar = bool(self.ajustes)

    def _aplicar_ajustes(self):
        """Manda resolucao/qualidade para a camera (de novo se ela reiniciar)."""
        partes = urlsplit(self.url.atual)  # /config fica sempre na porta 80
        url = urlunsplit((partes.scheme, partes.hostname, "/config", "", ""))
        try:
            r = self.sessao.get(url, params=self.ajustes, timeout=self.timeout)
        except requests.RequestException:
            return  # tenta de novo no proximo ciclo
        if r.status_code == 200:
            info = r.json()
            log.info("Camera: resolucao=%s qualidade=%s", info.get("resolucao"), info.get("qualidade"))
        elif r.status_code == 404:
            log.warning("Firmware da ESP32-CAM sem /config: regrave para ajustar a resolucao pelo PC.")
        else:
            log.warning("Camera recusou %s: %s", self.ajustes, r.text[:80])
        self.precisa_ajustar = False

    def ler(self) -> np.ndarray:
        if self.precisa_ajustar:
            self._aplicar_ajustes()
        try:
            r = self.sessao.get(self.url.atual, params=self.params, timeout=self.timeout)
        except requests.RequestException as e:
            self.url.resolver()  # se usar .local, o IP pode ter mudado
            self.precisa_ajustar = bool(self.ajustes)  # a camera pode ter reiniciado
            raise ErroCamera(f"ESP32-CAM inacessivel em {self.url.original}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ErroCamera(f"ESP32-CAM respondeu HTTP {r.status_code}: {r.text[:80]}")
        imagem = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if imagem is None:
            raise ErroCamera(f"Resposta nao e um JPEG valido ({len(r.content)} bytes)")
        return imagem

    def fechar(self):
        self.sessao.close()


class FonteVideoESP32CAM(FonteESP32CAM):
    """Video MJPEG continuo (porta 81 /stream): uma conexao so, quadros chegando sem pedir.

    Bem menos latencia que pedir foto por foto (sem abrir conexao a cada quadro) e nao
    esgota as 16 conexoes TCP do ESP32.
    """

    CAB_FIM = b"\r\n\r\n"
    TAMANHO = re.compile(rb"content-length:\s*(\d+)", re.IGNORECASE)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.resposta = None
        self.quadros = None

    def _conectar(self):
        try:
            self.resposta = self.sessao.get(self.url.atual, stream=True, timeout=self.timeout)
        except requests.RequestException as e:
            self.url.resolver()
            self.precisa_ajustar = bool(self.ajustes)
            raise ErroCamera(f"Video da ESP32-CAM inacessivel em {self.url.original}: {type(e).__name__}") from e
        if self.resposta.status_code != 200:
            self._desconectar()
            raise ErroCamera(f"ESP32-CAM respondeu HTTP {self.resposta.status_code} no video")
        self.quadros = self._separar_quadros(self.resposta)
        log.info("Video MJPEG conectado")

    def _separar_quadros(self, resposta):
        buf = b""
        for pedaco in resposta.iter_content(chunk_size=None):
            buf += pedaco
            while True:
                fim = buf.find(self.CAB_FIM)
                if fim < 0:
                    break
                m = self.TAMANHO.search(buf, 0, fim)
                if m is None:  # lixo antes do cabecalho: descarta
                    buf = buf[fim + 4:]
                    continue
                inicio, n = fim + 4, int(m.group(1))
                if len(buf) < inicio + n:
                    break  # quadro ainda chegando
                yield buf[inicio:inicio + n]
                buf = buf[inicio + n:]

    def _desconectar(self):
        if self.resposta is not None:
            self.resposta.close()
        self.resposta = self.quadros = None

    def ler(self) -> np.ndarray:
        if self.precisa_ajustar:
            self._aplicar_ajustes()
        if self.quadros is None:
            self._conectar()
        try:
            jpeg = next(self.quadros)
        except (StopIteration, requests.RequestException, OSError) as e:
            self._desconectar()
            self.precisa_ajustar = bool(self.ajustes)
            raise ErroCamera(f"Video da ESP32-CAM caiu: {type(e).__name__}") from e
        imagem = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if imagem is None:
            raise ErroCamera("Quadro do video nao e um JPEG valido")
        return imagem

    def fechar(self):
        self._desconectar()
        super().fechar()


class FonteWebcam:
    """Webcam do notebook: para testar o sistema sem a ESP32-CAM."""

    def __init__(self, indice: int):
        api = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        self.cap = cv2.VideoCapture(indice, api)
        if not self.cap.isOpened():
            raise ErroCamera(f"Nao abriu a webcam {indice}. Tente webcam_indice = 1")
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def ler(self) -> np.ndarray:
        self.cap.grab()  # descarta quadro antigo do buffer
        ok, imagem = self.cap.read()
        if not ok or imagem is None:
            raise ErroCamera("Falha ao ler a webcam")
        return imagem

    def fechar(self):
        self.cap.release()


class FontePasta:
    """Percorre as imagens de uma pasta em loop: teste sem hardware nenhum."""

    EXTENSOES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

    def __init__(self, pasta: Path):
        self.arquivos = sorted(p for p in Path(pasta).glob("*") if p.suffix.lower() in self.EXTENSOES)
        if not self.arquivos:
            raise ErroCamera(f"Nenhuma imagem em {pasta}")
        self.i = 0
        self.atual: Path | None = None

    def ler(self) -> np.ndarray:
        self.atual = self.arquivos[self.i % len(self.arquivos)]
        self.i += 1
        imagem = cv2.imread(str(self.atual))
        if imagem is None:
            raise ErroCamera(f"Nao foi possivel abrir {self.atual}")
        return imagem

    def fechar(self):
        pass


class CapturaEmParalelo:
    """Busca a proxima foto enquanto o YOLO analisa a atual.

    Sem isso cada ciclo soma (tempo da foto + tempo do YOLO); com isso o ciclo vira o
    maior dos dois, e a foto analisada e sempre a mais recente.
    """

    def __init__(self, fonte):
        self.fonte = fonte
        self.cond = threading.Condition()
        self.imagem: np.ndarray | None = None
        self.erro: ErroCamera | None = None
        self.seq = 0
        self.lido = 0
        self.rodando = True
        self.thread = threading.Thread(target=self._loop, name="captura", daemon=True)
        self.thread.start()

    def _loop(self):
        while self.rodando:
            try:
                imagem, erro = self.fonte.ler(), None
            except ErroCamera as e:
                imagem, erro = None, e
            with self.cond:
                self.imagem, self.erro = imagem, erro
                self.seq += 1
                self.cond.notify_all()
            if erro is not None:
                time.sleep(0.5)  # camera fora: nao martelar a rede

    def ler(self) -> np.ndarray:
        with self.cond:
            if not self.cond.wait_for(lambda: self.seq != self.lido, timeout=15):
                raise ErroCamera("Nenhuma foto nova em 15 s")
            self.lido = self.seq
            if self.erro is not None:
                raise self.erro
            return self.imagem

    def fechar(self):
        self.rodando = False
        self.thread.join(timeout=10)
        self.fonte.fechar()


class ComRotacao:
    """Gira a imagem (a ESP32-CAM so consegue espelhar/virar 180 graus no sensor)."""

    CODIGOS = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}

    def __init__(self, fonte, graus: int):
        self.fonte = fonte
        self.codigo = self.CODIGOS[graus]

    def ler(self) -> np.ndarray:
        return cv2.rotate(self.fonte.ler(), self.codigo)

    def fechar(self):
        self.fonte.fechar()


def criar_fonte(cfg: Config):
    c = cfg.camera
    if c.fonte == "esp32cam":
        video = urlsplit(c.url).path.rstrip("/").endswith("/stream")
        log.info("Fonte: ESP32-CAM (%s) em %s", "video MJPEG" if video else "foto a foto", c.url)
        classe = FonteVideoESP32CAM if video else FonteESP32CAM
        fonte = classe(c.url, c.flash, c.timeout_s, c.resolucao, c.qualidade_jpeg)
    elif c.fonte == "webcam":
        log.info("Fonte: webcam %d", c.webcam_indice)
        fonte = FonteWebcam(c.webcam_indice)
    else:
        pasta = cfg.caminho(c.pasta)
        log.info("Fonte: imagens da pasta %s", pasta)
        fonte = FontePasta(pasta)
    if c.rotacao:
        log.info("Girando a imagem %d graus", c.rotacao)
        fonte = ComRotacao(fonte, c.rotacao)
    if c.fonte != "pasta":
        fonte = CapturaEmParalelo(fonte)
    return fonte
