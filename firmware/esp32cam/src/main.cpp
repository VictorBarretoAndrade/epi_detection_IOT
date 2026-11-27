/*
 * ESP32-CAM (AI Thinker) - fornece fotos JPEG para o PC rodar o YOLO.
 *
 * Rotas HTTP (porta 80):
 *   GET /capture          -> tira uma foto NOVA e devolve o JPEG
 *   GET /capture?flash=1  -> idem, acendendo o LED de flash durante a foto
 *   GET /status           -> JSON com IP, sinal Wi-Fi, fotos servidas, resolucao...
 *   GET /config?resolucao=hvga&qualidade=15 -> muda resolucao/qualidade sem regravar
 *   GET /                 -> pagina de teste (mostra a foto no navegador)
 *
 * Video continuo (porta 81) - MENOR LATENCIA, e o que o PC usa:
 *   GET :81/stream        -> MJPEG (multipart), quadros sem parar numa unica conexao
 *   GET :81/stream?fps=15 -> limita a taxa de quadros
 *
 * Tambem responde por mDNS em http://epi-cam.local (quando a rede permite).
 */
#include <Arduino.h>
#include <ArduinoOTA.h>
#include <ESPmDNS.h>
#include <WebServer.h>
#include <WiFi.h>

#include "esp_camera.h"
#include "esp_http_server.h"
#include "lwip/sockets.h"
#include "soc/rtc_cntl_reg.h"
#include "soc/soc.h"

#if __has_include("credenciais.h")
#include "credenciais.h"
#else
#error "Copie include/credenciais.example.h para include/credenciais.h e preencha o Wi-Fi."
#endif

// ---------------------------------------------------------------------------
// Ajustes (podem ser sobrescritos em credenciais.h ou em build_flags)
// ---------------------------------------------------------------------------
#ifndef NOME_HOST
#define NOME_HOST "epi-cam"
#endif
#ifndef TAMANHO_FOTO
#define TAMANHO_FOTO FRAMESIZE_VGA  // 640x480: bom para YOLO com imgsz=640
#endif
#ifndef QUALIDADE_JPEG
#define QUALIDADE_JPEG 12  // 0-63, menor = melhor qualidade (e arquivo maior)
#endif
#ifndef INVERTER_VERTICAL
#define INVERTER_VERTICAL 0  // 1 se a imagem sair de cabeca para baixo
#endif
#ifndef ESPELHAR_HORIZONTAL
#define ESPELHAR_HORIZONTAL 0
#endif
#ifndef DESATIVAR_BROWNOUT
// 1 desliga o detector de queda de tensao. So use se a placa reiniciar com
// "Brownout detector was triggered" e voce NAO tiver uma fonte 5 V melhor.
#define DESATIVAR_BROWNOUT 0
#endif

// ---------------------------------------------------------------------------
// Pinos da camera AI Thinker ESP32-CAM
// ---------------------------------------------------------------------------
#define PWDN_GPIO_NUM 32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM 0
#define SIOD_GPIO_NUM 26
#define SIOC_GPIO_NUM 27
#define Y9_GPIO_NUM 35
#define Y8_GPIO_NUM 34
#define Y7_GPIO_NUM 39
#define Y6_GPIO_NUM 36
#define Y5_GPIO_NUM 21
#define Y4_GPIO_NUM 19
#define Y3_GPIO_NUM 18
#define Y2_GPIO_NUM 5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM 23
#define PCLK_GPIO_NUM 22

#define PINO_FLASH 4      // LED branco forte
#define PINO_LED_STATUS 33  // LED vermelho pequeno (ativo em BAIXO)

static const uint32_t TEMPO_MAX_SEM_WIFI_MS = 60000;  // reinicia se ficar 1 min sem Wi-Fi

WebServer servidor(80);
static httpd_handle_t servidorVideo = nullptr;
static volatile uint32_t fotosServidas = 0;
static uint32_t falhasCaptura = 0;
static uint32_t desconectadoDesde = 0;
static bool mdnsIniciado = false;

