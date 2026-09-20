"""Orquestra coleta + resumo + renderização da edição diária."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import db
from .collector import coletar
from .summarizer import resumir_tema
from .youtube_transcript import enriquecer_itens as enriquecer_com_transcricoes

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _renderizar(data_str: str, blocos: list[dict]) -> str:
    tpl = _env.get_template("edicao.html")
    return tpl.render(data=data_str, blocos=blocos)


async def gerar_edicao_async() -> int:
    """Executa o pipeline completo e salva a edição. Retorna o id."""
    temas = db.temas_ativos_com_feeds()
    if not temas:
        log.warning("Nenhum tema ativo — nada a gerar.")
        return 0

    log.info("Coletando RSS de %d temas...", len(temas))
    itens = await coletar(temas)
    log.info("Coletados %d itens brutos.", len(itens))

    # Enriquece itens de YouTube com transcrição (quando disponível) — dá material rico à IA
    await enriquecer_com_transcricoes(itens)

    # Deduplica por link e classifica: novo / hoje / ontem (< 48h e já visto) / antigo (exclui)
    por_link = {}
    for it in itens:
        por_link.setdefault(it.link, it)
    status = db.classificar_vistos(por_link.keys())
    itens_novos = [it for link, it in por_link.items() if status[link] == "novo"]
    itens_rep_hoje = [it for link, it in por_link.items() if status[link] == "hoje"]
    itens_rep_ontem = [it for link, it in por_link.items() if status[link] == "ontem"]
    log.info(
        "%d novos, %d de hoje, %d de ontem (reexibidos), %d antigos (excluídos).",
        len(itens_novos), len(itens_rep_hoje), len(itens_rep_ontem),
        len(por_link) - len(itens_novos) - len(itens_rep_hoje) - len(itens_rep_ontem),
    )

    # Agrupa por tema
    por_tema: dict[str, list] = {}
    for it in itens_novos:
        por_tema.setdefault(it.tema, []).append(it)
    por_tema_hoje: dict[str, list] = {}
    for it in itens_rep_hoje:
        por_tema_hoje.setdefault(it.tema, []).append(it)
    por_tema_ontem: dict[str, list] = {}
    for it in itens_rep_ontem:
        por_tema_ontem.setdefault(it.tema, []).append(it)

    # Reaproveita título/resumo já exibidos antes (sem custo de IA)
    links_reexibir = [it.link for it in itens_rep_hoje] + [it.link for it in itens_rep_ontem]
    exibidos_antes = db.itens_por_links(links_reexibir)

    def _ordena(bucket):
        bucket.sort(key=lambda x: (x.publicado is None, -(x.publicado.timestamp() if x.publicado else 0)))
        return bucket

    def _fmt_reexibido(it, tipo_label: int):
        """tipo_label: 1 = 'edição anterior de hoje', 2 = 'de ontem'."""
        antigo = exibidos_antes.get(it.link)
        return {
            "titulo": antigo["titulo"] if antigo else it.titulo,
            "resumo": antigo["resumo"] if antigo else (it.resumo_original or "")[:400],
            "link": it.link,
            "fonte": (antigo.get("fonte") if antigo else "") or it.fonte,
            "repetida": tipo_label,
        }

    blocos = []
    for tema in temas.keys():  # respeita ordem dos temas ativos
        bucket = _ordena(por_tema.get(tema, []))
        resumidos = resumir_tema(tema, bucket) if bucket else []
        for r in resumidos:
            r["repetida"] = 0

        hoje_fmt = [_fmt_reexibido(it, 1) for it in _ordena(por_tema_hoje.get(tema, []))]
        ontem_fmt = [_fmt_reexibido(it, 2) for it in _ordena(por_tema_ontem.get(tema, []))]

        itens_bloco = resumidos + hoje_fmt + ontem_fmt
        blocos.append({"tema": tema, "itens": itens_bloco, "vazio": not itens_bloco})

    data_str = datetime.now().strftime("%d/%m/%Y")
    # HTML salvo só como snapshot/backup; rendering real é dinâmico a partir de `itens`
    html = _renderizar(data_str, blocos)
    edicao_id = db.salvar_edicao(data_str, html)
    db.salvar_itens(edicao_id, [b for b in blocos if not b.get("vazio")])
    # Marca como visto SÓ o que foi exibido — o que a IA não selecionou continua elegível
    exibidos = [item["link"] for b in blocos for item in b["itens"]]
    db.marcar_vistos(exibidos)
    log.info("Edição #%d salva.", edicao_id)
    return edicao_id


def gerar_edicao_sync() -> int:
    return asyncio.run(gerar_edicao_async())


async def buscar_extra_tema(tema_id: int, janela_horas: int = 168) -> int:
    """Busca focada em UM tema, ignora filtro de 'já vistos', janela padrão 7 dias.
    Cria uma edição nova só com esse tema. Retorna o id da edição."""
    tema = db.obter_tema(tema_id)
    if not tema or not tema["feeds"]:
        log.warning("Tema %s não tem feeds.", tema_id)
        return 0

    log.info("Busca extra no tema '%s' (janela %dh)...", tema["nome"], janela_horas)
    itens = await coletar({tema["nome"]: tema["feeds"]}, janela_horas=janela_horas)
    log.info("Busca extra coletou %d itens.", len(itens))

    if not itens:
        return 0

    # Deduplica por link, MAS não filtra vistos (queremos revisitar)
    por_link = {}
    for it in itens:
        por_link.setdefault(it.link, it)
    bucket = list(por_link.values())
    bucket.sort(key=lambda x: (x.publicado is None, -(x.publicado.timestamp() if x.publicado else 0)))

    resumidos = resumir_tema(tema["nome"], bucket)
    # Etiqueta o que já apareceu em edição de hoje
    status = db.classificar_vistos([r["link"] for r in resumidos])
    for r in resumidos:
        r["repetida"] = 1 if status.get(r["link"]) == "hoje" else 0
    blocos = [{"tema": tema["nome"], "itens": resumidos, "vazio": not resumidos}]

    data_str = f"{datetime.now().strftime('%d/%m/%Y')} · Busca extra: {tema['nome']}"
    html = _renderizar(data_str, blocos)
    edicao_id = db.salvar_edicao(data_str, html)
    db.salvar_itens(edicao_id, [b for b in blocos if not b["vazio"]])
    # Marca como visto SÓ o que foi exibido — o resto continua elegível na rodada diária
    db.marcar_vistos([r["link"] for r in resumidos])
    log.info("Busca extra salva como edição #%d", edicao_id)
    return edicao_id


async def gerar_edicao_periodo(
    desde: datetime,
    ate: datetime,
    rotulo: str,
    tema_id: int | None = None,
    marcar_vistos: bool = False,
) -> tuple[int, str]:
    """Monta uma edição com itens publicados entre `desde` e `ate` (UTC).

    Ignora o filtro de 'já vistos' — a ideia é justamente revisitar o passado.
    Retorna (edicao_id, mensagem). edicao_id = 0 quando não achou nada."""
    if tema_id:
        tema = db.obter_tema(tema_id)
        if not tema or not tema["feeds"]:
            return 0, "Esse tema não tem fontes cadastradas."
        temas = {tema["nome"]: tema["feeds"]}
    else:
        temas = db.temas_ativos_com_feeds()
        if not temas:
            return 0, "Nenhum tema ativo com fontes."

    log.info("Edição de período %s → %s (%d temas)", desde.date(), ate.date(), len(temas))
    itens = await coletar(temas, desde=desde, ate=ate)
    log.info("Período coletou %d itens brutos.", len(itens))

    if not itens:
        return 0, (
            "Nenhum item encontrado nesse período. Os feeds RSS só servem os itens "
            "mais recentes — quanto mais antiga a data, menor a chance de ainda estar lá."
        )

    await enriquecer_com_transcricoes(itens)

    por_link = {}
    for it in itens:
        por_link.setdefault(it.link, it)

    por_tema: dict[str, list] = {}
    for it in por_link.values():
        por_tema.setdefault(it.tema, []).append(it)

    # Etiqueta o que já apareceu em alguma edição, pra você saber o que é revisita
    status = db.classificar_vistos(por_link.keys())

    blocos = []
    for tema in temas.keys():
        bucket = por_tema.get(tema, [])
        if not bucket:
            blocos.append({"tema": tema, "itens": [], "vazio": True})
            continue
        bucket.sort(key=lambda x: (x.publicado is None, -(x.publicado.timestamp() if x.publicado else 0)))
        resumidos = resumir_tema(tema, bucket)
        for r in resumidos:
            r["repetida"] = 0 if status.get(r["link"]) == "novo" else 2
        blocos.append({"tema": tema, "itens": resumidos, "vazio": not resumidos})

    total = sum(len(b["itens"]) for b in blocos)
    if total == 0:
        return 0, "A IA não selecionou nenhum item relevante nesse período."

    data_str = f"{datetime.now().strftime('%d/%m/%Y')} · 📅 {rotulo}"
    html = _renderizar(data_str, blocos)
    edicao_id = db.salvar_edicao(data_str, html)
    db.salvar_itens(edicao_id, [b for b in blocos if not b["vazio"]])
    if marcar_vistos:
        db.marcar_vistos([item["link"] for b in blocos for item in b["itens"]])
    log.info("Edição de período salva como #%d (%d itens)", edicao_id, total)
    return edicao_id, ""
