# Detector de EPI (capacete) com ESP32-CAM + YOLO + ESP32

```
 ┌────────────┐  video MJPEG :81/stream ┌──────────────────────┐  GET /estado?valor=ok  ┌──────────────┐
 │ ESP32-CAM  │ ──────────────────────▶ │ PC (Python + YOLO)   │ ─────────────────────▶ │ ESP32 + LEDs │
 │ filma      │  (ou foto a foto        │ detecta capacete e   │  (Wi-Fi ou cabo USB)   │ + servo      │
 └────────────┘   em /capture)          │ decide ok / sem_epi  │                        └──────────────┘
                                        └──────────────────────┘
```

1. A ESP32-CAM manda vídeo contínuo (MJPEG, ~5 quadros/s em HVGA) para o PC.
2. O YOLO procura **capacete**, **cabeça sem capacete** e **pessoa**.
3. O PC decide e manda o estado ao ESP32 dos LEDs:

| Estado    | LED                       | Quando                                                |
|-----------|---------------------------|-------------------------------------------------------|
| `ok`      | **verde** (servo abre)    | todas as pessoas estão com capacete na cabeça          |
| `sem_epi` | **vermelho**              | alguém sem capacete (ou com capacete na mão)          |
| `vazio`   | apagados                  | ninguém na frente da câmera                           |
| `erro`    | vermelho piscando rápido  | o PC não conseguiu foto da câmera                     |
| sem sinal | vermelho piscando devagar | o PC parou de mandar mensagens (desligado, sem rede…) |

O servo (opcional, ex.: catraca/cancela) só abre em `ok`; em qualquer outro estado fica fechado.

O YOLO roda no PC porque a ESP32-CAM não tem memória nem CPU para isso.

## Estrutura

```
firmware/
  esp32cam/     PlatformIO: ESP32-CAM AI Thinker -> video :81/stream, foto /capture, ajuste /config
  esp32_led/    PlatformIO: ESP32 comum (DevKit, S3 ou C3) -> LEDs verde/vermelho + servo
  servo_teste/  PlatformIO: so o servo indo e voltando (testa fiacao/fonte sem o resto)
  ler_serial.py mostra o log da placa (reinicia ela; --sem-reset para so ler)
pc/
  main.py            programa principal (câmera -> YOLO -> LEDs)
  config.toml        IPs, modelo, classes, regras
  treinar_modelo.py  treina o YOLO de capacete (gera modelos/epi.pt)
  testar_camera.py   testa só a ESP32-CAM
  testar_led.py      testa só o ESP32 dos LEDs
  epi/               código (decisão, detector, câmera, envio)
  tests/             testes das regras (python -m unittest discover -s tests)
```

## Material

- 1× **ESP32-CAM AI Thinker** (OV2640) + base USB **ESP32-CAM-MB** (ou adaptador FTDI) + fonte 5 V boa.
- 1× **ESP32** qualquer (DevKit V1 / WROOM, ESP32-S3 DevKitC ou ESP32-C3).
- 1× LED verde, 1× LED vermelho, 2× resistores de 220 Ω (ou use o LED RGB da placa S3).
- Opcional: 1× servo (SG90/MG90S) + **fonte 5 V separada** (≥ 1 A) e um capacitor de 470–1000 µF.
- PC com Python 3.11+ (testado com 3.12). Os três precisam estar na **mesma rede Wi-Fi 2,4 GHz**.

Ligação (padrão; os pinos podem ser mudados no `build_flags` do `platformio.ini`):

| Placa       | LED verde | LED vermelho | Servo  | Extra                     |
|-------------|-----------|--------------|--------|---------------------------|
| `esp32dev`  | GPIO26    | GPIO27       | —      |                           |
| `esp32s3`   | GPIO4     | GPIO5        | —      | LED RGB da placa (GPIO48) |
| `esp32s3_octal` | GPIO5 | GPIO4        | GPIO6  | S3 N16R8/N32R8 (memória octal), USB nativa |
| `esp32c3`   | GPIO3     | GPIO4        | —      |                           |

