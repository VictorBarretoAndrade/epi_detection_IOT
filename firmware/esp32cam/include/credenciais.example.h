// Copie este arquivo para "credenciais.h" (mesma pasta) e preencha.
// O credenciais.h fica fora do Git (.gitignore).
#pragma once

// Rede Wi-Fi 2,4 GHz (o ESP32 nao conecta em 5 GHz).
#define WIFI_SSID  "NOME_DA_REDE"
#define WIFI_SENHA "SENHA_DA_REDE"

// Opcional: senha para gravar pelo Wi-Fi (ambiente esp32cam_ota). Sem ela, qualquer um
// na mesma rede consegue regravar a camera.
// #define OTA_SENHA "troque-esta-senha"

// Opcional: IP fixo, para nao precisar mudar o config.toml do PC a cada boot.
// Use um IP livre da sua rede. Deixe comentado para usar DHCP.
// #define IP_FIXO   192, 168, 0, 50
// #define GATEWAY   192, 168, 0, 1
// #define MASCARA   255, 255, 255, 0
