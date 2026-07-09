"""FastAPI: interface web + API."""
from __future__ import annotations
import html as html_mod
import logging
from contextlib import asynccontextmanager
from pathlib import Path
import markdown as md
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

load_dotenv()

from . import db
from .generator import gerar_edicao_async, buscar_extra_tema
from .scheduler import iniciar_scheduler
from .summarizer import gerar_roteiro, gerar_roteiro_livre
from .youtube import resolver_canal

CORES_VALIDAS = {"", "vermelho", "laranja", "amarelo", "verde", "azul", "roxo"}


def _render_md(texto: str) -> str:
    """Roteiro (texto do modelo) -> HTML: tabelas Markdown viram <table>, quebras viram <br>.
    Escapa o texto antes, então nenhum HTML vindo do modelo é executado."""
    return md.markdown(html_mod.escape(texto), extensions=["tables", "nl2br"])

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)

BASE = Path(__file__).parent
CONFIG_YAML = BASE.parent / "config" / "feeds.yaml"
templates = Jinja2Templates(directory=str(BASE / "templates"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if CONFIG_YAML.exists():
        with open(CONFIG_YAML, "r", encoding="utf-8") as f:
            db.seed_from_yaml(yaml.safe_load(f) or {})
    scheduler = iniciar_scheduler()
    app.state.scheduler = scheduler
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Newsletter Central", lifespan=lifespan)


def _render_edicao(request: Request, edicao: dict | None, salvo_estilo: bool = False):
    edicoes = db.listar_edicoes(limit=15)
    blocos = db.itens_da_edicao(edicao["id"]) if edicao else []
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "edicao": edicao,
            "edicoes": edicoes,
            "blocos": blocos,
            "tem_itens": bool(blocos),
            "prompt_roteiro": db.get_config("prompt_base_roteiro", ""),
            "salvo_estilo": salvo_estilo,
        },
    )


@app.get("/", response_class=HTMLResponse)
def home(request: Request, salvo_estilo: int = 0):
    return _render_edicao(request, db.ultima_edicao(), salvo_estilo=bool(salvo_estilo))


@app.get("/edicao/{edicao_id}", response_class=HTMLResponse)
def ver_edicao(request: Request, edicao_id: int, salvo_estilo: int = 0):
    edicao = db.obter_edicao(edicao_id)
    if not edicao:
        raise HTTPException(404)
    return _render_edicao(request, edicao, salvo_estilo=bool(salvo_estilo))


@app.post("/estilo-lateral")
def salvar_estilo_lateral(prompt_roteiro: str = Form(""), volta: str = Form("/")):
    db.set_config("prompt_base_roteiro", prompt_roteiro.strip())
    sep = "&" if "?" in volta else "?"
    return RedirectResponse(f"{volta}{sep}salvo_estilo=1", status_code=303)


@app.post("/itens/{item_id}/cor")
def definir_cor(item_id: int, cor: str = Form(...)):
    if cor not in CORES_VALIDAS:
        raise HTTPException(400, "Cor inválida")
    db.definir_cor_item(item_id, cor)
    item = db.obter_item(item_id)
    return RedirectResponse(f"/edicao/{item['edicao_id']}#item-{item_id}", status_code=303)


@app.post("/edicao/{edicao_id}/tema-cor")
def definir_cor_tema(edicao_id: int, tema: str = Form(...), cor: str = Form(...)):
    if cor not in CORES_VALIDAS:
        raise HTTPException(400, "Cor inválida")
    db.definir_cor_tema_edicao(edicao_id, tema, cor)
    return RedirectResponse(f"/edicao/{edicao_id}", status_code=303)


@app.post("/itens/{item_id}/roteiro")
def gerar_roteiro_item(item_id: int):
    item = db.obter_item(item_id)
    if not item:
        raise HTTPException(404)
    texto = gerar_roteiro(item["titulo"], item["resumo"], item.get("fonte", ""))
    db.salvar_roteiro(item_id, texto)
    return RedirectResponse(f"/itens/{item_id}/roteiro", status_code=303)


@app.post("/roteiros/{roteiro_id}/usado")
def toggle_usado_roteiro(roteiro_id: int, usado: int = Form(1), volta: str = Form("/biblioteca")):
    db.marcar_usado_roteiro(roteiro_id, bool(usado))
    return RedirectResponse(volta, status_code=303)


@app.get("/itens/{item_id}/roteiro", response_class=HTMLResponse)
def ver_roteiro(request: Request, item_id: int):
    item = db.obter_item(item_id)
    if not item:
        raise HTTPException(404)
    roteiro = db.obter_roteiro_mais_recente(item_id)
    return templates.TemplateResponse(
        "roteiro.html",
        {
            "request": request,
            "item": item,
            "roteiro": roteiro,
            "texto_html": _render_md(roteiro["texto"]) if roteiro else "",
        },
    )


@app.get("/temas", response_class=HTMLResponse)
def pagina_temas(request: Request, erro: str = ""):
    return templates.TemplateResponse(
        "temas.html",
        {"request": request, "temas": db.listar_temas(), "erro": erro},
    )


@app.post("/temas/novo")
def criar_tema(nome: str = Form(...)):
    if nome.strip():
        db.adicionar_tema(nome)
    return RedirectResponse("/temas", status_code=303)


@app.post("/temas/{tema_id}/toggle")
def toggle(tema_id: int):
    db.toggle_tema(tema_id)
    return RedirectResponse("/temas", status_code=303)


@app.post("/temas/{tema_id}/remover")
def remover(tema_id: int):
    db.remover_tema(tema_id)
    return RedirectResponse("/temas", status_code=303)