No `esp32s3_octal` o verde/vermelho estão no 5/4 porque assim ficaram os fios da montagem
original; se os seus LEDs acenderem trocados (verde com `sem_epi`), troque os dois números.
Para usar servo em outra placa, acrescente `-DPINO_SERVO=<pino>` no ambiente dela.

```
GPIO5  ──[220 Ω]──▶|── GND   (LED verde: perna longa no resistor, curta no GND)
GPIO4  ──[220 Ω]──▶|── GND   (LED vermelho)

Servo:  sinal (laranja/amarelo) ── GPIO6
        +5 V  (vermelho)        ── + da fonte externa   (NÃO no 3V3/5V do ESP)
        GND   (marrom/preto)    ── − da fonte externa E um GND do ESP (GND em comum!)
```

> **ESP32-S3 com memória octal (N16R8/N32R8): não ligue nada nos GPIO 33 a 37.** Eles são
> usados pela flash/PSRAM; um LED ou servo ali trava ou reinicia a placa.

O servo fica em `SERVO_FECHADO` (10°) e vai até `SERVO_ABERTO` (100°) com `ok`, andando aos
poucos (2° a cada 15 ms): o salto direto puxa pico de corrente e o servo travava. Os ângulos
evitam os batentes 0°/180°, onde o servo força e esquenta.

---

## Passo 1 — Python no PC

