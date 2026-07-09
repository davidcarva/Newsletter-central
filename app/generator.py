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

    # Deduplica por link e classifica: novo / já visto HOJE (reexibe com label) / antigo (exclui)
    por_link = {}
    for it in itens:
        por_link.setdefault(it.link, it)
    status = db.classificar_vistos(por_link.keys())
    itens_novos = [it for link, it in por_link.items() if status[link] == "novo"]
    itens_repetidos = [it for link, it in por_link.items() if status[link] == "hoje"]
    log.info(
        "%d novos, %d repetidos de hoje, %d antigos (excluídos).",
        len(itens_novos), len(itens_repetidos),
        len(por_link) - len(itens_novos) - len(itens_repetidos),
    )

    # Agrupa por tema
    por_tema: dict[str, list] = {}
    for it in itens_novos:
        por_tema.setdefault(it.tema, []).append(it)
    por_tema_rep: dict[str, list] = {}
    for it in itens_repetidos:
        por_tema_rep.setdefault(it.tema, []).append(it)

    # Reaproveita título/resumo já exibidos hoje (sem custo de IA)
    exibidos_antes = db.itens_por_links([it.link for it in itens_repetidos])

    def _ordena(bucket):
        bucket.sort(key=lambda x: (x.publicado is None, -(x.publicado.timestamp() if x.publicado else 0)))
        return bucket

    blocos = []
    for tema in temas.keys():  # respeita ordem dos temas ativos
        bucket = _ordena(por_tema.get(tema, []))
        resumidos = resumir_tema(tema, bucket) if bucket else []
        for r in resumidos:
            r["repetida"] = 0

        repetidos_fmt = []
        for it in _ordena(por_tema_rep.get(tema, [])):
            antigo = exibidos_antes.get(it.link)
            repetidos_fmt.append({
                "titulo": antigo["titulo"] if antigo else it.titulo,
                "resumo": antigo["resumo"] if antigo else (it.resumo_original or "")[:400],
                "link": it.link,
                "fonte": (antigo.get("fonte") if antigo else "") or it.fonte,
                "repetida": 1,
            })

        itens_bloco = resumidos + repetidos_fmt
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
