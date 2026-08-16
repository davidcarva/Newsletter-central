"""Busca transcrições (legendas automáticas) de vídeos do YouTube.

Usa `youtube-transcript-api` — não faz login, não usa API oficial.
Cobre a grande maioria dos vídeos públicos (que têm legenda auto ou manual)."""
from __future__ import annotations
import asyncio
import logging
import re
from typing import Iterable

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _API = YouTubeTranscriptApi()
    _DISPONIVEL = True
except Exception:  # pragma: no cover
    _API = None
    _DISPONIVEL = False

log = logging.getLogger(__name__)

VIDEO_ID_RE = re.compile(
    r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/|live/))([A-Za-z0-9_-]{11})"
)
LANGS_PADRAO = ["pt", "pt-BR", "pt-PT", "en", "en-US", "en-GB"]


def extrair_video_id(url: str) -> str | None:
    if not url:
        return None
    m = VIDEO_ID_RE.search(url)
    return m.group(1) if m else None


def _buscar_sync(video_id: str, max_chars: int) -> str | None:
    if not _DISPONIVEL:
        return None
    try:
        fetched = _API.fetch(video_id, languages=LANGS_PADRAO)
    except Exception as e:
        log.debug("Sem transcrição para %s: %s", video_id, type(e).__name__)
        return None
    partes = []
    for snip in fetched:
        # snip pode ser FetchedTranscriptSnippet (com .text) ou dict legado
        texto = getattr(snip, "text", None) or (snip.get("text") if isinstance(snip, dict) else None)
        if texto:
            partes.append(texto.strip())
    texto = re.sub(r"\s+", " ", " ".join(partes)).strip()
    return texto[:max_chars] if texto else None


async def buscar_transcricao(url_ou_id: str, max_chars: int = 6000) -> str | None:
    """Retorna a transcrição truncada, ou None se indisponível/erro."""
    if not _DISPONIVEL:
        return None
    video_id = url_ou_id if len(url_ou_id) == 11 else extrair_video_id(url_ou_id)
    if not video_id:
        return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _buscar_sync, video_id, max_chars)


async def enriquecer_itens(itens: Iterable, max_chars: int = 6000) -> int:
    """Para cada item de YouTube, tenta baixar a transcrição e mesclar em `resumo_original`.
    Muta os items in-place. Retorna quantos foram enriquecidos."""
    if not _DISPONIVEL:
        return 0
    yt_itens = [it for it in itens if extrair_video_id(getattr(it, "link", ""))]
    if not yt_itens:
        return 0
    log.info("Buscando transcrição de %d vídeo(s) do YouTube...", len(yt_itens))
    tasks = [buscar_transcricao(it.link, max_chars) for it in yt_itens]
    resultados = await asyncio.gather(*tasks, return_exceptions=True)
    enriquecidos = 0
    for it, transcricao in zip(yt_itens, resultados):
        if isinstance(transcricao, str) and transcricao:
            desc_curta = (it.resumo_original or "")[:300]
            it.resumo_original = (
                f"{desc_curta}\n\n[TRANSCRIÇÃO DO VÍDEO — use como fonte principal:]\n"
                f"{transcricao}"
            )
            enriquecidos += 1
    log.info("Transcrições obtidas: %d/%d", enriquecidos, len(yt_itens))
    return enriquecidos
