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
#include <WebServer.h>
#include <WiFi.h>

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

WebServer servidor(80);
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

  static Estado ultimoLogado = Estado::SEM_SINAL;
  const Estado e = estadoEfetivo();
  if (e != ultimoLogado) {
    ultimoLogado = e;
    Serial.printf("[LED] estado: %s\n", nomeEstado(e));
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
  atualizarLeds();
}

// ---------------------------------------------------------------------------
// HTTP
// ---------------------------------------------------------------------------

static void responderJson(int codigo, const char *erro = nullptr) {
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
  servidor.sendHeader("Access-Control-Allow-Origin", "*");
  servidor.sendHeader("Cache-Control", "no-store");
  servidor.send(codigo, "application/json", json);
}

static void rotaEstado() {
  String valor;
  if (servidor.hasArg("valor")) {
    valor = servidor.arg("valor");
  } else if (servidor.method() == HTTP_POST && servidor.hasArg("plain")) {
    valor = servidor.arg("plain");  // corpo cru, ex.: curl -d ok
  }

  if (valor.length() > 0) {
    Estado e;
    if (!interpretar(valor, e)) {
      responderJson(400, "valor_invalido");
      return;
    }
    aplicar(e);
  }
  responderJson(200);
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

static void rotaRaiz() { servidor.send_P(200, "text/html", PAGINA); }

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
        if (interpretar(String(buf), e)) {
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
  Serial.println("==============================================");
  Serial.print("Wi-Fi OK. IP do sinalizador: ");
  Serial.println(WiFi.localIP());
  Serial.print("Coloque no config.toml do PC:  url = \"http://");
  Serial.print(WiFi.localIP());
  Serial.println("\"");
  Serial.println("==============================================");
  if (!mdnsIniciado && MDNS.begin(NOME_HOST)) {
    MDNS.addService("http", "tcp", 80);
    mdnsIniciado = true;
    Serial.println("mDNS: http://" NOME_HOST ".local");
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

  // Teste rapido dos LEDs ao ligar: verde, vermelho, apaga.
  escreverLeds(true, false);
  delay(400);
  escreverLeds(false, true);
  delay(400);
  escreverLeds(false, false);

  iniciarWifi();

  servidor.on("/estado", HTTP_ANY, rotaEstado);
  servidor.on("/", HTTP_GET, rotaRaiz);
  servidor.onNotFound([]() { servidor.send(404, "text/plain", "Use /estado?valor=ok|sem_epi|vazio|erro"); });
  servidor.begin();
}

void loop() {
  servidor.handleClient();
  lerSerial();
  atualizarLeds();

  static bool estavaConectado = false;
  static uint32_t ultimaTentativa = 0;
  const bool conectado = WiFi.status() == WL_CONNECTED;
  if (conectado && !estavaConectado) {
    anunciarEndereco();
  } else if (!conectado && estavaConectado) {
    Serial.println("Wi-Fi caiu, reconectando...");
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
