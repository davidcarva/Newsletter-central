"""Busca metadados de posts sociais (Instagram, YouTube, sites em geral).

⚠️ Instagram bloqueia scraping. Este módulo tenta apenas os metadados OpenGraph
públicos (que às vezes vazam no HTML mesmo com login wall). Não faz login,
não força, não retenta agressivamente. Se falhar, devolve motivo pro usuário
complementar manualmente."""
from __future__ import annotations
import html
import re
import logging
import httpx

log = logging.getLogger(__name__)

IG_URL_RE = re.compile(r"instagram\.com/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)")
YT_URL_RE = re.compile(r"(?:youtu\.be/|youtube\.com/(?:watch\?v=|shorts/|embed/))([A-Za-z0-9_-]{11})")
OG_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']og:(\w+)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TWITTER_META_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\']twitter:(\w+)["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
TITLE_RE = re.compile(r"<title[^>]*>([^<]+)</title>", re.IGNORECASE)

UA = "Mozilla/5.0 (compatible; NewsletterCentral/1.0; +https://github.com/davidcarva/Newsletter-central)"


def _tipo(url: str) -> str:
    if IG_URL_RE.search(url):
        return "instagram"
    if YT_URL_RE.search(url):
        return "youtube"
    return "generico"


def _fetch_html(url: str) -> tuple[str, int]:
    with httpx.Client(follow_redirects=True, timeout=12.0,
                      headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}) as c:
        r = c.get(url)
        return r.text, r.status_code


def buscar_metadados(url: str) -> dict:
    """Retorna {ok, tipo, titulo, descricao, autor, imagem, url, erro?}.
    Sempre inclui `tipo` mesmo em erro."""
    url = (url or "").strip()
    if not url:
        return {"ok": False, "tipo": "vazio", "erro": "URL vazia."}
    tipo = _tipo(url)
    if tipo == "instagram" and not IG_URL_RE.search(url):
        return {"ok": False, "tipo": tipo, "erro": "URL do Instagram inválida."}

    try:
        html_texto, status = _fetch_html(url)
    except Exception as e:
        return {"ok": False, "tipo": tipo, "erro": f"Falha ao acessar a página: {e}"}

    if status != 200:
        return {"ok": False, "tipo": tipo, "erro": f"Servidor respondeu status {status}."}

    og = {}
    for m in OG_META_RE.finditer(html_texto):
        og[m.group(1).lower()] = html.unescape(m.group(2))
    tw = {}
    for m in TWITTER_META_RE.finditer(html_texto):
        tw[m.group(1).lower()] = html.unescape(m.group(2))

    titulo = og.get("title") or tw.get("title") or ""
    descricao = og.get("description") or tw.get("description") or ""
    imagem = og.get("image") or tw.get("image") or ""
    autor = og.get("username") or ""
    if not titulo:
        m = TITLE_RE.search(html_texto)
        if m:
            titulo = html.unescape(m.group(1)).strip()

    if not (titulo or descricao):
        motivo = {
            "instagram": "Instagram provavelmente serviu login wall — não expôs metadados. "
                         "Cole a legenda manualmente abaixo pra IA conseguir analisar.",
            "youtube": "Não achei metadados no YouTube. Tente colar título/descrição do vídeo.",
            "generico": "A página não expôs metadados OpenGraph legíveis.",
        }[tipo]
        return {"ok": False, "tipo": tipo, "erro": motivo}

    return {
        "ok": True,
        "tipo": tipo,
        "titulo": titulo.strip(),
        "descricao": descricao.strip(),
        "autor": autor.strip(),
        "imagem": imagem.strip(),
        "url": url,
    }
