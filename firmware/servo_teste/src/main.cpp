// Teste do servo: so PWM de 50 Hz no PINO_SERVO, indo e voltando.
#include <Arduino.h>

static const int CANAL = 4;

static void angulo(int graus) {
  const uint32_t pulsoUs = 500 + (uint32_t)graus * 2000 / 180;  // 0.5 ms a 2.5 ms
  ledcWrite(CANAL, pulsoUs * 16384 / 20000);                    // periodo 20 ms, 14 bits
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);
  ledcSetup(CANAL, 50, 14);
  ledcAttachPin(PINO_SERVO, CANAL);
  angulo(90);
  delay(1000);
}

void loop() {
  // Passo a passo (mais suave e puxa menos corrente que saltar direto)
  for (int g = 10; g <= 170; g += 2) { angulo(g); delay(15); }
  Serial.println("[TESTE] 170 graus");
  delay(700);
  for (int g = 170; g >= 10; g -= 2) { angulo(g); delay(15); }
  Serial.println("[TESTE] 10 graus");
  delay(700);
}
