/*
 * ESP32 sinalizador de EPI.
 *
 * Recebe do PC o resultado do YOLO e acende os LEDs:
 *
 *   estado      | LED VERDE | LED VERMELHO        | significado
 *   ------------+-----------+---------------------+-----------------------------
 *   ok          | aceso     | apagado             | EPI correta (capacete)
 *   sem_epi     | apagado   | aceso               | EPI incorreta / sem capacete
 *   vazio       | apagado   | apagado             | ninguem na frente da camera
 *   erro        | apagado   | pisca rapido        | PC sem foto da camera / erro
 *   (sem sinal) | apagado   | pisca devagar       | PC parou de mandar ha > 6 s
 *
 * Duas formas de receber o estado (use a que for mais pratica):
 *   1) Wi-Fi/HTTP:  GET http://IP/estado?valor=ok   (ou POST com o mesmo campo)
 *   2) Cabo USB:    escrever "ok\n" na serial (115200) - funciona mesmo sem Wi-Fi
 *
 * GET /estado sem parametro so consulta. GET / mostra uma pagina com botoes de teste.
 */
#include <Arduino.h>
#include <ESPmDNS.h>
#include <WiFi.h>

#include "esp_http_server.h"
#include "soc/gpio_struct.h"

#if __has_include("credenciais.h")
#include "credenciais.h"
#else
#error "Copie include/credenciais.example.h para include/credenciais.h e preencha o Wi-Fi."
#endif

// ---------------------------------------------------------------------------
// Ajustes (os pinos vem do platformio.ini de cada placa)
// ---------------------------------------------------------------------------
#ifndef PINO_LED_VERDE
#define PINO_LED_VERDE 26
#endif
#ifndef PINO_LED_VERMELHO
#define PINO_LED_VERMELHO 27
#endif
#ifndef PINO_LED_RGB
#define PINO_LED_RGB -1  // LED RGB enderecavel (WS2812) da placa; -1 = nao usa
#endif
#ifndef BRILHO_RGB
#define BRILHO_RGB 60  // 0-255
#endif
#ifndef LED_ATIVO_ALTO
#define LED_ATIVO_ALTO 1  // 1: pino -> resistor -> LED -> GND
#endif
#ifndef PINO_SERVO
#define PINO_SERVO -1  // servo (catraca/cancela): abre com EPI ok; -1 = sem servo
#endif
#ifndef SERVO_ABERTO
#define SERVO_ABERTO 90  // graus
#endif
#ifndef SERVO_FECHADO
#define SERVO_FECHADO 0
#endif
#ifndef TEMPO_SEM_SINAL_MS
#define TEMPO_SEM_SINAL_MS 6000  // sem mensagem do PC por esse tempo -> "sem sinal"
#endif
#ifndef NOME_HOST
#define NOME_HOST "epi-led"
#endif

enum class Estado : uint8_t { SEM_SINAL, OK, SEM_EPI, VAZIO, ERRO };

static const char *nomeEstado(Estado e) {
  switch (e) {
    case Estado::OK: return "ok";
    case Estado::SEM_EPI: return "sem_epi";
    case Estado::VAZIO: return "vazio";
    case Estado::ERRO: return "erro";
    default: return "sem_sinal";
  }
}

static Estado estadoRecebido = Estado::SEM_SINAL;
static uint32_t ultimoSinalMs = 0;
static bool recebeuAlgum = false;
static uint32_t totalMensagens = 0;
static bool mdnsIniciado = false;

// ---------------------------------------------------------------------------
// LEDs
// ---------------------------------------------------------------------------

static void escreverLeds(bool verde, bool vermelho) {
  const uint8_t ligado = LED_ATIVO_ALTO ? HIGH : LOW;
  const uint8_t desligado = LED_ATIVO_ALTO ? LOW : HIGH;
  digitalWrite(PINO_LED_VERDE, verde ? ligado : desligado);
  digitalWrite(PINO_LED_VERMELHO, vermelho ? ligado : desligado);

#if PINO_LED_RGB >= 0
  // So reescreve o RGB quando muda (evita ocupar o RMT a cada loop).
  static int8_t ultimo = -1;
  const int8_t atual = (verde ? 1 : 0) | (vermelho ? 2 : 0);
  if (atual != ultimo) {
    ultimo = atual;
    neopixelWrite(PINO_LED_RGB, vermelho ? BRILHO_RGB : 0, verde ? BRILHO_RGB : 0, 0);
  }
#endif
}