// ---------------------------------------------------------------------------

static bool iniciarCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;
  c.pin_d1 = Y3_GPIO_NUM;
  c.pin_d2 = Y4_GPIO_NUM;
  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;
  c.pin_d5 = Y7_GPIO_NUM;
  c.pin_d6 = Y8_GPIO_NUM;
  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;
  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;
  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;
  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;
  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    c.frame_size = TAMANHO_FOTO;
    c.jpeg_quality = QUALIDADE_JPEG;
    c.fb_count = 2;
    c.fb_location = CAMERA_FB_IN_PSRAM;
    c.grab_mode = CAMERA_GRAB_LATEST;  // sempre o quadro mais recente
  } else {
    Serial.println("AVISO: PSRAM nao encontrada, usando 320x240.");
    c.frame_size = FRAMESIZE_QVGA;
    c.jpeg_quality = 15;
    c.fb_count = 1;
    c.fb_location = CAMERA_FB_IN_DRAM;
    c.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("ERRO: esp_camera_init falhou (0x%x). Confira o cabo flat da camera.\n", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s != nullptr) {
    s->set_vflip(s, INVERTER_VERTICAL);
    s->set_hmirror(s, ESPELHAR_HORIZONTAL);
    if (s->id.PID == OV3660_PID) {  // alguns modulos vem com OV3660
      s->set_brightness(s, 1);
      s->set_saturation(s, -2);
    }
  }
  return true;
}

static void conectarWifi() {
  if (strcmp(WIFI_SSID, "NOME_DA_REDE") == 0) {
    Serial.println("ATENCAO: edite include/credenciais.h com o nome e a senha do seu Wi-Fi!");
  }
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // menor latencia nas requisicoes
  WiFi.setHostname(NOME_HOST);
  WiFi.setAutoReconnect(true);
#ifdef IP_FIXO
  WiFi.config(IPAddress(IP_FIXO), IPAddress(GATEWAY), IPAddress(MASCARA), IPAddress(GATEWAY));
#endif
  WiFi.begin(WIFI_SSID, WIFI_SENHA);

  Serial.printf("Conectando em \"%s\"", WIFI_SSID);
  uint32_t inicio = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 20000) {
    delay(300);
    Serial.print('.');
  }
  Serial.println();
}

static void anunciarEndereco() {
  Serial.println("==============================================");
  Serial.print("Wi-Fi OK. IP da camera: ");
  Serial.println(WiFi.localIP());
  Serial.print("Coloque no config.toml do PC:  url = \"http://");
  Serial.print(WiFi.localIP());
  Serial.println("/capture\"");
  Serial.println("==============================================");
  if (!mdnsIniciado && MDNS.begin(NOME_HOST)) {
    MDNS.addService("http", "tcp", 80);
    mdnsIniciado = true;
    Serial.println("mDNS: http://" NOME_HOST ".local");
  }
}

// Gravacao pelo Wi-Fi (PlatformIO: ambiente esp32cam_ota). Evita depender do USB/base MB.
static void iniciarOTA() {
  ArduinoOTA.setHostname(NOME_HOST);
  ArduinoOTA.setMdnsEnabled(false);  // o mDNS ja foi iniciado em anunciarEndereco()
#ifdef OTA_SENHA
  ArduinoOTA.setPassword(OTA_SENHA);
#endif
  ArduinoOTA.onStart([]() {
    Serial.println("OTA: recebendo firmware novo...");
    if (servidorVideo != nullptr) {
      httpd_stop(servidorVideo);  // libera a camera e a rede para a gravacao
      servidorVideo = nullptr;
    }
  });
  ArduinoOTA.onEnd([]() { Serial.println("OTA: pronto, reiniciando."); });
  ArduinoOTA.onError([](ota_error_t e) { Serial.printf("OTA: erro %u\n", e); });
  ArduinoOTA.begin();
  Serial.println("OTA pelo Wi-Fi ativo (porta 3232).");
}

