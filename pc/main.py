"""Detector de EPI: busca foto na ESP32-CAM, roda o YOLO e manda verde/vermelho ao ESP32 dos LEDs.

Uso (dentro da pasta pc/, com o .venv ativo):
    python main.py                          # usa config.toml
    python main.py --fonte webcam           # testa com a webcam do notebook
    python main.py --imagem foto.jpg        # analisa uma foto e mostra o resultado
    python main.py --sem-janela             # sem janela (so terminal)

Teclas na janela:  q = sair   s = salvar a imagem atual
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

import cv2

from epi.config import Config, carregar
from epi.decisao import Estabilizador, Estado, Resultado, avaliar
from epi.detector import DetectorEPI
from epi.fontes import ErroCamera, criar_fonte
from epi.sinalizador import criar_sinalizador
from epi.visual import desenhar

PASTA = Path(__file__).resolve().parent
JANELA = "Detector de EPI (q = sair, s = salvar)"
log = logging.getLogger("epi")


def argumentos():
    p = argparse.ArgumentParser(description="Detector de EPI com ESP32-CAM + YOLO + ESP32 LEDs")
    p.add_argument("--config", default=str(PASTA / "config.toml"))
    p.add_argument("--fonte", choices=["esp32cam", "webcam", "pasta"], help="sobrescreve camera.fonte")
    p.add_argument("--modelo", help="sobrescreve modelo.arquivo (ex.: modelos/epi.pt)")
    p.add_argument("--led", choices=["http", "serial", "nenhum"], help="sobrescreve led.modo")
    p.add_argument("--imagem", help="analisa uma unica imagem e sai")
    p.add_argument("--sem-janela", action="store_true", help="nao abre janela")
    return p.parse_args()


def salvar(imagem, pasta: Path, prefixo: str) -> Path:
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = pasta / f"{prefixo}_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    cv2.imwrite(str(arquivo), imagem)
    return arquivo


def analisar_imagem(cfg: Config, detector: DetectorEPI, caminho: str, janela: bool) -> int:
    imagem = cv2.imread(caminho)
    if imagem is None:
        log.error("Nao foi possivel abrir %s", caminho)
        return 1
    t = time.perf_counter()
    res = avaliar(detector.detectar(imagem), cfg.regras.exigir_capacete_por_pessoa, cfg.regras.altura_cabeca)
    ms = (time.perf_counter() - t) * 1000
    print(f"Resultado: {res.estado.value.upper()}  ({res.motivo})  [{ms:.0f} ms]")
    for d in res.deteccoes:
        print(f"  - {d.categoria:13s} {d.classe:12s} conf={d.confianca:.2f} caixa={[round(v) for v in d.caixa]}")
    saida = desenhar(imagem, res, res.estado, f"{ms:.0f} ms")
    arquivo = Path(caminho).with_name(Path(caminho).stem + "_resultado.jpg")
    cv2.imwrite(str(arquivo), saida)
    print(f"Imagem anotada salva em {arquivo}")
    if janela:
        cv2.imshow(JANELA, saida)
        cv2.waitKey(0)
        cv2.destroyAllWindows()
    return 0


def loop(cfg: Config, detector: DetectorEPI, janela: bool) -> int:
    fonte = criar_fonte(cfg)
    sinal = criar_sinalizador(cfg.led)
    estab = Estabilizador(cfg.regras.confirmacoes, cfg.regras.confirmacoes_vazio)
    pasta_infracoes = cfg.caminho(cfg.app.pasta_infracoes)
    estado_anterior: Estado | None = None
    falhas_seguidas = 0
    log.info("Rodando. Ctrl+C para parar%s.", " (ou 'q' na janela)" if janela else "")

    try:
        while True:
            inicio = time.perf_counter()
            imagem = None
            try:
                imagem = fonte.ler()
                t_yolo = time.perf_counter()
                deteccoes = detector.detectar(imagem)
                ms_yolo = (time.perf_counter() - t_yolo) * 1000
                res = avaliar(deteccoes, cfg.regras.exigir_capacete_por_pessoa, cfg.regras.altura_cabeca)
                falhas_seguidas = 0
            except ErroCamera as e:
                falhas_seguidas += 1
                ms_yolo = 0.0
                res = Resultado(Estado.ERRO, str(e))
                if falhas_seguidas in (1, 10) or falhas_seguidas % 50 == 0:
                    log.warning("Camera: %s (falhas seguidas: %d)", e, falhas_seguidas)

            estado = estab.atualizar(res.estado)
            sinal.enviar(estado)  # envia todo ciclo: serve de "batimento" para o ESP32

            if estado != estado_anterior:
                motivo = res.motivo if estado == res.estado else "aguardando confirmacao"
                log.info("Estado -> %s  (%s)", estado.value.upper(), motivo)
                if estado == Estado.SEM_EPI and cfg.app.salvar_infracoes and imagem is not None:
                    arq = salvar(desenhar(imagem, res, estado), pasta_infracoes, "sem_epi")
                    log.info("Foto da infracao salva: %s", arq.name)
                estado_anterior = estado

            total_ms = (time.perf_counter() - inicio) * 1000
            if janela:
                quadro = desenhar(imagem, res, estado, f"YOLO {ms_yolo:.0f} ms | ciclo {total_ms:.0f} ms")
                cv2.imshow(JANELA, quadro)
                tecla = cv2.waitKey(1) & 0xFF
                if tecla == ord("q") or cv2.getWindowProperty(JANELA, cv2.WND_PROP_VISIBLE) < 1:
                    break
                if tecla == ord("s"):
                    log.info("Imagem salva: %s", salvar(quadro, cfg.caminho("capturas"), "captura"))

            espera = cfg.app.intervalo_s - (time.perf_counter() - inicio)
            if espera > 0:
                time.sleep(espera)
    except KeyboardInterrupt:
        pass
    finally:
        log.info("Encerrando.")
        fonte.fechar()
        sinal.fechar()
        if janela:
            cv2.destroyAllWindows()
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    args = argumentos()
    try:
        cfg = carregar(args.config)
    except (FileNotFoundError, ValueError) as e:
        log.error("%s", e)
        return 1
    if args.fonte:
        cfg.camera.fonte = args.fonte
    if args.modelo:
        cfg.modelo.arquivo = args.modelo
    if args.led:
        cfg.led.modo = args.led
    janela = cfg.app.mostrar_janela and not args.sem_janela

    try:
        log.info("Carregando modelo %s ...", cfg.modelo.arquivo)
        detector = DetectorEPI(cfg.modelo, cfg.classes, cfg.caminho(cfg.modelo.arquivo))
        if args.imagem:
            return analisar_imagem(cfg, detector, args.imagem, janela)
        return loop(cfg, detector, janela)
    except (FileNotFoundError, ErroCamera, RuntimeError) as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