// Log pela serial que NUNCA trava o loop: se nenhum programa esta lendo a porta USB,
// o buffer enche e o Serial.print fica esperando -> LEDs e servo paravam de atualizar
// (so voltavam quando alguem abria o Monitor Serial). Sem espaco, a mensagem e descartada.
static void logf(const char *fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  const int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n <= 0) return;
  const size_t len = n < (int)sizeof(buf) ? n : sizeof(buf) - 1;
  if ((size_t)Serial.availableForWrite() >= len) Serial.write((const uint8_t *)buf, len);
}

// Servo por PWM (LEDC) a 50 Hz: pulso de 0,5 ms (0 graus) a 2,5 ms (180 graus).
#if PINO_SERVO >= 0
static const int CANAL_SERVO = 4;
static void iniciarServo() {
  ledcSetup(CANAL_SERVO, 50, 14);
  ledcAttachPin(PINO_SERVO, CANAL_SERVO);
}
static void escreverServo(int graus) {
  const uint32_t pulsoUs = 500 + (uint32_t)graus * 2000 / 180;
  ledcWrite(CANAL_SERVO, pulsoUs * 16384 / 20000);
}
// Vai ate o alvo aos poucos (2 graus a cada 15 ms), sem travar o loop: o salto direto
// puxava um pico de corrente e o servo travava depois da primeira vez.
static void moverServo(int alvo) {
  static int atual = -1, ultimoAlvo = -1;
  static uint32_t ultimoPassoMs = 0;
  if (atual < 0) {  // primeira vez: posiciona direto
    atual = ultimoAlvo = alvo;
    escreverServo(atual);
    logf("[SERVO] %d graus\n", alvo);
    return;
  }
  if (alvo != ultimoAlvo) {
    ultimoAlvo = alvo;
    logf("[SERVO] indo para %d graus\n", alvo);
  }
  if (atual == alvo || millis() - ultimoPassoMs < 15) return;
  ultimoPassoMs = millis();
  atual += constrain(alvo - atual, -2, 2);
  escreverServo(atual);
}
#else
static void iniciarServo() {}
static void moverServo(int) {}
#endif

static Estado estadoEfetivo() {
  if (!recebeuAlgum || millis() - ultimoSinalMs > TEMPO_SEM_SINAL_MS) {
    return Estado::SEM_SINAL;
  }
  return estadoRecebido;
}

static void atualizarLeds() {
  const uint32_t agora = millis();
  switch (estadoEfetivo()) {
    case Estado::OK: escreverLeds(true, false); break;
    case Estado::SEM_EPI: escreverLeds(false, true); break;
    case Estado::VAZIO: escreverLeds(false, false); break;
    case Estado::ERRO: escreverLeds(false, (agora / 125) % 2 == 0); break;     // 4 Hz
    case Estado::SEM_SINAL: escreverLeds(false, (agora / 500) % 2 == 0); break;  // 1 Hz
  }

  const Estado e = estadoEfetivo();
  moverServo(e == Estado::OK ? SERVO_ABERTO : SERVO_FECHADO);  // so abre com EPI ok

  static Estado ultimoLogado = Estado::SEM_SINAL;
  if (e != ultimoLogado) {
    ultimoLogado = e;
    logf("[LED] estado: %s\n", nomeEstado(e));
  }
}

// ---------------------------------------------------------------------------
// Interpretacao do comando (mesma para HTTP e Serial)
// ---------------------------------------------------------------------------

static bool interpretar(String txt, Estado &saida) {
  txt.trim();
  txt.toLowerCase();
  if (txt == "ok" || txt == "verde" || txt == "green" || txt == "conforme" || txt == "1") {
    saida = Estado::OK;
  } else if (txt == "sem_epi" || txt == "vermelho" || txt == "red" || txt == "nao_conforme" ||
             txt == "fail" || txt == "0") {
    saida = Estado::SEM_EPI;
  } else if (txt == "vazio" || txt == "ninguem" || txt == "none" || txt == "off" || txt == "apagado") {
    saida = Estado::VAZIO;
  } else if (txt == "erro" || txt == "error") {
    saida = Estado::ERRO;
  } else {
    return false;
  }
  return true;
}

