"""Testa a separacao dos quadros do video MJPEG (sem rede, sem placa)."""
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from epi.fontes import FonteVideoESP32CAM  # noqa: E402

LIMITE = b"epi7b3c9f1e2d"


def montar(quadros):
    corpo = b""
    for q in quadros:
        corpo += b"\r\n--" + LIMITE + b"\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(q) + q
    return corpo


class RespostaFalsa:
    def __init__(self, corpo, rng):
        self.corpo, self.rng = corpo, rng

    def iter_content(self, chunk_size=None):
        i = 0
        while i < len(self.corpo):
            n = self.rng.randint(1, 50)  # pedacos cortados em qualquer lugar
            yield self.corpo[i:i + n]
            i += n


class TestVideo(unittest.TestCase):
    def test_separa_quadros_mesmo_cortados(self):
        rng = random.Random(1)
        quadros = [bytes(rng.randrange(256) for _ in range(rng.randint(10, 300))) for _ in range(20)]
        quadros.append(b"\r\n\r\n tem CRLF CRLF dentro do JPEG \r\n\r\n")  # binario pode conter isso
        fonte = FonteVideoESP32CAM.__new__(FonteVideoESP32CAM)
        lidos = list(fonte._separar_quadros(RespostaFalsa(montar(quadros), rng)))
        self.assertEqual(lidos, quadros)


if __name__ == "__main__":
    unittest.main()