Na pasta do projeto, no PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r pc\requirements.txt
```

(Isso instala Ultralytics/YOLO, PyTorch CPU, OpenCV e o PlatformIO de linha de comando.)
Se quiser ativar o ambiente: `.\.venv\Scripts\Activate.ps1`. Os comandos abaixo assumem que
você está **dentro da pasta `pc`** com o ambiente ativo.

## Passo 2 — Modelo YOLO de capacete

O YOLO "de fábrica" (`yolo11n.pt`) **não conhece capacete**.

**Já pronto (padrão do `config.toml`):** `pc/modelos/capacete_yolov8n.pt` é o modelo pré-treinado
[keremberke/yolov8n-hard-hat-detection](https://huggingface.co/keremberke/yolov8n-hard-hat-detection)
(YOLOv8n, classes `Hardhat` / `NO-Hardhat`, mAP50 ≈ 0,84, ~150 ms por foto em CPU).
Se o arquivo não estiver lá (ele fica fora do Git), baixe:

```powershell
Invoke-WebRequest https://huggingface.co/keremberke/yolov8n-hard-hat-detection/resolve/main/best.pt -OutFile modelos\capacete_yolov8n.pt
```

**Treinar o seu (opcional, para melhorar):** o script usa o dataset **Construction-PPE** da
Ultralytics (CC BY 4.0, ~1000 fotos de obra, baixado sozinho) e treina `helmet`, `no_helmet` e
`Person`. Depois troque `modelo.arquivo` para `modelos/epi.pt`.

**Opção A — Google Colab (recomendado, GPU grátis, ~10–15 min):**

1. Abra <https://colab.research.google.com>, *Ambiente de execução → Alterar tipo → GPU T4*.
2. Rode numa célula:
   ```python
   !pip install -q ultralytics
   from ultralytics import YOLO
   YOLO("yolo11n.pt").train(data="construction-ppe.yaml", epochs=60, imgsz=640, classes=[0, 5, 6])
   ```
3. Baixe `runs/detect/train/weights/best.pt` (painel de arquivos à esquerda) e salve como
   `pc/modelos/epi.pt`.

**Opção B — no próprio PC (sem GPU demora bastante):**

```powershell
python treinar_modelo.py                         # 50 épocas, imgsz 640
python treinar_modelo.py --epocas 15 --imgsz 480 # mais rápido, um pouco menos preciso
```

Referência medida num i3-10110U sem GPU: ~10 min por época a 640 px (50 épocas ≈ 8 h);
a 480 px fica em torno de metade disso. O dataset (~500 MB) é baixado para `pc/datasets/`.

O resultado vai para `pc/modelos/epi.pt`. Para um teste rápido do script: `--epocas 1 --fracao 0.1`.

**Já tem um `.pt` de capacete?** Copie para `pc/modelos/epi.pt` e ajuste os nomes das classes
na seção `[classes]` do `config.toml` (o programa mostra as classes do modelo ao iniciar).
Também dá para treinar com um dataset seu (ex.: exportado do Roboflow em formato YOLOv8):
`python treinar_modelo.py --dados caminho\data.yaml`.

## Passo 3 — Gravar a ESP32-CAM

1. Edite `firmware/esp32cam/include/credenciais.h` com o nome/senha do Wi-Fi (2,4 GHz).
   (Se o arquivo não existir, copie de `credenciais.example.h`. Ele fica fora do Git.)
2. Grave com PlatformIO — escolha um jeito:
   - **VS Code**: instale a extensão *PlatformIO IDE*, *File → Open Folder* em `firmware/esp32cam`,
     clique em **→ Upload** e depois em **🔌 Serial Monitor**.
   - **Terminal**:
     ```powershell
     cd ..\firmware\esp32cam
     ..\..\.venv\Scripts\pio run -t upload
     ..\..\.venv\Scripts\pio device monitor
     ```
   - Com **base ESP32-CAM-MB** é só conectar. Com **FTDI**: IO0 no GND + RST antes do upload;
     depois solte IO0 e aperte RST.
3. O Monitor Serial mostra o IP: `Coloque no config.toml do PC: url = "http://192.168.x.y/capture"`.
   Alternativa ao monitor (reinicia a placa e mostra o log por 20 s):
   `..\..\.venv\Scripts\python ..\ler_serial.py COM10`
4. Abra `http://IP_DA_CAMERA/` (ou `http://epi-cam.local/`) no navegador: aparece a foto e um
   botão **Vídeo ao vivo**. Outras rotas:
   - `http://epi-cam.local:81/stream?fps=15` — vídeo MJPEG (o que o PC usa);
   - `/capture` — uma foto; `/status` — JSON com resolução, qualidade, sinal Wi-Fi;
   - `/config?resolucao=hvga&qualidade=20` — o PC chama sozinho com os valores do `config.toml`
     (não passa da resolução com que a câmera iniciou, VGA).
5. **Gravar pelo Wi-Fi (OTA)**, sem cabo, depois da primeira gravação pela USB:
   ```powershell
   ..\..\.venv\Scripts\pio run -e esp32cam_ota -t upload
   ```
   Para exigir senha, defina `OTA_SENHA` em `credenciais.h` (ver `credenciais.example.h`).

## Passo 4 — Gravar o ESP32 dos LEDs

1. Edite `firmware/esp32_led/include/credenciais.h` com o mesmo Wi-Fi.
2. Escolha o ambiente da sua placa (`esp32dev`, `esp32s3`, `esp32s3_octal` ou `esp32c3`) e grave.
   Não sabe qual é? `..\..\.venv\Scripts\python %USERPROFILE%\.platformio\packages\tool-esptoolpy\esptool.py --port COM11 flash_id`
   mostra o chip; se aparecer `Flash type set in eFuse: octal`, use `esp32s3_octal`.
   ```powershell
   cd ..\firmware\esp32_led
   ..\..\.venv\Scripts\pio run -e esp32dev -t upload
   ..\..\.venv\Scripts\pio device monitor
   ```
   No VS Code, troque o ambiente na barra inferior (`env:esp32dev`).
3. Ao ligar, os LEDs piscam verde → vermelho (autoteste) e depois o vermelho pisca devagar
   (ainda sem sinal do PC). O Monitor Serial mostra o IP.
