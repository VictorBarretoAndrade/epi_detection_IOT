"""Desenha as caixas e uma faixa colorida com o resultado na imagem."""
from __future__ import annotations

import cv2
import numpy as np

from .decisao import CAPACETE, PESSOA, SEM_CAPACETE, Estado, Resultado

# Cores em BGR (OpenCV)
COR_CATEGORIA = {CAPACETE: (0, 200, 0), SEM_CAPACETE: (0, 0, 255), PESSOA: (255, 160, 0)}
FAIXA = {
    Estado.OK: ((0, 150, 0), "EPI OK"),
    Estado.SEM_EPI: ((0, 0, 210), "SEM EPI"),
    Estado.VAZIO: ((80, 80, 80), "NINGUEM"),
    Estado.ERRO: ((0, 120, 255), "ERRO"),
}


def desenhar(imagem: np.ndarray | None, resultado: Resultado, estado_led: Estado, info: str = "") -> np.ndarray:
    if imagem is None:
        imagem = np.zeros((480, 640, 3), np.uint8)
    img = imagem.copy()
    h, w = img.shape[:2]
    escala = max(0.5, w / 1000)

    destacar = {id(d) for d in resultado.pessoas_sem_capacete}
    for d in resultado.deteccoes:
        x1, y1, x2, y2 = (int(v) for v in d.caixa)
        cor = (0, 0, 255) if id(d) in destacar else COR_CATEGORIA[d.categoria]
        cv2.rectangle(img, (x1, y1), (x2, y2), cor, 3 if id(d) in destacar else 2)
        rotulo = f"{d.classe} {d.confianca:.2f}"
        (tw, th), _ = cv2.getTextSize(rotulo, cv2.FONT_HERSHEY_SIMPLEX, 0.5 * escala, 1)
        cv2.rectangle(img, (x1, max(0, y1 - th - 6)), (x1 + tw + 4, y1), cor, -1)
        cv2.putText(img, rotulo, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.5 * escala, (255, 255, 255), 1)

    cor, texto = FAIXA[estado_led]
    altura_faixa = int(48 * escala)
    cv2.rectangle(img, (0, 0), (w, altura_faixa), cor, -1)
    cv2.putText(img, texto, (10, int(altura_faixa * 0.72)), cv2.FONT_HERSHEY_SIMPLEX, 1.1 * escala,
                (255, 255, 255), 2)
    detalhe = _ascii(resultado.motivo + (f" | {info}" if info else ""))
    cv2.putText(img, detalhe, (int(200 * escala), int(altura_faixa * 0.65)), cv2.FONT_HERSHEY_SIMPLEX,
                0.5 * escala, (255, 255, 255), 1)
    return img


def _ascii(txt: str) -> str:
    # cv2.putText nao desenha acentos
    import unicodedata

    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