// ---------------------------------------------------------------------------
// Rotas HTTP
// ---------------------------------------------------------------------------

static void rotaCaptura() {
  const bool comFlash = servidor.hasArg("flash") && servidor.arg("flash") != "0";
  if (comFlash) {
    digitalWrite(PINO_FLASH, HIGH);
    delay(100);  // deixa a exposicao se ajustar a luz
  }

  // Descarta um quadro que pode ter sido capturado antes da requisicao,
  // garantindo que a foto enviada e realmente "de agora".
  camera_fb_t *fb = esp_camera_fb_get();
  if (fb != nullptr) {
    esp_camera_fb_return(fb);
  }
  fb = esp_camera_fb_get();

  if (comFlash) {
    digitalWrite(PINO_FLASH, LOW);
  }

  if (fb == nullptr || fb->format != PIXFORMAT_JPEG || fb->len == 0) {
    if (fb != nullptr) {
      esp_camera_fb_return(fb);
    }
    falhasCaptura++;
    servidor.send(503, "text/plain", "Falha ao capturar foto");
    return;
  }

  fotosServidas++;
  // Sem isso o TCP espera ACKs (Nagle x "delayed ACK" do Windows) e a foto leva ~1 s.
  servidor.client().setNoDelay(true);
  servidor.sendHeader("Cache-Control", "no-store");
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.sendHeader("X-Foto-Id", String(fotosServidas));
  servidor.setContentLength(fb->len);
  servidor.send(200, "image/jpeg", "");
  servidor.sendContent(reinterpret_cast<const char *>(fb->buf), fb->len);
  esp_camera_fb_return(fb);

  digitalWrite(PINO_LED_STATUS, (fotosServidas & 1) ? LOW : HIGH);  // pisca a cada foto
}

// Resolucoes aceitas em /config (nao pode passar do TAMANHO_FOTO usado no boot).
struct Resolucao {
  const char *nome;
  framesize_t tamanho;
};
static const Resolucao RESOLUCOES[] = {
    {"qqvga", FRAMESIZE_QQVGA},  // 160x120
    {"qvga", FRAMESIZE_QVGA},    // 320x240
    {"cif", FRAMESIZE_CIF},      // 400x296
    {"hvga", FRAMESIZE_HVGA},    // 480x320
    {"vga", FRAMESIZE_VGA},      // 640x480
};

static const char *nomeResolucao(framesize_t t) {
  for (const auto &r : RESOLUCOES) {
    if (r.tamanho == t) return r.nome;
  }
  return "?";
}

static void rotaStatus() {
  sensor_t *s = esp_camera_sensor_get();
  char json[320];
  snprintf(json, sizeof(json),
           "{\"dispositivo\":\"esp32cam\",\"ip\":\"%s\",\"rssi\":%d,\"uptime_s\":%lu,"
           "\"fotos\":%lu,\"falhas\":%lu,\"psram\":%s,\"heap_livre\":%u,"
           "\"resolucao\":\"%s\",\"qualidade\":%d}",
           WiFi.localIP().toString().c_str(), WiFi.RSSI(), (unsigned long)(millis() / 1000),
           (unsigned long)fotosServidas, (unsigned long)falhasCaptura, psramFound() ? "true" : "false",
           (unsigned)ESP.getFreeHeap(), s ? nomeResolucao(s->status.framesize) : "?",
           s ? s->status.quality : -1);
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.send(200, "application/json", json);
}

