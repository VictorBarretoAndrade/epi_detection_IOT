"""Regras de decisao: transforma as deteccoes do YOLO em ok / sem_epi / vazio.

Modulo puro (sem YOLO/OpenCV) para poder ser testado sem placa nem modelo.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum

CAPACETE = "capacete"
SEM_CAPACETE = "sem_capacete"
PESSOA = "pessoa"


class Estado(str, Enum):
    OK = "ok"  # EPI correta -> LED verde
    SEM_EPI = "sem_epi"  # alguem sem capacete -> LED vermelho
    VAZIO = "vazio"  # ninguem na imagem -> LEDs apagados
    ERRO = "erro"  # sem foto / falha -> vermelho piscando


@dataclass(frozen=True)
class Deteccao:
    categoria: str  # CAPACETE | SEM_CAPACETE | PESSOA
    classe: str  # nome original da classe no modelo
    confianca: float
    caixa: tuple[float, float, float, float]  # x1, y1, x2, y2 em pixels


@dataclass
class Resultado:
    estado: Estado
    motivo: str
    deteccoes: list[Deteccao] = field(default_factory=list)
    pessoas_sem_capacete: list[Deteccao] = field(default_factory=list)


def capacete_na_cabeca(capacete: Deteccao, pessoa: Deteccao, altura_cabeca: float = 0.4) -> bool:
    """True se o centro do capacete esta na parte de cima da caixa da pessoa.

    Evita contar um capacete na mao ou em cima da mesa como "usando EPI".
    """
    px1, py1, px2, py2 = pessoa.caixa
    largura, altura = px2 - px1, py2 - py1
    cx1, cy1, cx2, cy2 = capacete.caixa
    cx, cy = (cx1 + cx2) / 2, (cy1 + cy2) / 2
    dentro_x = px1 - 0.1 * largura <= cx <= px2 + 0.1 * largura
    no_topo = py1 - 0.15 * altura <= cy <= py1 + altura_cabeca * altura
    return dentro_x and no_topo


def avaliar(
    deteccoes: list[Deteccao],
    exigir_capacete_por_pessoa: bool = True,
    altura_cabeca: float = 0.4,
) -> Resultado:
    capacetes = [d for d in deteccoes if d.categoria == CAPACETE]
    sem_capacete = [d for d in deteccoes if d.categoria == SEM_CAPACETE]
    pessoas = [d for d in deteccoes if d.categoria == PESSOA]

    # 1) O modelo viu explicitamente uma cabeca sem capacete.
    if sem_capacete:
        return Resultado(
            Estado.SEM_EPI,
            f"{len(sem_capacete)} cabeca(s) sem capacete",
            deteccoes,
            sem_capacete,
        )

    # 2) Ha pessoas: cada uma precisa de um capacete na regiao da cabeca.
    if pessoas:
        if exigir_capacete_por_pessoa:
            faltando = [
                p for p in pessoas
                if not any(capacete_na_cabeca(c, p, altura_cabeca) for c in capacetes)
            ]
        else:
            faltando = [] if capacetes else list(pessoas)
        if faltando:
            return Resultado(
                Estado.SEM_EPI,
                f"{len(faltando)} de {len(pessoas)} pessoa(s) sem capacete na cabeca",
                deteccoes,
                faltando,
            )
        return Resultado(Estado.OK, f"{len(pessoas)} pessoa(s), todas com capacete", deteccoes)

    # 3) Sem pessoa detectada, mas capacete visivel (ex.: rosto em close).
    if capacetes:
        return Resultado(Estado.OK, f"{len(capacetes)} capacete(s) detectado(s)", deteccoes)

    return Resultado(Estado.VAZIO, "ninguem detectado", deteccoes)


class Estabilizador:
    """So troca o estado depois de N resultados iguais seguidos (evita LED piscando).

    Ir para VAZIO exige mais confirmacoes: se a pessoa sai do quadro por um instante
    (ou o YOLO perde a cabeca num quadro), o LED nao apaga e acende de novo.
    """

    def __init__(self, confirmacoes: int = 2, confirmacoes_vazio: int | None = None):
        self.n = max(1, int(confirmacoes))
        self.n_vazio = max(self.n, int(confirmacoes_vazio or self.n))
        self.historico: deque[Estado] = deque(maxlen=max(self.n, self.n_vazio))
        self.atual = Estado.VAZIO

    def atualizar(self, estado: Estado) -> Estado:
        self.historico.append(estado)
        n = self.n_vazio if estado == Estado.VAZIO else self.n
        ultimos = list(self.historico)[-n:]
        if len(ultimos) == n and all(e == estado for e in ultimos):
            self.atual = estado
        return self.atual