4. Abra `http://IP_DO_ESP32/` (ou `http://epi-led.local/`) no navegador: tem botões para testar
   verde/vermelho.
5. Pela serial também dá para testar sem rede: digite `ok`, `sem_epi`, `vazio` ou `erro`. O comando
   **`diag`** mostra o estado, o nível de cada pino de LED e o pulso do servo, por exemplo
   `DIAG estado=ok verde(GPIO5)=1 vermelho(GPIO4)=0 ...` / `DIAG servo(GPIO6) duty=1319 pulso_us=1610`.
   Se o pino está em 1 e o LED não acende, o problema é fiação (GND!), não código.
6. Servo não mexe? Grave `firmware/servo_teste` (`pio run -e esp32s3_octal -t upload` naquela
   pasta): ele só faz o servo ir e voltar. Se ali funciona, servo e fonte estão bons. Depois grave
   o `esp32_led` de novo.

> Sem Wi-Fi? Deixe esse ESP32 **ligado no USB do PC** e use `modo = "serial"` no `config.toml`
> (feche o Monitor Serial antes, ele ocupa a porta COM).

## Passo 5 — Configurar o PC

Edite `pc/config.toml`:

```toml
[camera]
url = "http://epi-cam.local:81/stream?fps=15"  # vídeo; ou ".../capture" para foto a foto
resolucao = "hvga"                    # qqvga | qvga | cif | hvga | vga
qualidade_jpeg = 20                   # 4 (melhor) a 63 (pior, menor e mais rápido)
rotacao = 270                         # se a foto vier de lado/invertida (0, 90, 180, 270)

[led]
modo = "http"
url = "http://epi-led.local"          # nome mDNS do ESP32 dos LEDs (ou o IP)
```

Pelo nome `.local` não é preciso mudar nada quando o hotspot do celular troca de rede: o PC
resolve o nome uma vez e resolve de novo se a placa parar de responder. Se o seu Windows não
encontrar `epi-cam.local`, use os IPs que as placas mostram no Monitor Serial
(ex.: `http://192.168.0.50:81/stream?fps=15`).

O vídeo usa uma conexão só e tem bem menos atraso que pedir foto por foto. Medido no hotspot:
HVGA/qualidade 20 ≈ 7 KB por quadro; VGA/12 ≈ 15 KB. O PC busca o próximo quadro enquanto o
YOLO analisa o atual, e manda o estado aos LEDs em segundo plano (na hora quando muda, e a cada
1 s para a placa saber que o PC está vivo).

## Passo 6 — Testar por partes

```powershell
python testar_camera.py              # baixa fotos da ESP32-CAM -> teste_camera.jpg
python testar_led.py ok              # verde por 5 s
python testar_led.py ciclo           # ok, sem_epi, vazio, erro
python main.py --imagem teste_camera.jpg   # roda o YOLO numa foto e mostra o resultado
python main.py --fonte webcam --led nenhum # testa o YOLO com a webcam do notebook
```

## Passo 7 — Rodar tudo

```powershell
python main.py
```

Abre uma janela com a imagem, as caixas detectadas e uma faixa verde/vermelha. Teclas:
`q` sai, `s` salva a imagem. Fotos de infração vão para `pc/infracoes/`.
Sem janela: `python main.py --sem-janela`.

## Como a decisão é feita

1. Se o modelo encontrar uma **cabeça sem capacete** → `sem_epi`.
2. Se encontrar **pessoas**, cada uma precisa ter um capacete na parte de cima da caixa
   (≈ 40 % superior). Capacete na mão ou na mesa **não conta** → `sem_epi`.
3. Só capacete, sem pessoa (rosto em close) → `ok`.
4. Nada detectado → `vazio`.
5. O LED só troca depois de **2 resultados iguais seguidos** (`regras.confirmacoes`),
   para não ficar piscando com um erro isolado do YOLO. Para apagar (ninguém) são 4
   (`regras.confirmacoes_vazio`), porque a pessoa sai do quadro por instantes.