static void aplicar(Estado e) {
  estadoRecebido = e;
  ultimoSinalMs = millis();
  recebeuAlgum = true;
  totalMensagens++;
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

// Servidor do ESP-IDF: mantem a conexao aberta (keep-alive) com o PC. O WebServer do
// Arduino abria/fechava uma conexao por mensagem e travava apos alguns segundos no S3.
static esp_err_t responderJson(httpd_req_t *req, const char *status, const char *erro = nullptr) {
  char json[224];
  if (erro != nullptr) {
    snprintf(json, sizeof(json), "{\"erro\":\"%s\",\"validos\":[\"ok\",\"sem_epi\",\"vazio\",\"erro\"]}", erro);
  } else {
    snprintf(json, sizeof(json),
             "{\"dispositivo\":\"esp32_led\",\"estado\":\"%s\",\"ip\":\"%s\",\"rssi\":%d,"
             "\"mensagens\":%lu,\"ms_desde_ultimo_sinal\":%lu}",
             nomeEstado(estadoEfetivo()), WiFi.localIP().toString().c_str(), WiFi.RSSI(),
             (unsigned long)totalMensagens,
             recebeuAlgum ? (unsigned long)(millis() - ultimoSinalMs) : 0UL);
  }
  httpd_resp_set_status(req, status);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  return httpd_resp_sendstr(req, json);
}

static esp_err_t rotaEstado(httpd_req_t *req) {
  char query[64] = {0};
  char valor[32] = {0};
  bool tem = false;
  if (httpd_req_get_url_query_str(req, query, sizeof(query)) == ESP_OK &&
      httpd_query_key_value(query, "valor", valor, sizeof(valor)) == ESP_OK) {
    tem = true;
  } else if (req->method == HTTP_POST && req->content_len > 0 && req->content_len < sizeof(valor)) {
    const int n = httpd_req_recv(req, valor, req->content_len);  // corpo "ok" ou "valor=ok"
    if (n > 0) {
      valor[n] = '\0';
      tem = true;
    }
  }
  if (tem) {
    String txt(valor);
    if (txt.startsWith("valor=")) txt = txt.substring(6);
    Estado e;
    if (!interpretar(txt, e)) return responderJson(req, "400 Bad Request", "valor_invalido");
    aplicar(e);
  }
  return responderJson(req, "200 OK");
}

static const char PAGINA[] PROGMEM = R"HTML(<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ESP32 sinalizador EPI</title>
<style>
 body{font-family:system-ui,sans-serif;margin:16px;background:#111;color:#eee}
 button{font-size:18px;margin:6px;padding:10px 16px;border:0;border-radius:6px;color:#fff}
 #estado{font-size:28px;font-weight:bold;margin:16px 0}
</style></head><body>
<h2>ESP32 &mdash; sinalizador de EPI</h2>
<div id="estado">...</div>
<button style="background:#1a7f37" onclick="enviar('ok')">ok (verde)</button>
<button style="background:#c62828" onclick="enviar('sem_epi')">sem_epi (vermelho)</button>
<button style="background:#555" onclick="enviar('vazio')">vazio (apagado)</button>
<button style="background:#e65100" onclick="enviar('erro')">erro (pisca)</button>
<p>Sem mensagens por alguns segundos a placa volta para <b>sem_sinal</b> (vermelho piscando devagar).</p>
<pre id="info"></pre>
<script>
function mostrar(j){document.getElementById('estado').textContent='Estado: '+j.estado;
 document.getElementById('info').textContent=JSON.stringify(j,null,1);}
function enviar(v){fetch('/estado?valor='+v).then(r=>r.json()).then(mostrar);}
function consultar(){fetch('/estado').then(r=>r.json()).then(mostrar).catch(()=>{});}
consultar(); setInterval(consultar,1000);
</script></body></html>)HTML";

static esp_err_t rotaRaiz(httpd_req_t *req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_sendstr(req, PAGINA);
}

static httpd_handle_t servidor = nullptr;

static void iniciarServidor() {
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.server_port = 80;
  cfg.max_open_sockets = 5;
  cfg.lru_purge_enable = true;  // conexao velha e liberada quando chega outra
  if (httpd_start(&servidor, &cfg) != ESP_OK) {
    Serial.println("ERRO: nao iniciou o servidor HTTP");
    return;
  }
  httpd_uri_t u = {};
  u.uri = "/estado";
  u.handler = rotaEstado;
  u.method = HTTP_GET;
  httpd_register_uri_handler(servidor, &u);
  u.method = HTTP_POST;
  httpd_register_uri_handler(servidor, &u);
  u.uri = "/";
  u.method = HTTP_GET;
  u.handler = rotaRaiz;
  httpd_register_uri_handler(servidor, &u);
}

// ---------------------------------------------------------------------------
// Serial (alternativa ao Wi-Fi: ESP32 ligado no USB do PC)
// ---------------------------------------------------------------------------

static void lerSerial() {
  static char buf[32];
  static uint8_t n = 0;
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (n > 0) {
        buf[n] = '\0';
        Estado e;
        if (strcmp(buf, "diag") == 0) {
          // Nivel que o chip esta colocando em cada pino de LED (1 = 3,3 V)
          Serial.printf("DIAG estado=%s verde(GPIO%d)=%d vermelho(GPIO%d)=%d wifi=%d heap=%u\n",
                        nomeEstado(estadoEfetivo()), PINO_LED_VERDE,
                        (int)((GPIO.out >> PINO_LED_VERDE) & 1), PINO_LED_VERMELHO,
                        (int)((GPIO.out >> PINO_LED_VERMELHO) & 1), WiFi.status() == WL_CONNECTED,
                        (unsigned)ESP.getFreeHeap());
#if PINO_SERVO >= 0
          // Pulso que esta saindo no pino do servo: ~410 = 0 graus (0,5 ms), ~1229 = 90 graus (1,5 ms)
          Serial.printf("DIAG servo(GPIO%d) duty=%u pulso_us=%u\n", PINO_SERVO, (unsigned)ledcRead(CANAL_SERVO),
                        (unsigned)(ledcRead(CANAL_SERVO) * 20000UL / 16384));
#endif
        } else if (interpretar(String(buf), e)) {
          aplicar(e);
          Serial.printf("OK %s\n", nomeEstado(e));
        } else {
          Serial.printf("ERRO comando invalido: %s (use ok, sem_epi, vazio, erro)\n", buf);
        }
        n = 0;
      }
    } else if (n < sizeof(buf) - 1) {
      buf[n++] = c;
    }
  }
}

