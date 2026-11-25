// Copie este arquivo para "credenciais.h" (mesma pasta) e preencha.
// O credenciais.h fica fora do Git (.gitignore).
#pragma once

// Rede Wi-Fi 2,4 GHz (a mesma do PC e da ESP32-CAM).
#define WIFI_SSID  "NOME_DA_REDE"
#define WIFI_SENHA "SENHA_DA_REDE"

// Opcional: IP fixo. Deixe comentado para usar DHCP.
// #define IP_FIXO   192, 168, 0, 51
// #define GATEWAY   192, 168, 0, 1
// #define MASCARA   255, 255, 255, 0

// Opcional: se os seus LEDs acendem com o pino em NIVEL BAIXO
// (anodo no 3V3, catodo no pino), troque para 0.
// #define LED_ATIVO_ALTO 1