Tudo isso é ajustável em `[regras]` do `config.toml`. As regras têm testes em `pc/tests/`.

## Problemas comuns

| Sintoma | Causa provável / solução |
|---|---|
| ESP32-CAM reinicia com `Brownout detector was triggered` | Fonte fraca. Use 5 V/2 A e cabo curto. Último recurso: `#define DESATIVAR_BROWNOUT 1` em `credenciais.h`. |
| `esp_camera_init falhou (0x105)` | Cabo flat da câmera mal encaixado. |
| ESP não conecta no Wi-Fi | Rede 5 GHz ou rede corporativa/acadêmica (WPA2-Enterprise). Use o **roteador do celular** (2,4 GHz). |
| PC não alcança os IPs (timeout) | PC em outra rede, ou a rede isola os dispositivos (comum em Wi-Fi de faculdade). Use o hotspot do celular. Desative VPN. |
| Imagem de lado / de cabeça para baixo | `rotacao = 90` (ou 180/270) em `[camera]` no `config.toml`. O YOLO erra muito com imagem deitada. |
| `Modelo nao encontrado` | Faça o Passo 2. |
| Aviso `O modelo NAO tem classe de capacete` | Você está usando um YOLO genérico; treine (Passo 2) ou ajuste `[classes]`. |
| Detecta pouco/errado | Melhore a luz, aproxime a câmera, use `flash = true`, baixe `confianca` para 0,35 ou treine mais épocas. |
| `Nao abriu COM5` | Porta errada ou Monitor Serial aberto. Veja a porta no Gerenciador de Dispositivos. |
| ESP32-S3/C3 aparece como "Dispositivo USB Desconhecido" | O programa antigo trava a USB nativa. Segure **BOOT**, aperte e solte **RST**, solte o BOOT: a porta aparece e dá para gravar. |
| S3 gravou mas não roda (nada na serial, não entra no Wi-Fi) | Ficou no modo de gravação: aperte **RST**. S3 com memória octal precisa do ambiente `esp32s3_octal`. |
| S3 octal em loop de boot / trava ao ligar algo | Ambiente errado (use `esp32s3_octal`) ou algo ligado nos GPIO 33–37. |
| LEDs/servo param e só voltam quando abre o Monitor Serial | Firmware antigo: o log da USB nativa enchia e travava o loop. Grave o `esp32_led` atual (descarta o log se ninguém lê). |
| LEDs e servo param juntos, mas `diag` mostra os pinos mudando | Fiação: GND do ESP, da fonte do servo e dos LEDs precisa ser o mesmo. Veja se a trilha de GND da protoboard é dividida no meio. |
| LEDs acendem trocados (verde sem capacete) | Fios do verde e vermelho invertidos: troque `PINO_LED_VERDE`/`PINO_LED_VERMELHO` no `platformio.ini`. |
| Servo mexe uma vez e trava | Fonte fraca (use ≥ 1 A + capacitor), GND não comum, ou servo de 360° (gira sem parar em vez de ir a um ângulo). Teste com `firmware/servo_teste`. |
| Estado fica trocando ok ↔ sem_epi | Capacete num ângulo em que o modelo fica em dúvida. Suba `regras.confirmacoes` para 4. |
| ESP32-CAM: `No serial data received` no upload | Contato ruim entre a placa e a base MB: encaixe de novo. Ou grave pelo Wi-Fi (`esp32cam_ota`). |
| Só tenho um cabo USB | Grave uma placa de cada vez. Para rodar tudo junto, ligue a ESP32-CAM num carregador de celular / power bank e o sinalizador no PC (ou vice-versa): eles conversam pelo Wi-Fi. |

## Observações

- HTTP sem senha: use numa rede local controlada. O OTA da câmera sem `OTA_SENHA` deixa qualquer
  um na mesma rede regravá-la.
- O dataset Construction-PPE é CC BY 4.0; a Ultralytics é AGPL-3.0.
