"""Leitura do config.toml (biblioteca padrao tomllib, Python 3.11+)."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class ConfigCamera:
    fonte: str = "esp32cam"  # esp32cam | webcam | pasta
    url: str = "http://192.168.0.50/capture"
    resolucao: str = ""  # qqvga | qvga | cif | hvga | vga ("" = a do firmware)
    qualidade_jpeg: int = 0  # 4 (melhor) a 63 (pior); 0 = a do firmware
    flash: bool = False
    timeout_s: float = 3.0
    rotacao: int = 0  # 0, 90, 180 ou 270 graus (sentido horario) se a camera estiver de lado
    webcam_indice: int = 0
    pasta: str = "imagens_teste"


@dataclass
class ConfigModelo:
    arquivo: str = "modelos/epi.pt"
    confianca: float = 0.45
    imgsz: int = 640
    dispositivo: str = "cpu"


@dataclass
class ConfigClasses:
    # Nomes das classes do modelo (comparacao sem diferenciar maiusculas).
    capacete: list[str] = field(default_factory=lambda: ["helmet", "hardhat", "hard_hat", "hard-hat"])
    sem_capacete: list[str] = field(default_factory=lambda: ["no_helmet", "no-hardhat", "no-helmet", "head"])
    pessoa: list[str] = field(default_factory=lambda: ["person"])


@dataclass
class ConfigRegras:
    confirmacoes: int = 2
    confirmacoes_vazio: int = 4
    exigir_capacete_por_pessoa: bool = True
    altura_cabeca: float = 0.4  # fracao superior da caixa da pessoa onde o capacete deve estar


@dataclass
class ConfigLed:
    modo: str = "http"  # http | serial | nenhum
    url: str = "http://192.168.0.51"
    porta_serial: str = "COM5"
    baud: int = 115200
    timeout_s: float = 1.0


@dataclass
class ConfigApp:
    intervalo_s: float = 0.5
    mostrar_janela: bool = True
    salvar_infracoes: bool = True
    pasta_infracoes: str = "infracoes"


@dataclass
class Config:
    camera: ConfigCamera = field(default_factory=ConfigCamera)
    modelo: ConfigModelo = field(default_factory=ConfigModelo)
    classes: ConfigClasses = field(default_factory=ConfigClasses)
    regras: ConfigRegras = field(default_factory=ConfigRegras)
    led: ConfigLed = field(default_factory=ConfigLed)
    app: ConfigApp = field(default_factory=ConfigApp)
    pasta_base: Path = Path(".")

    def caminho(self, relativo: str) -> Path:
        """Resolve caminhos relativos a partir da pasta do config.toml."""
        p = Path(relativo)
        return p if p.is_absolute() else (self.pasta_base / p)


def _preencher(classe, dados: dict, secao: str):
    validos = {f.name for f in fields(classe)}
    desconhecidos = set(dados) - validos
    if desconhecidos:
        raise ValueError(f"[{secao}] chave(s) desconhecida(s): {', '.join(sorted(desconhecidos))}")
    return classe(**dados)


def carregar(caminho: str | Path) -> Config:
    caminho = Path(caminho).resolve()
    if not caminho.exists():
        raise FileNotFoundError(f"Config nao encontrado: {caminho}")
    with open(caminho, "rb") as f:
        dados = tomllib.load(f)

    secoes = {
        "camera": ConfigCamera,
        "modelo": ConfigModelo,
        "classes": ConfigClasses,
        "regras": ConfigRegras,
        "led": ConfigLed,
        "app": ConfigApp,
    }
    extras = set(dados) - set(secoes)
    if extras:
        raise ValueError(f"Secao(oes) desconhecida(s) no config: {', '.join(sorted(extras))}")

    partes = {nome: _preencher(cls, dados.get(nome, {}), nome) for nome, cls in secoes.items()}
    cfg = Config(**partes, pasta_base=caminho.parent)

    if cfg.camera.fonte not in ("esp32cam", "webcam", "pasta"):
        raise ValueError("camera.fonte deve ser esp32cam, webcam ou pasta")
    if cfg.camera.rotacao not in (0, 90, 180, 270):
        raise ValueError("camera.rotacao deve ser 0, 90, 180 ou 270")
    if cfg.led.modo not in ("http", "serial", "nenhum"):
        raise ValueError("led.modo deve ser http, serial ou nenhum")
    return cfg
