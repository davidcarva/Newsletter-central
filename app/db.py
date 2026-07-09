"""Camada de persistência simples com SQLite."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).parent.parent / "newsletter.db"


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn):
    """Adiciona colunas em tabelas existentes (idempotente)."""
    def cols(tabela):
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({tabela})").fetchall()}
    if "roteiros" in {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
        existing = cols("roteiros")
        if "usado" not in existing:
            conn.execute("ALTER TABLE roteiros ADD COLUMN usado INTEGER NOT NULL DEFAULT 0")
        if "usado_em" not in existing:
            conn.execute("ALTER TABLE roteiros ADD COLUMN usado_em TEXT")
    if "roteiros_livres" in {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
        existing = cols("roteiros_livres")
        if "usado" not in existing:
            conn.execute("ALTER TABLE roteiros_livres ADD COLUMN usado INTEGER NOT NULL DEFAULT 0")
        if "usado_em" not in existing:
            conn.execute("ALTER TABLE roteiros_livres ADD COLUMN usado_em TEXT")
    if "itens" in {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
        if "repetida" not in cols("itens"):
            conn.execute("ALTER TABLE itens ADD COLUMN repetida INTEGER NOT NULL DEFAULT 0")


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS temas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT UNIQUE NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS feeds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tema_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                FOREIGN KEY (tema_id) REFERENCES temas(id) ON DELETE CASCADE,
                UNIQUE(tema_id, url)
            );

            CREATE TABLE IF NOT EXISTS edicoes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                data TEXT NOT NULL,
                conteudo_html TEXT NOT NULL,
                criado_em TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS vistos (
                link TEXT PRIMARY KEY,
                visto_em TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS itens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                edicao_id INTEGER NOT NULL,
                tema TEXT NOT NULL,
                titulo TEXT NOT NULL,
                resumo TEXT NOT NULL,
                link TEXT NOT NULL,
                fonte TEXT,
                cor TEXT NOT NULL DEFAULT '',
                ordem INTEGER NOT NULL DEFAULT 0,
                repetida INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (edicao_id) REFERENCES edicoes(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS roteiros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                texto TEXT NOT NULL,
                criado_em TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES itens(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_itens_edicao ON itens(edicao_id);
            CREATE INDEX IF NOT EXISTS idx_roteiros_item ON roteiros(item_id);

            CREATE TABLE IF NOT EXISTS config (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS roteiros_livres (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tema TEXT,
                titulo TEXT NOT NULL,
                contexto TEXT,
                texto TEXT NOT NULL,
                cor TEXT NOT NULL DEFAULT '',
                criado_em TEXT NOT NULL,
                usado INTEGER NOT NULL DEFAULT 0,
                usado_em TEXT
            );
            """
        )
        _migrate(conn)


def seed_from_yaml(yaml_data: dict):
    """Popula o banco a partir do feeds.yaml (só se estiver vazio)."""
    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM temas").fetchone()[0]
        if count > 0:
            return
        for tema in yaml_data.get("temas", []):
            cur = conn.execute(
                "INSERT INTO temas (nome, ativo) VALUES (?, ?)",
                (tema["nome"], 1 if tema.get("ativo", True) else 0),
            )
            tema_id = cur.lastrowid
            for url in tema.get("feeds", []):
                conn.execute(
                    "INSERT OR IGNORE INTO feeds (tema_id, url) VALUES (?, ?)",
                    (tema_id, url),
                )


def listar_temas():
    with get_conn() as conn:
        temas = conn.execute("SELECT * FROM temas ORDER BY nome").fetchall()
        resultado = []
        for t in temas:
            feeds = conn.execute(
                "SELECT * FROM feeds WHERE tema_id = ? ORDER BY url", (t["id"],)
            ).fetchall()
            resultado.append({
                "id": t["id"],
                "nome": t["nome"],
                "ativo": bool(t["ativo"]),
                "feeds": [dict(f) for f in feeds],
            })
        return resultado


