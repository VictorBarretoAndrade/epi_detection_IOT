"""Treina (fine-tuning) um YOLO para detectar capacete e cabeca sem capacete.

Dataset padrao: Construction-PPE da Ultralytics (CC BY 4.0, ~1000 fotos de obra),
baixado automaticamente na primeira execucao. Classes usadas: helmet, no_helmet, Person.

    python treinar_modelo.py                     # padrao (50 epocas)
    python treinar_modelo.py --epocas 15 --imgsz 480   # mais rapido em CPU fraca
    python treinar_modelo.py --dados meu_dataset/data.yaml   # seu proprio dataset (ex.: Roboflow)

Em CPU isso demora (horas). Com GPU (ex.: Google Colab gratuito) leva ~10 min; veja o README.
No final o melhor peso e copiado para pc/modelos/epi.pt.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PASTA = Path(__file__).resolve().parent
DATASET_PADRAO = "construction-ppe.yaml"
CLASSES_CAPACETE = ("helmet", "person", "no_helmet")


def preparar_dataset_padrao() -> tuple[str, list[int]]:
    """Baixa o Construction-PPE para pc/datasets/ (e nao na pasta global da Ultralytics).

    Devolve o caminho do data.yaml local e os IDs das classes de capacete/pessoa.
    """
    import ultralytics
    from ultralytics.utils import YAML
    from ultralytics.utils.downloads import safe_download

    original = Path(ultralytics.__file__).parent / "cfg" / "datasets" / DATASET_PADRAO
    dados = YAML.load(original)
    raiz = PASTA / "datasets"
    pasta = raiz / "construction-ppe"
    if not (pasta / dados["val"]).exists():
        print(f"Baixando dataset Construction-PPE para {raiz} ...")
        safe_download(dados["download"], dir=raiz, unzip=True, delete=True)
    dados["path"] = str(pasta)
    dados.pop("download", None)
    dados.pop("yaml_file", None)
    local = raiz / DATASET_PADRAO
    YAML.save(local, dados)

    ids = [i for i, nome in dados["names"].items() if nome.lower() in CLASSES_CAPACETE]
    return str(local), ids


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dados", default=DATASET_PADRAO, help="data.yaml do dataset")
    p.add_argument("--base", default=str(PASTA / "modelos" / "yolo11n.pt"),
                   help="modelo pre-treinado de partida (yolo11n = mais leve; baixado se faltar)")
    p.add_argument("--epocas", type=int, default=50)
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--lote", type=int, default=-1, help="batch; -1 = automatico (GPU) / 8 (CPU)")
    p.add_argument("--dispositivo", default=None, help="'cpu', '0' (GPU)... padrao: automatico")
    p.add_argument("--fracao", type=float, default=1.0, help="fracao do dataset (ex.: 0.1 para teste rapido)")
    p.add_argument("--todas-classes", action="store_true",
                   help="treina todas as classes do dataset (luvas, colete...) e nao so as de capacete")
    p.add_argument("--saida", default=str(PASTA / "modelos" / "epi.pt"))
    a = p.parse_args()

    import torch
    from ultralytics import YOLO

    dispositivo = a.dispositivo or ("0" if torch.cuda.is_available() else "cpu")
    lote = a.lote if a.lote != -1 or dispositivo != "cpu" else 8
    extras = {}
    dados = a.dados
    if a.dados == DATASET_PADRAO:
        dados, ids = preparar_dataset_padrao()
        if not a.todas_classes:
            extras["classes"] = ids  # treina so helmet / Person / no_helmet

    print(f"Treinando {a.base} em {dados} | epocas={a.epocas} imgsz={a.imgsz} "
          f"lote={lote} dispositivo={dispositivo}")
    if dispositivo == "cpu":
        print("AVISO: sem GPU o treino e lento. Para testar, use --epocas 1 --fracao 0.1")

    modelo = YOLO(a.base)
    resultado = modelo.train(
        data=dados,
        epochs=a.epocas,
        imgsz=a.imgsz,
        batch=lote,
        device=dispositivo,
        fraction=a.fracao,
        project=str(PASTA / "runs"),
        name="epi",
        exist_ok=True,
        patience=15,
        workers=2 if dispositivo == "cpu" else 8,
        **extras,
    )

    melhor = Path(resultado.save_dir) / "weights" / "best.pt"
    if not melhor.exists():
        print(f"ERRO: nao encontrei {melhor}")
        return 1
    destino = Path(a.saida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(melhor, destino)
    print(f"\nModelo salvo em {destino}")
    print("Classes:", YOLO(str(destino)).names)
    print("Agora rode:  python main.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
