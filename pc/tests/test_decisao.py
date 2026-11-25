"""Testes das regras de decisao (sem YOLO, sem placa).  python -m unittest discover -s tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from epi.decisao import (  # noqa: E402
    CAPACETE,
    PESSOA,
    SEM_CAPACETE,
    Deteccao,
    Estabilizador,
    Estado,
    avaliar,
)


def pessoa(x1=100, y1=100, x2=300, y2=600):
    return Deteccao(PESSOA, "person", 0.9, (x1, y1, x2, y2))


def capacete(cx, cy, r=30):
    return Deteccao(CAPACETE, "helmet", 0.8, (cx - r, cy - r, cx + r, cy + r))


class TestAvaliar(unittest.TestCase):
    def test_ninguem(self):
        self.assertEqual(avaliar([]).estado, Estado.VAZIO)

    def test_pessoa_com_capacete_na_cabeca(self):
        self.assertEqual(avaliar([pessoa(), capacete(200, 130)]).estado, Estado.OK)

    def test_pessoa_sem_capacete(self):
        self.assertEqual(avaliar([pessoa()]).estado, Estado.SEM_EPI)

    def test_capacete_na_mao_nao_conta(self):
        # capacete na altura da cintura da pessoa
        r = avaliar([pessoa(), capacete(280, 400)])
        self.assertEqual(r.estado, Estado.SEM_EPI)
        self.assertEqual(len(r.pessoas_sem_capacete), 1)

    def test_capacete_na_mao_conta_se_regra_desligada(self):
        r = avaliar([pessoa(), capacete(280, 400)], exigir_capacete_por_pessoa=False)
        self.assertEqual(r.estado, Estado.OK)

    def test_cabeca_sem_capacete_vence(self):
        cabeca = Deteccao(SEM_CAPACETE, "no_helmet", 0.7, (500, 100, 560, 160))
        r = avaliar([pessoa(), capacete(200, 130), cabeca])
        self.assertEqual(r.estado, Estado.SEM_EPI)

    def test_duas_pessoas_uma_sem(self):
        r = avaliar([pessoa(), capacete(200, 130), pessoa(400, 100, 600, 600)])
        self.assertEqual(r.estado, Estado.SEM_EPI)
        self.assertEqual(len(r.pessoas_sem_capacete), 1)

    def test_close_so_capacete(self):
        # rosto em close: modelo pode nao achar "pessoa", so o capacete
        self.assertEqual(avaliar([capacete(320, 100, 80)]).estado, Estado.OK)

    def test_capacete_um_pouco_acima_da_caixa(self):
        # capacete pode sair um pouco acima da caixa da pessoa
        self.assertEqual(avaliar([pessoa(), capacete(200, 90)]).estado, Estado.OK)


class TestEstabilizador(unittest.TestCase):
    def test_precisa_de_confirmacao(self):
        e = Estabilizador(2)
        self.assertEqual(e.atualizar(Estado.OK), Estado.VAZIO)
        self.assertEqual(e.atualizar(Estado.OK), Estado.OK)
        self.assertEqual(e.atualizar(Estado.SEM_EPI), Estado.OK)  # 1 so nao troca
        self.assertEqual(e.atualizar(Estado.OK), Estado.OK)
        self.assertEqual(e.atualizar(Estado.SEM_EPI), Estado.OK)
        self.assertEqual(e.atualizar(Estado.SEM_EPI), Estado.SEM_EPI)

    def test_vazio_exige_mais_confirmacoes(self):
        e = Estabilizador(2, 4)
        e.atualizar(Estado.SEM_EPI)
        self.assertEqual(e.atualizar(Estado.SEM_EPI), Estado.SEM_EPI)
        for _ in range(3):
            self.assertEqual(e.atualizar(Estado.VAZIO), Estado.SEM_EPI)  # perdeu a cabeca 3x
        self.assertEqual(e.atualizar(Estado.VAZIO), Estado.VAZIO)  # 4a vez: apaga
        e.atualizar(Estado.OK)
        self.assertEqual(e.atualizar(Estado.OK), Estado.OK)  # outros estados seguem com 2

    def test_confirmacao_1_e_imediato(self):
        e = Estabilizador(1)
        self.assertEqual(e.atualizar(Estado.ERRO), Estado.ERRO)


if __name__ == "__main__":
    unittest.main()