def temas_ativos_com_feeds():
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT t.id, t.nome, f.url
            FROM temas t
            JOIN feeds f ON f.tema_id = t.id
            WHERE t.ativo = 1
            ORDER BY t.nome
            """
        ).fetchall()
        agrupado: dict[str, list[str]] = {}
        for r in rows:
            agrupado.setdefault(r["nome"], []).append(r["url"])
        return agrupado


def adicionar_tema(nome: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO temas (nome, ativo) VALUES (?, 1)", (nome.strip(),)
        )
        return cur.lastrowid


def toggle_tema(tema_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE temas SET ativo = 1 - ativo WHERE id = ?", (tema_id,)
        )


def remover_tema(tema_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM feeds WHERE tema_id = ?", (tema_id,))
        conn.execute("DELETE FROM temas WHERE id = ?", (tema_id,))


def adicionar_feed(tema_id: int, url: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO feeds (tema_id, url) VALUES (?, ?)",
            (tema_id, url.strip()),
        )


def remover_feed(feed_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))


def salvar_edicao(data: str, conteudo_html: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO edicoes (data, conteudo_html, criado_em) VALUES (?, ?, ?)",
            (data, conteudo_html, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def listar_edicoes(limit: int = 30):
    with get_conn() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT id, data, criado_em FROM edicoes ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]


def obter_edicao(edicao_id: int):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM edicoes WHERE id = ?", (edicao_id,)
        ).fetchone()
        return dict(row) if row else None


def ultima_edicao():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM edicoes ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def marcar_vistos(links: Iterable[str]):
    agora = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO vistos (link, visto_em) VALUES (?, ?)",
            [(l, agora) for l in links],
        )


def salvar_itens(edicao_id: int, blocos: list[dict]):
    """blocos = [{tema, itens:[{titulo,resumo,link,fonte}]}, ...]"""
    with get_conn() as conn:
        ordem = 0
        for bloco in blocos:
            for item in bloco.get("itens", []):
                conn.execute(
                    """INSERT INTO itens (edicao_id, tema, titulo, resumo, link, fonte, ordem, repetida)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        edicao_id,
                        bloco["tema"],
                        item["titulo"],
                        item["resumo"],
                        item["link"],
                        item.get("fonte", ""),
                        ordem,
                        1 if item.get("repetida") else 0,
                    ),
                )
                ordem += 1


def itens_da_edicao(edicao_id: int) -> list[dict]:
    """Retorna itens agrupados por tema, ordem preservada."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM itens WHERE edicao_id = ? ORDER BY ordem", (edicao_id,)
        ).fetchall()
        agrupado: dict[str, list[dict]] = {}
        for r in rows:
            agrupado.setdefault(r["tema"], []).append(dict(r))
        return [{"tema": tema, "itens": its} for tema, its in agrupado.items()]


def obter_item(item_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM itens WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None


def definir_cor_item(item_id: int, cor: str):
    with get_conn() as conn:
        conn.execute("UPDATE itens SET cor = ? WHERE id = ?", (cor, item_id))


def definir_cor_tema_edicao(edicao_id: int, tema: str, cor: str):
    """Aplica a cor a TODOS os itens de um tema dentro de uma edição."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE itens SET cor = ? WHERE edicao_id = ? AND tema = ?",
            (cor, edicao_id, tema),
        )