@app.post("/temas/{tema_id}/feeds/novo")
def add_feed(tema_id: int, url: str = Form(...)):
    if url.strip():
        db.adicionar_feed(tema_id, url)
    return RedirectResponse("/temas", status_code=303)


@app.post("/feeds/{feed_id}/remover")
def del_feed(feed_id: int):
    db.remover_feed(feed_id)
    return RedirectResponse("/temas", status_code=303)


@app.post("/temas/{tema_id}/feeds/novo-youtube")
def add_feed_youtube(tema_id: int, entrada: str = Form(...)):
    rss_url, nome = resolver_canal(entrada)
    if not rss_url:
        # Sem JS no frontend, mostro erro via query string simples
        return RedirectResponse(f"/temas?erro=Canal+nao+encontrado:+{entrada}", status_code=303)
    db.adicionar_feed(tema_id, rss_url)
    return RedirectResponse("/temas", status_code=303)


@app.post("/gerar-agora")
async def gerar_agora():
    edicao_id = await gerar_edicao_async()
    if edicao_id:
        return RedirectResponse(f"/edicao/{edicao_id}", status_code=303)
    return RedirectResponse("/", status_code=303)


@app.post("/temas/{tema_id}/buscar")
async def buscar_tema(tema_id: int, janela: int = Form(168)):
    edicao_id = await buscar_extra_tema(tema_id, janela_horas=max(1, min(janela, 720)))
    if edicao_id:
        return RedirectResponse(f"/edicao/{edicao_id}", status_code=303)
    return RedirectResponse("/temas", status_code=303)


# ---------- Roteiros livres ----------

@app.get("/roteiros-livres", response_class=HTMLResponse)
def roteiros_livres_lista(request: Request, tema: str = ""):
    temas = [t["nome"] for t in db.listar_temas() if t["ativo"]]
    roteiros = db.listar_roteiros_livres()
    return templates.TemplateResponse(
        "roteiros_livres.html",
        {"request": request, "temas": temas, "roteiros": roteiros, "tema_preselect": tema},
    )


@app.post("/roteiros-livres/novo")
def roteiro_livre_novo(
    tema: str = Form(""),
    titulo: str = Form(...),
    contexto: str = Form(""),
    permitir_repetir: str = Form(""),
):
    if not titulo.strip():
        raise HTTPException(400, "Título obrigatório")
    tema_lim = tema.strip() or None
    evitar = []
    if tema_lim and not permitir_repetir:
        evitar = db.usados_recentes_por_tema(tema_lim)
    texto = gerar_roteiro_livre(tema_lim, titulo.strip(), contexto.strip() or None, evitar=evitar)
    rid = db.criar_roteiro_livre(tema_lim, titulo, contexto, texto)
    return RedirectResponse(f"/roteiros-livres/{rid}", status_code=303)


@app.get("/roteiros-livres/{rid}", response_class=HTMLResponse)
def roteiro_livre_ver(request: Request, rid: int):
    r = db.obter_roteiro_livre(rid)
    if not r:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "roteiro_livre.html",
        {"request": request, "r": r, "texto_html": _render_md(r["texto"])},
    )


@app.post("/roteiros-livres/{rid}/regerar")
def roteiro_livre_regerar(rid: int):
    r = db.obter_roteiro_livre(rid)
    if not r:
        raise HTTPException(404)
    texto = gerar_roteiro_livre(r.get("tema"), r["titulo"], r.get("contexto"))
    db.regerar_roteiro_livre(rid, texto)
    return RedirectResponse(f"/roteiros-livres/{rid}", status_code=303)


@app.post("/roteiros-livres/{rid}/cor")
def roteiro_livre_cor(rid: int, cor: str = Form(...)):
    if cor not in CORES_VALIDAS:
        raise HTTPException(400, "Cor inválida")
    db.definir_cor_roteiro_livre(rid, cor)
    return RedirectResponse("/roteiros-livres", status_code=303)


@app.post("/roteiros-livres/{rid}/remover")
def roteiro_livre_remover(rid: int):
    db.remover_roteiro_livre(rid)
    return RedirectResponse("/roteiros-livres", status_code=303)


@app.post("/roteiros-livres/{rid}/usado")
def toggle_usado_livre(rid: int, usado: int = Form(1), volta: str = Form("/biblioteca")):
    db.marcar_usado_livre(rid, bool(usado))
    return RedirectResponse(volta, status_code=303)


# ---------- Configuração / Prompt-base ----------

@app.get("/config", response_class=HTMLResponse)
def pagina_config(request: Request, salvo: int = 0):
    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "prompt_base": db.get_config("prompt_base_roteiro", ""),
            "salvo": bool(salvo),
        },
    )


@app.post("/config")
def salvar_config(prompt_base: str = Form("")):
    db.set_config("prompt_base_roteiro", prompt_base.strip())
    return RedirectResponse("/config?salvo=1", status_code=303)


# ---------- Biblioteca unificada ----------

@app.get("/biblioteca", response_class=HTMLResponse)
def biblioteca(request: Request, tema: str = "", usado: str = "todos"):
    temas = [t["nome"] for t in db.listar_temas()]
    if usado not in ("todos", "usados", "nao_usados"):
        usado = "todos"
    roteiros = db.biblioteca_roteiros(filtro_tema=tema or None, filtro_usado=usado)
    return templates.TemplateResponse(
        "biblioteca.html",
        {
            "request": request,
            "temas": temas,
            "roteiros": roteiros,
            "filtro_tema": tema,
            "filtro_usado": usado,
        },
    )
