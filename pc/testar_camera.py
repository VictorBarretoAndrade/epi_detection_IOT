"""Testa so a ESP32-CAM: baixa algumas fotos e salva a ultima (nao precisa de YOLO).

    python testar_camera.py                      # usa camera.url do config.toml
    python testar_camera.py --url http://192.168.0.50/capture --n 5
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import requests

PASTA = Path(__file__).resolve().parent
sys.path.insert(0, str(PASTA))
from epi.config import carregar  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url")
    p.add_argument("--n", type=int, default=3, help="quantas fotos baixar")
    p.add_argument("--flash", action="store_true")
    p.add_argument("--config", default=str(PASTA / "config.toml"))
    a = p.parse_args()

    url = a.url or carregar(a.config).camera.url
    print(f"Buscando {a.n} foto(s) em {url}")
    conteudo = b""
    for i in range(a.n):
        t = time.perf_counter()
        try:
            r = requests.get(url, params={"flash": "1"} if a.flash else None, timeout=(3, 8))
        except requests.RequestException as e:
            print(f"  ERRO: {type(e).__name__}: {e}")
            print("  -> Confira o IP no Monitor Serial e se PC e ESP32-CAM estao na mesma rede.")
            return 1
        ms = (time.perf_counter() - t) * 1000
        tipo = r.headers.get("Content-Type", "?")
        print(f"  foto {i + 1}: HTTP {r.status_code}, {tipo}, {len(r.content) / 1024:.1f} KB, {ms:.0f} ms")
        if r.status_code != 200 or not r.content.startswith(b"\xff\xd8"):
            print("  ERRO: a resposta nao e um JPEG.")
            return 1
        conteudo = r.content

    destino = PASTA / "teste_camera.jpg"
    destino.write_bytes(conteudo)
    print(f"OK! Ultima foto salva em {destino}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
