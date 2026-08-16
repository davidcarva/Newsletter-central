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

from urllib.parse import quote

from . import db, diretor
from .generator import gerar_edicao_async, buscar_extra_tema
from .scheduler import iniciar_scheduler, reagendar, proximas_execucoes
from .summarizer import gerar_roteiro, gerar_roteiro_livre, analisar_post_e_sugerir
from .youtube import resolver_canal
from .social import buscar_metadados

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
    presets = db.listar_presets()
    ativo = next((p for p in presets if p["ativo"]), None)
    return templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "edicao": edicao,
            "edicoes": edicoes,
            "blocos": blocos,
            "tem_itens": bool(blocos),
            "presets": presets,
            "preset_ativo": ativo,
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
def ver_roteiro(request: Request, item_id: int, enviado: str = "", cenas: int = 0, diretor_erro: str = ""):
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
            "diretor_ok": enviado == "ok",
            "diretor_cenas": cenas,
            "diretor_erro": diretor_erro,
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
def roteiro_livre_ver(request: Request, rid: int, enviado: str = "", cenas: int = 0, diretor_erro: str = ""):
    r = db.obter_roteiro_livre(rid)
    if not r:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "roteiro_livre.html",
        {
            "request": request, "r": r, "texto_html": _render_md(r["texto"]),
            "diretor_ok": enviado == "ok", "diretor_cenas": cenas, "diretor_erro": diretor_erro,
        },
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


# ---------- Ponte com o Diretor (app de cenas) ----------

@app.post("/itens/{item_id}/enviar-diretor")
def enviar_item_diretor(item_id: int):
    """Manda o roteiro da notícia pro Diretor, já decupado em cenas."""
    item = db.obter_item(item_id)
    roteiro = db.obter_roteiro_mais_recente(item_id) if item else None
    if not roteiro:
        raise HTTPException(404, "Gere o roteiro antes de enviar.")
    volta = f"/itens/{item_id}/roteiro"
    try:
        r = diretor.enviar_roteiro(roteiro["texto"], item["titulo"])
        return RedirectResponse(f"{volta}?enviado=ok&cenas={len(r['cenas'])}", status_code=303)
    except diretor.DiretorErro as e:
        return RedirectResponse(f"{volta}?diretor_erro={quote(str(e))}", status_code=303)
    except Exception as e:  # nunca deixa virar 500 na cara do usuario
        log.exception("Falha inesperada ao enviar pro Diretor")
        return RedirectResponse(f"{volta}?diretor_erro={quote(f'{type(e).__name__}: {e}')}", status_code=303)


@app.post("/roteiros-livres/{rid}/enviar-diretor")
def enviar_livre_diretor(rid: int):
    r = db.obter_roteiro_livre(rid)
    if not r:
        raise HTTPException(404)
    volta = f"/roteiros-livres/{rid}"
    try:
        env = diretor.enviar_roteiro(r["texto"], r.get("titulo") or "")
        return RedirectResponse(f"{volta}?enviado=ok&cenas={len(env['cenas'])}", status_code=303)
    except diretor.DiretorErro as e:
        return RedirectResponse(f"{volta}?diretor_erro={quote(str(e))}", status_code=303)
    except Exception as e:  # nunca deixa virar 500 na cara do usuario
        log.exception("Falha inesperada ao enviar pro Diretor")
        return RedirectResponse(f"{volta}?diretor_erro={quote(f'{type(e).__name__}: {e}')}", status_code=303)


# ---------- Presets de prompt ----------

@app.get("/config")
def config_redirect():
    # /config antigo virou /presets — mantém o link no menu funcionando
    return RedirectResponse("/presets", status_code=307)


@app.get("/presets", response_class=HTMLResponse)
def pagina_presets(request: Request, erro: str = "", editar: int | None = None, salvo: int = 0):
    presets = db.listar_presets()
    preset_editando = db.obter_preset(editar) if editar else None
    return templates.TemplateResponse(
        "presets.html",
        {
            "request": request,
            "presets": presets,
            "preset_editando": preset_editando,
            "erro": erro,
            "salvo": bool(salvo),
        },
    )


@app.post("/presets/novo")
def preset_novo(nome: str = Form(...), texto: str = Form("")):
    try:
        pid = db.criar_preset(nome, texto)
    except ValueError as e:
        return RedirectResponse(f"/presets?erro={e}", status_code=303)
    if pid is None:
        return RedirectResponse("/presets?erro=Já+existe+preset+com+esse+nome", status_code=303)
    # Se é o primeiro, já ativa
    if len(db.listar_presets()) == 1:
        db.ativar_preset(pid)
    return RedirectResponse("/presets?salvo=1", status_code=303)


