"""Resolve URLs de canais do YouTube em URLs de RSS.

YouTube expõe RSS público por canal em:
  https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxx

Esse módulo aceita várias formas que o usuário pode colar:
- URL de RSS pronta -> retorna como veio
- channel_id puro (UC...) -> monta a URL
- /channel/UC... -> extrai e monta
- /@handle ou youtube.com/@handle -> baixa a página e extrai o channelId
- nome solto -> tenta como /@nome
"""
from __future__ import annotations
import re
import logging
import httpx

log = logging.getLogger(__name__)

UC_ID_RE = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")
URL_CHANNEL_RE = re.compile(r"/channel/(UC[a-zA-Z0-9_-]{22})")
HTML_CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId)":"(UC[a-zA-Z0-9_-]{22})"')


def _feed_url(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def resolver_canal(entrada: str) -> tuple[str | None, str | None]:
    """Tenta converter `entrada` em (rss_url, nome_canal).
    Retorna (None, None) se não conseguir."""
    entrada = (entrada or "").strip()
    if not entrada:
        return None, None

    # Já é RSS pronta
    if "feeds/videos.xml" in entrada and "channel_id=" in entrada:
        return entrada, None

    # channel_id puro
    if UC_ID_RE.match(entrada):
        return _feed_url(entrada), None

    # URL /channel/UC...
    m = URL_CHANNEL_RE.search(entrada)
    if m:
        return _feed_url(m.group(1)), None

    # Normaliza handle solto pra URL completa
    if not entrada.startswith("http"):
        if entrada.startswith("@"):
            url = f"https://www.youtube.com/{entrada}"
        else:
            url = f"https://www.youtube.com/@{entrada}"
    else:
        url = entrada

    # Baixa HTML do canal e extrai channelId
    try:
        r = httpx.get(
            url, timeout=10.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (NewsletterCentral)"},
        )
        if r.status_code != 200:
            log.warning("Canal YouTube %s status %s", url, r.status_code)
            return None, None
        html = r.text
        m = HTML_CHANNEL_ID_RE.search(html)
        if not m:
            return None, None
        nome_m = re.search(r'"channelMetadataRenderer":\{"title":"([^"]+)"', html)
        nome = nome_m.group(1) if nome_m else None
        return _feed_url(m.group(1)), nome
    except Exception as e:
        log.warning("Falha ao resolver canal YouTube %s: %s", url, e)
        return None, None
