"""Wrapper do YOLO (Ultralytics) que devolve deteccoes ja categorizadas."""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from .config import ConfigClasses, ConfigModelo
from .decisao import CAPACETE, PESSOA, SEM_CAPACETE, Deteccao

log = logging.getLogger(__name__)

MODELO_AUSENTE = """
Modelo nao encontrado: {caminho}

Voce precisa de pesos YOLO treinados para capacete. Opcoes:
  1) Treinar (recomendado):  python treinar_modelo.py
     (no Google Colab com GPU leva ~10 min; veja o README)
  2) Copiar um .pt que voce ja tenha para pc/modelos/epi.pt
     e ajustar a secao [classes] do config.toml com os nomes das classes dele.
"""


class DetectorEPI:
    def __init__(self, cfg_modelo: ConfigModelo, cfg_classes: ConfigClasses, caminho_modelo: Path):
        from ultralytics import YOLO  # import pesado: so quando for usar

        # Modelos oficiais (yolo11n.pt, yolov8n.pt...) sao baixados pela Ultralytics se faltarem.
        # Eles NAO conhecem capacete: servem so para testar o fluxo.
        oficial = caminho_modelo.name.lower().startswith("yolo")
        if not caminho_modelo.exists() and not oficial:
            raise FileNotFoundError(MODELO_AUSENTE.format(caminho=caminho_modelo))

        self.cfg = cfg_modelo
        self.modelo = YOLO(str(caminho_modelo))
        self.nomes: dict[int, str] = dict(self.modelo.names)
        self.mapa = self._mapear_classes(cfg_classes)
        # A 1a inferencia e bem mais lenta (inicializacao); faz agora para nao atrasar o 1o resultado.
        self.modelo.predict(np.zeros((cfg_modelo.imgsz, cfg_modelo.imgsz, 3), np.uint8),
                            imgsz=cfg_modelo.imgsz, device=cfg_modelo.dispositivo, verbose=False)

    def _lista_classes(self) -> str:
        nomes = list(self.nomes.values())
        return str(nomes) if len(nomes) <= 15 else f"{nomes[:10]} ... ({len(nomes)} classes)"

    def _mapear_classes(self, cfg: ConfigClasses) -> dict[int, str]:
        listas = {
            CAPACETE: {c.lower() for c in cfg.capacete},
            SEM_CAPACETE: {c.lower() for c in cfg.sem_capacete},
            PESSOA: {c.lower() for c in cfg.pessoa},
        }
        mapa: dict[int, str] = {}
        for idx, nome in self.nomes.items():
            for categoria, nomes in listas.items():
                if nome.lower() in nomes:
                    mapa[idx] = categoria
                    break

        usados = {cat: [self.nomes[i] for i, c in mapa.items() if c == cat] for cat in listas}
        log.info("Classes do modelo: %s", self._lista_classes())
        log.info("Mapeamento -> capacete=%s | sem_capacete=%s | pessoa=%s",
                 usados[CAPACETE], usados[SEM_CAPACETE], usados[PESSOA])
        if not usados[CAPACETE]:
            log.warning(
                "O modelo NAO tem classe de capacete. Ele nao vai reconhecer EPI; "
                "treine um modelo (treinar_modelo.py) ou ajuste [classes] no config.toml."
            )
        if not usados[SEM_CAPACETE] and not usados[PESSOA]:
            log.warning("Sem classe 'sem capacete' nem 'pessoa': o sistema nunca vai dar vermelho.")
        return mapa

    def detectar(self, imagem_bgr: np.ndarray) -> list[Deteccao]:
        r = self.modelo.predict(
            imagem_bgr,
            conf=self.cfg.confianca,
            imgsz=self.cfg.imgsz,
            device=self.cfg.dispositivo,
            verbose=False,
        )[0]
        saida: list[Deteccao] = []
        if r.boxes is None:
            return saida
        for cls, conf, xyxy in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(), r.boxes.xyxy.tolist()):
            categoria = self.mapa.get(int(cls))
            if categoria is None:
                continue  # luvas, colete, etc.: ignorados nesta versao
            saida.append(Deteccao(categoria, self.nomes[int(cls)], float(conf), tuple(xyxy)))
        return saida