@app.post("/presets/{pid}/atualizar")
def preset_atualizar(pid: int, nome: str = Form(...), texto: str = Form("")):
    try:
        ok = db.atualizar_preset(pid, nome, texto)
    except ValueError as e:
        return RedirectResponse(f"/presets?erro={e}&editar={pid}", status_code=303)
    if not ok:
        return RedirectResponse(f"/presets?erro=Nome+em+uso&editar={pid}", status_code=303)
    return RedirectResponse("/presets?salvo=1", status_code=303)


@app.post("/presets/{pid}/ativar")
def preset_ativar(pid: int, volta: str = Form("/presets")):
    db.ativar_preset(pid)
    return RedirectResponse(volta, status_code=303)


@app.post("/presets/ativar")
def preset_ativar_form(preset_id: int = Form(...), volta: str = Form("/")):
    """Endpoint usado pelo dropdown na home."""
    if preset_id == 0:
        db.desativar_presets()
    else:
        db.ativar_preset(preset_id)
    return RedirectResponse(volta, status_code=303)


@app.post("/presets/{pid}/remover")
def preset_remover(pid: int):
    db.remover_preset(pid)
    return RedirectResponse("/presets", status_code=303)


# ---------- Análise de post social ----------

@app.get("/analisar-post", response_class=HTMLResponse)
def pagina_analisar(request: Request, url: str = ""):
    meta = None
    if url:
        meta = buscar_metadados(url)
    return templates.TemplateResponse(
        "analisar_post.html",
        {"request": request, "url": url, "meta": meta},
    )


@app.post("/analisar-post/buscar")
def analisar_buscar(url: str = Form(...)):
    """Só faz o fetch e volta pra mesma página com preview."""
    return RedirectResponse(f"/analisar-post?url={url}", status_code=303)


@app.post("/analisar-post/gerar")
def analisar_gerar(
    url: str = Form(...),
    legenda: str = Form(""),
    observacoes: str = Form(""),
):
    meta = buscar_metadados(url)
    tipo = meta.get("tipo", "generico")
    titulo = meta.get("titulo", "") if meta.get("ok") else ""
    descricao = meta.get("descricao", "") if meta.get("ok") else ""
    autor = meta.get("autor", "") if meta.get("ok") else ""

    texto = analisar_post_e_sugerir(
        url=url,
        tipo=tipo,
        titulo_extraido=titulo,
        descricao_extraida=descricao,
        autor_extraido=autor,
        legenda_manual=legenda.strip() or None,
        observacoes=observacoes.strip() or None,
    )

    # Salva na biblioteca como roteiro livre, marcado com tema especial pra achar depois
    titulo_biblioteca = titulo or f"Análise: {url[:60]}"
    contexto_completo = (
        f"URL: {url}\n"
        f"Tipo: {tipo}\n"
        + (f"Legenda: {legenda.strip()}\n" if legenda.strip() else "")
        + (f"Observações: {observacoes.strip()}\n" if observacoes.strip() else "")
    )
    rid = db.criar_roteiro_livre(
        tema=f"🎨 Análise ({tipo})",
        titulo=titulo_biblioteca,
        contexto=contexto_completo,
        texto=texto,
    )
    return RedirectResponse(f"/roteiros-livres/{rid}", status_code=303)


# ---------- Agenda de execuções automáticas ----------

@app.get("/agenda", response_class=HTMLResponse)
def pagina_agenda(request: Request, erro: str = ""):
    ags = db.listar_agendamentos()
    proximas = proximas_execucoes(app.state.scheduler, limit=5)
    return templates.TemplateResponse(
        "agenda.html",
        {"request": request, "agendamentos": ags, "proximas": proximas, "erro": erro},
    )


@app.post("/agenda/novo")
def agenda_novo(horario: str = Form(...)):
    try:
        h, m = horario.strip().split(":")
        hora, minuto = int(h), int(m)
    except Exception:
        return RedirectResponse("/agenda?erro=Horário+inválido+(use+HH:MM)", status_code=303)
    try:
        novo_id = db.adicionar_agendamento(hora, minuto)
    except ValueError as e:
        return RedirectResponse(f"/agenda?erro={e}", status_code=303)
    if novo_id is None:
        return RedirectResponse("/agenda?erro=Esse+horário+já+existe", status_code=303)
    reagendar(app.state.scheduler)
    return RedirectResponse("/agenda", status_code=303)


@app.post("/agenda/{ag_id}/toggle")
def agenda_toggle(ag_id: int):
    db.toggle_agendamento(ag_id)
    reagendar(app.state.scheduler)
    return RedirectResponse("/agenda", status_code=303)


@app.post("/agenda/{ag_id}/remover")
def agenda_remover(ag_id: int):
    db.remover_agendamento(ag_id)
    reagendar(app.state.scheduler)
    return RedirectResponse("/agenda", status_code=303)


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
