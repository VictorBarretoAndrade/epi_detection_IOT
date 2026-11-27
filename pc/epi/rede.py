"""Resolve nomes mDNS (epi-cam.local) uma vez e guarda o IP.

No Windows cada consulta .local leva ~0,2-0,5 s; resolver a cada foto deixaria tudo lento.
Usar o nome em vez do IP evita editar o config.toml quando o hotspot muda de rede.
"""
from __future__ import annotations

import logging
import socket
from urllib.parse import urlsplit, urlunsplit

log = logging.getLogger(__name__)


class UrlResolvida:
    def __init__(self, url: str):
        self.original = url
        self.atual = url
        self.resolver()

    def resolver(self) -> str:
        partes = urlsplit(self.original)
        host = partes.hostname or ""
        if not host.endswith(".local"):
            return self.atual
        try:
            ip = socket.getaddrinfo(host, partes.port or 80, socket.AF_INET)[0][4][0]
        except OSError:
            log.warning("Nao encontrei %s na rede (mDNS). A placa esta ligada e no mesmo Wi-Fi?", host)
            return self.atual
        netloc = ip if partes.port is None else f"{ip}:{partes.port}"
        novo = urlunsplit(partes._replace(netloc=netloc))
        if novo != self.atual:
            log.info("%s -> %s", host, ip)
        self.atual = novo
        return novo