def salvar_roteiro(item_id: int, texto: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO roteiros (item_id, texto, criado_em) VALUES (?, ?, ?)",
            (item_id, texto, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def obter_roteiro_mais_recente(item_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM roteiros WHERE item_id = ? ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        return dict(row) if row else None


def criar_roteiro_livre(tema: str | None, titulo: str, contexto: str | None, texto: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO roteiros_livres (tema, titulo, contexto, texto, criado_em)
               VALUES (?, ?, ?, ?, ?)""",
            (tema or None, titulo.strip(), (contexto or "").strip() or None,
             texto, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def listar_roteiros_livres(limit: int = 100):
    with get_conn() as conn:
        return [
            dict(r) for r in conn.execute(
                "SELECT id, tema, titulo, cor, criado_em FROM roteiros_livres ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        ]


def obter_roteiro_livre(rid: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM roteiros_livres WHERE id = ?", (rid,)).fetchone()
        return dict(row) if row else None


def regerar_roteiro_livre(rid: int, novo_texto: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE roteiros_livres SET texto = ?, criado_em = ? WHERE id = ?",
            (novo_texto, datetime.utcnow().isoformat(), rid),
        )


def definir_cor_roteiro_livre(rid: int, cor: str):
    with get_conn() as conn:
        conn.execute("UPDATE roteiros_livres SET cor = ? WHERE id = ?", (cor, rid))


def remover_roteiro_livre(rid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM roteiros_livres WHERE id = ?", (rid,))


def get_config(chave: str, default: str = "") -> str:
    with get_conn() as conn:
        row = conn.execute("SELECT valor FROM config WHERE chave = ?", (chave,)).fetchone()
        return row["valor"] if row else default


def set_config(chave: str, valor: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO config (chave, valor) VALUES (?, ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
            (chave, valor),
        )


def marcar_usado_livre(rid: int, usado: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE roteiros_livres SET usado = ?, usado_em = ? WHERE id = ?",
            (1 if usado else 0, datetime.utcnow().isoformat() if usado else None, rid),
        )


def marcar_usado_roteiro(roteiro_id: int, usado: bool):
    with get_conn() as conn:
        conn.execute(
            "UPDATE roteiros SET usado = ?, usado_em = ? WHERE id = ?",
            (1 if usado else 0, datetime.utcnow().isoformat() if usado else None, roteiro_id),
        )


def usados_recentes_por_tema(tema: str, limit: int = 15) -> list[str]:
    """Títulos de roteiros (livres + de notícia) já marcados como usados nesse tema."""
    if not tema:
        return []
    with get_conn() as conn:
        rows1 = conn.execute(
            """SELECT titulo, usado_em FROM roteiros_livres
               WHERE tema = ? AND usado = 1
               ORDER BY usado_em DESC LIMIT ?""",
            (tema, limit),
        ).fetchall()
        rows2 = conn.execute(
            """SELECT i.titulo AS titulo, r.usado_em AS usado_em
               FROM roteiros r
               JOIN itens i ON i.id = r.item_id
               WHERE i.tema = ? AND r.usado = 1
               ORDER BY r.usado_em DESC LIMIT ?""",
            (tema, limit),
        ).fetchall()
        combinado = [(r["titulo"], r["usado_em"]) for r in rows1] + \
                    [(r["titulo"], r["usado_em"]) for r in rows2]
        combinado.sort(key=lambda x: x[1] or "", reverse=True)
        return [t for t, _ in combinado[:limit]]


def biblioteca_roteiros(filtro_tema: str | None = None, filtro_usado: str = "todos") -> list[dict]:
    """Unifica roteiros livres + de notícia em uma lista. filtro_usado: 'todos'|'usados'|'nao_usados'."""
    with get_conn() as conn:
        livres = conn.execute(
            """SELECT id, tema, titulo, cor, criado_em, usado, usado_em
               FROM roteiros_livres ORDER BY id DESC"""
        ).fetchall()
        noticias = conn.execute(
            """SELECT r.id AS roteiro_id, i.id AS item_id, i.tema, i.titulo,
                      i.cor, r.criado_em, r.usado, r.usado_em, i.edicao_id
               FROM roteiros r
               JOIN itens i ON i.id = r.item_id
               WHERE r.id IN (
                   SELECT MAX(id) FROM roteiros GROUP BY item_id
               )
               ORDER BY r.id DESC"""
        ).fetchall()

    resultado = []
    for r in livres:
        d = dict(r)
        d["tipo"] = "livre"
        d["url"] = f"/roteiros-livres/{d['id']}"
        resultado.append(d)
    for r in noticias:
        d = dict(r)
        d["tipo"] = "noticia"
        d["id"] = d["roteiro_id"]
        d["url"] = f"/itens/{d['item_id']}/roteiro"
        resultado.append(d)

    resultado.sort(key=lambda x: x["criado_em"], reverse=True)

    if filtro_tema:
        resultado = [r for r in resultado if (r.get("tema") or "") == filtro_tema]
    if filtro_usado == "usados":
        resultado = [r for r in resultado if r["usado"]]
    elif filtro_usado == "nao_usados":
        resultado = [r for r in resultado if not r["usado"]]
    return resultado


def obter_tema(tema_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM temas WHERE id = ?", (tema_id,)).fetchone()
        if not row:
            return None
        feeds = conn.execute(
            "SELECT url FROM feeds WHERE tema_id = ?", (tema_id,)
        ).fetchall()
        return {"id": row["id"], "nome": row["nome"], "ativo": bool(row["ativo"]),
                "feeds": [f["url"] for f in feeds]}


def _inicio_do_dia_utc() -> str:
    """Início do dia de HOJE no fuso configurado, como ISO em UTC naive
    (mesmo formato dos timestamps gravados em `vistos`)."""
    tz = ZoneInfo(os.getenv("TIMEZONE", "America/Sao_Paulo"))
    inicio_local = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return inicio_local.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def classificar_vistos(links: Iterable[str]) -> dict[str, str]:
    """Classifica cada link: 'novo' (nunca visto), 'hoje' (já apareceu em edição
    de hoje) ou 'antigo' (visto em dia anterior)."""
    links = list(links)
    if not links:
        return {}
    with get_conn() as conn:
        placeholders = ",".join("?" * len(links))
        vistos = {
            r["link"]: r["visto_em"]
            for r in conn.execute(
                f"SELECT link, visto_em FROM vistos WHERE link IN ({placeholders})", links
            ).fetchall()
        }
    inicio_hoje = _inicio_do_dia_utc()
    resultado = {}
    for link in links:
        if link not in vistos:
            resultado[link] = "novo"
        elif vistos[link] >= inicio_hoje:
            resultado[link] = "hoje"
        else:
            resultado[link] = "antigo"
    return resultado


def itens_por_links(links: Iterable[str]) -> dict[str, dict]:
    """Última versão exibida de cada link (pra reaproveitar título/resumo já resumidos)."""
    links = list(links)
    if not links:
        return {}
    with get_conn() as conn:
        placeholders = ",".join("?" * len(links))
        rows = conn.execute(
            f"SELECT * FROM itens WHERE link IN ({placeholders}) ORDER BY id", links
        ).fetchall()
    return {r["link"]: dict(r) for r in rows}
