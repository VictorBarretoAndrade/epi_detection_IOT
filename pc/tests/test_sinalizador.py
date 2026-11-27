"""Testa o envio em segundo plano: muda na hora, repete devagar (sem rede, sem placa)."""
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from epi.decisao import Estado  # noqa: E402
from epi.sinalizador import EnvioEmSegundoPlano  # noqa: E402


class Falso:
    def __init__(self):
        self.enviados = []

    def enviar(self, estado):
        self.enviados.append((time.monotonic(), estado))
        return True

    def fechar(self):
        pass


class TestEnvio(unittest.TestCase):
    def test_limita_repeticoes_e_envia_mudanca_na_hora(self):
        falso = Falso()
        envio = EnvioEmSegundoPlano(falso, batimento_s=0.5)
        inicio = time.monotonic()
        for _ in range(60):  # 1,2 s chamando 50x por segundo com o mesmo estado
            envio.enviar(Estado.SEM_EPI)
            time.sleep(0.02)
        envio.enviar(Estado.OK)
        time.sleep(0.3)
        envio.fechar()

        sem_epi = [t for t, e in falso.enviados if e == Estado.SEM_EPI]
        ok = [t for t, e in falso.enviados if e == Estado.OK]
        self.assertLessEqual(len(sem_epi), 4)  # ~1 a cada 0,5 s, nao 60
        self.assertEqual(len(ok), 1)
        self.assertLess(ok[0] - inicio, 1.2 + 0.3)  # mudanca enviada logo


if __name__ == "__main__":
    unittest.main()