// GET /config?resolucao=hvga&qualidade=15  -> muda sem regravar (volta ao padrao ao reiniciar)
static void rotaConfig() {
  sensor_t *s = esp_camera_sensor_get();
  if (s == nullptr) {
    servidor.send(503, "text/plain", "Camera indisponivel");
    return;
  }
  if (servidor.hasArg("resolucao")) {
    String nome = servidor.arg("resolucao");
    nome.toLowerCase();
    bool achou = false;
    for (const auto &r : RESOLUCOES) {
      if (nome == r.nome) {
        if (psramFound() && r.tamanho > TAMANHO_FOTO) {
          servidor.send(400, "text/plain", "Resolucao maior que a do boot (TAMANHO_FOTO)");
          return;
        }
        s->set_framesize(s, r.tamanho);
        achou = true;
      }
    }
    if (!achou) {
      servidor.send(400, "text/plain", "Use resolucao=qqvga|qvga|cif|hvga|vga");
      return;
    }
  }
  if (servidor.hasArg("qualidade")) {
    const int q = servidor.arg("qualidade").toInt();
    if (q < 4 || q > 63) {
      servidor.send(400, "text/plain", "qualidade deve ser de 4 (melhor) a 63 (pior)");
      return;
    }
    s->set_quality(s, q);
  }
  rotaStatus();
}

// ---------------------------------------------------------------------------
// Video MJPEG na porta 81 (servidor proprio do ESP-IDF, roda em outra tarefa:
// a porta 80 continua respondendo /capture, /status e /config enquanto o video passa)
// ---------------------------------------------------------------------------
#define LIMITE_PARTE "epi7b3c9f1e2d"
static const char *TIPO_VIDEO = "multipart/x-mixed-replace;boundary=" LIMITE_PARTE;
static const char *SEPARADOR = "\r\n--" LIMITE_PARTE "\r\n";
static const char *CABECALHO_QUADRO = "Content-Type: image/jpeg\r\nContent-Length: %u\r\n\r\n";

static esp_err_t rotaVideo(httpd_req_t *req) {
  // fps maximo opcional: /stream?fps=15
  uint32_t intervaloMs = 0;
  char query[32];
  char valor[8];
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK &&
      httpd_query_key_value(query, "fps", valor, sizeof(valor)) == ESP_OK && atoi(valor) > 0) {
    intervaloMs = 1000 / atoi(valor);
  }

  httpd_resp_set_type(req, TIPO_VIDEO);
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  const int fd = httpd_req_to_sockfd(req);
  const int um = 1;
  setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &um, sizeof(um));  // manda cada quadro na hora

  char cabecalho[64];
  uint32_t ultimo = 0;
  int falhasSeguidas = 0;
  while (true) {
    if (intervaloMs > 0) {
      const uint32_t passou = millis() - ultimo;
      if (passou < intervaloMs) delay(intervaloMs - passou);
      ultimo = millis();
    }
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb == nullptr) {
      falhasCaptura++;
      if (++falhasSeguidas > 20) break;  // camera com problema: encerra e o PC reconecta
      delay(50);
      continue;
    }
    falhasSeguidas = 0;
    const int n = snprintf(cabecalho, sizeof(cabecalho), CABECALHO_QUADRO, (unsigned)fb->len);
    esp_err_t res = httpd_resp_send_chunk(req, SEPARADOR, strlen(SEPARADOR));
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, cabecalho, n);
    if (res == ESP_OK) res = httpd_resp_send_chunk(req, reinterpret_cast<const char *>(fb->buf), fb->len);
    esp_camera_fb_return(fb);
    if (res != ESP_OK) break;  // cliente desconectou
    fotosServidas++;
  }
  return ESP_OK;
}

static void iniciarVideo() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 81;
  cfg.ctrl_port = 32769;
  cfg.max_open_sockets = 3;
  cfg.lru_purge_enable = true;  // conexao velha e derrubada se chegar uma nova
  if (httpd_start(&servidorVideo, &cfg) != ESP_OK) {
    Serial.println("ERRO: nao iniciou o video na porta 81");
    return;
  }
  httpd_uri_t uri = {};
  uri.uri = "/stream";
  uri.method = HTTP_GET;
  uri.handler = rotaVideo;
  httpd_register_uri_handler(servidorVideo, &uri);
  Serial.println("Video MJPEG na porta 81: /stream");
}

