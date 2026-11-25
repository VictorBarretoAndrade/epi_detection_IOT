"""De onde vem a imagem: ESP32-CAM (HTTP), webcam do PC ou uma pasta de fotos."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import cv2
import numpy as np
import requests

from .config import Config

log = logging.getLogger(__name__)


class ErroCamera(Exception):
    pass


class FonteESP32CAM:
    def __init__(self, url: str, flash: bool, timeout_s: float):
        self.url = url
        self.params = {"flash": "1"} if flash else None
        self.timeout = (min(2.0, timeout_s), timeout_s)  # (conectar, ler)
        self.sessao = requests.Session()

    def ler(self) -> np.ndarray:
        try:
            r = self.sessao.get(self.url, params=self.params, timeout=self.timeout)
        except requests.RequestException as e:
            raise ErroCamera(f"ESP32-CAM inacessivel em {self.url}: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ErroCamera(f"ESP32-CAM respondeu HTTP {r.status_code}: {r.text[:80]}")
        imagem = cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
        if imagem is None:
            raise ErroCamera(f"Resposta nao e um JPEG valido ({len(r.content)} bytes)")
        return imagem

    def fechar(self):
        self.sessao.close()


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
        log.info("Fonte: ESP32-CAM em %s", c.url)
        fonte = FonteESP32CAM(c.url, c.flash, c.timeout_s)
    elif c.fonte == "webcam":
        log.info("Fonte: webcam %d", c.webcam_indice)
        fonte = FonteWebcam(c.webcam_indice)
    else:
        pasta = cfg.caminho(c.pasta)
        log.info("Fonte: imagens da pasta %s", pasta)
        fonte = FontePasta(pasta)
    if c.rotacao:
        log.info("Girando a imagem %d graus", c.rotacao)
        return ComRotacao(fonte, c.rotacao)
    return fonte