// ---------------------------------------------------------------------------
// Wi-Fi
// ---------------------------------------------------------------------------

static void anunciarEndereco() {
  const String ip = WiFi.localIP().toString();
  logf("==============================================\n");
  logf("Wi-Fi OK. IP do sinalizador: %s\n", ip.c_str());
  logf("Coloque no config.toml do PC:  url = \"http://%s\"\n", ip.c_str());
  logf("==============================================\n");
  if (!mdnsIniciado && MDNS.begin(NOME_HOST)) {
    MDNS.addService("http", "tcp", 80);
    mdnsIniciado = true;
    logf("mDNS: http://" NOME_HOST ".local\n");
  }
}

static void iniciarWifi() {
  if (strcmp(WIFI_SSID, "NOME_DA_REDE") == 0) {
    Serial.println("ATENCAO: edite include/credenciais.h com o nome e a senha do seu Wi-Fi!");
  }
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setHostname(NOME_HOST);
  WiFi.setAutoReconnect(true);
#ifdef IP_FIXO
  WiFi.config(IPAddress(IP_FIXO), IPAddress(GATEWAY), IPAddress(MASCARA), IPAddress(GATEWAY));
#endif
  WiFi.begin(WIFI_SSID, WIFI_SENHA);
  Serial.printf("Conectando em \"%s\" (a serial ja aceita comandos)...\n", WIFI_SSID);
}

// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(115200);
#if ARDUINO_USB_MODE && ARDUINO_USB_CDC_ON_BOOT
  Serial.setTxTimeoutMs(0);  // USB nativa: nao trava o loop se nenhum PC estiver lendo
#endif
  delay(300);
  Serial.println("\n=== ESP32 sinalizador de EPI ===");

  pinMode(PINO_LED_VERDE, OUTPUT);
  pinMode(PINO_LED_VERMELHO, OUTPUT);
  iniciarServo();
  moverServo(SERVO_FECHADO);

  // Teste rapido dos LEDs ao ligar: verde, vermelho, apaga.
  escreverLeds(true, false);
  delay(400);
  escreverLeds(false, true);
  delay(400);
  escreverLeds(false, false);

  iniciarWifi();

  iniciarServidor();
}

void loop() {
  lerSerial();
  atualizarLeds();

  static bool estavaConectado = false;
  static uint32_t ultimaTentativa = 0;
  const bool conectado = WiFi.status() == WL_CONNECTED;
  if (conectado && !estavaConectado) {
    anunciarEndereco();
  } else if (!conectado && estavaConectado) {
    logf("Wi-Fi caiu, reconectando...\n");
  }
  if (!conectado && millis() - ultimaTentativa > 15000) {
    ultimaTentativa = millis();
    if (!estavaConectado) {
      WiFi.disconnect();
      WiFi.begin(WIFI_SSID, WIFI_SENHA);
    }
  }
  estavaConectado = conectado;

  delay(2);
}