static const char PAGINA[] PROGMEM = R"HTML(<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32-CAM EPI</title>
<style>
 body{font-family:system-ui,sans-serif;margin:16px;background:#111;color:#eee}
 img{max-width:100%;border:1px solid #444;display:block;margin:12px 0}
 button,label{font-size:16px;margin-right:12px}
 code{background:#222;padding:2px 6px}
</style></head><body>
<h2>ESP32-CAM &mdash; detector de EPI</h2>
<p>O PC busca as fotos em <code>/capture</code>.</p>
<button onclick="atualizar()">Nova foto</button>
<button onclick="aoVivo()">Video ao vivo</button>
<label><input type="checkbox" id="auto"> atualizar sozinho</label>
<label><input type="checkbox" id="flash"> flash</label>
<img id="foto" alt="foto">
<pre id="info"></pre>
<script>
function atualizar(){
  const f=document.getElementById('flash').checked?'&flash=1':'';
  document.getElementById('foto').src='/capture?t='+Date.now()+f;
  fetch('/status').then(r=>r.json()).then(j=>{
    document.getElementById('info').textContent=JSON.stringify(j,null,1);});
}
function aoVivo(){
  document.getElementById('auto').checked=false;
  document.getElementById('foto').src=location.protocol+'//'+location.hostname+':81/stream';
}
document.getElementById('foto').onload=()=>{
  if(document.getElementById('auto').checked) setTimeout(atualizar,300);
};
atualizar();
</script></body></html>)HTML";

static void rotaRaiz() { servidor.send_P(200, "text/html", PAGINA); }

// ---------------------------------------------------------------------------

void setup() {
#if DESATIVAR_BROWNOUT
  WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);
#endif
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== ESP32-CAM detector de EPI ===");

  pinMode(PINO_FLASH, OUTPUT);
  digitalWrite(PINO_FLASH, LOW);
  pinMode(PINO_LED_STATUS, OUTPUT);
  digitalWrite(PINO_LED_STATUS, HIGH);  // apagado

  if (!iniciarCamera()) {
    Serial.println("Reiniciando em 5 s...");
    delay(5000);
    ESP.restart();
  }
  Serial.println("Camera OK.");

  conectarWifi();
  if (WiFi.status() == WL_CONNECTED) {
    anunciarEndereco();
  } else {
    Serial.println("Sem Wi-Fi ainda. Confira SSID/senha (rede 2,4 GHz). Tentando de novo...");
    desconectadoDesde = millis();
  }

  servidor.on("/capture", HTTP_GET, rotaCaptura);
  servidor.on("/captura", HTTP_GET, rotaCaptura);  // apelido em portugues
  servidor.on("/status", HTTP_GET, rotaStatus);
  servidor.on("/config", HTTP_GET, rotaConfig);
  servidor.on("/", HTTP_GET, rotaRaiz);
  servidor.onNotFound([]() { servidor.send(404, "text/plain", "Rota nao encontrada. Use /capture"); });
  iniciarVideo();
  iniciarOTA();
  servidor.begin();
  Serial.println("Servidor HTTP na porta 80.");
}

void loop() {
  servidor.handleClient();
  ArduinoOTA.handle();

  // Monitora o Wi-Fi: anuncia o IP ao reconectar e reinicia se ficar muito tempo fora.
  static bool estavaConectado = WiFi.status() == WL_CONNECTED;
  const bool conectado = WiFi.status() == WL_CONNECTED;
  if (conectado && !estavaConectado) {
    anunciarEndereco();
  } else if (!conectado && estavaConectado) {
    Serial.println("Wi-Fi caiu, reconectando...");
    desconectadoDesde = millis();
  }
  if (!conectado && millis() - desconectadoDesde > TEMPO_MAX_SEM_WIFI_MS) {
    Serial.println("Muito tempo sem Wi-Fi. Reiniciando...");
    ESP.restart();
  }
  estavaConectado = conectado;

  delay(1);
}
