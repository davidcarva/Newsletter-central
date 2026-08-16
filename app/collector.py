"""Coleta de notícias via RSS. Nenhuma busca aberta na web."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import feedparser
import httpx

log = logging.getLogger(__name__)

JANELA_HORAS = 48  # janela padrão — 48h pra pegar também vídeos de ontem
TIMEOUT = 15.0


@dataclass
class Item:
    tema: str
    titulo: str
    link: str
    resumo_original: str
    fonte: str
    publicado: datetime | None


async def _baixar(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url, timeout=TIMEOUT, follow_redirects=True,
                             headers={"User-Agent": "NewsletterCentral/1.0"})
        if r.status_code == 200:
            return r.text
        log.warning("Feed %s retornou status %s", url, r.status_code)
    except Exception as e:
        log.warning("Falha ao baixar %s: %s", url, e)
    return None


def _parse_data(entry) -> datetime | None:
    for campo in ("published_parsed", "updated_parsed"):
        val = getattr(entry, campo, None)
        if val:
            try:
                return datetime(*val[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    return None


def _extrair_fonte(feed) -> str:
    titulo = getattr(feed.feed, "title", None) if hasattr(feed, "feed") else None
    return titulo or "Fonte desconhecida"


async def coletar(temas_e_feeds: dict[str, list[str]], janela_horas: int = JANELA_HORAS) -> list[Item]:
    """Recebe {tema: [urls]} e devolve lista de Item recentes."""
    limite = datetime.now(timezone.utc) - timedelta(hours=janela_horas)
    itens: list[Item] = []

    async with httpx.AsyncClient() as client:
        tarefas = []
        mapeamento = []  # (tema, url) na mesma ordem
        for tema, urls in temas_e_feeds.items():
            for url in urls:
                tarefas.append(_baixar(client, url))
                mapeamento.append((tema, url))

        resultados = await asyncio.gather(*tarefas, return_exceptions=True)

    for (tema, url), conteudo in zip(mapeamento, resultados):
        if not conteudo or isinstance(conteudo, Exception):
            continue
        feed = feedparser.parse(conteudo)
        fonte = _extrair_fonte(feed)
        for entry in feed.entries[:20]:
            link = getattr(entry, "link", None)
            titulo = getattr(entry, "title", None)
            if not link or not titulo:
                continue
            publicado = _parse_data(entry)
            if publicado and publicado < limite:
                continue
            resumo = getattr(entry, "summary", "") or getattr(entry, "description", "")
            itens.append(
                Item(
                    tema=tema,
                    titulo=titulo.strip(),
                    link=link.strip(),
                    resumo_original=resumo.strip()[:1500],
                    fonte=fonte,
                    publicado=publicado,
                )
            )
    return itens
