"""Ponte com o Diretor (app de cenas na nuvem).

Pega um roteiro em texto (o que a IA daqui já gera), manda decupar em cenas
pela API do Diretor e grava direto no Firestore do usuário — o roteiro
aparece no app, em qualquer aparelho.

Config no .env:
    DIRETOR_UID=<ID de usuário mostrado em Configurações no Diretor>
    DIRETOR_API=https://directors-app-scenes.vercel.app/api/gerar-roteiro.js
    DIRETOR_CREDENCIAL=D:\\caminho\\para\\service-account.json
"""
from __future__ import annotations

import logging
import os
import random
import string
import time
import unicodedata

import httpx

log = logging.getLogger(__name__)

API_PADRAO = "https://directors-app-scenes.vercel.app/api/gerar-roteiro.js"
_app_fb = None  # app do firebase-admin (inicializa uma vez)


class DiretorErro(Exception):
    """Erro previsível da ponte (mensagem já pronta pra mostrar na tela)."""


def configurado() -> bool:
    return bool(os.getenv("DIRETOR_UID") and os.getenv("DIRETOR_CREDENCIAL"))


def _uid() -> str:
    uid = (os.getenv("DIRETOR_UID") or "").strip()
    if not uid:
        raise DiretorErro("Falta DIRETOR_UID no .env (pegue em Configurações no Diretor).")
    return uid


def _novo_id(prefixo: str) -> str:
    sufixo = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    return f"{prefixo}_{int(time.time() * 1000)}_{sufixo}"


def _firestore():
    """Cliente Firestore via service account (lazy, uma vez só)."""
    global _app_fb
    cred_path = (os.getenv("DIRETOR_CREDENCIAL") or "").strip()
    if not cred_path:
        raise DiretorErro("Falta DIRETOR_CREDENCIAL no .env (arquivo .json da conta de serviço).")
    if not os.path.exists(cred_path):
        raise DiretorErro(f"Credencial não encontrada: {cred_path}")
    try:
        import firebase_admin
        from firebase_admin import credentials, firestore
    except ImportError as e:
        raise DiretorErro("Instale a dependência: pip install firebase-admin") from e

    if _app_fb is None:
        _app_fb = firebase_admin.initialize_app(credentials.Certificate(cred_path))
    return firestore.client(_app_fb)


def _decupar(texto: str, titulo: str = "") -> dict:
    """Chama a API do Diretor pra transformar o texto em cenas."""
    url = (os.getenv("DIRETOR_API") or API_PADRAO).strip()
    try:
        r = httpx.post(url, json={"texto": texto, "tema": titulo}, timeout=90)
    except httpx.HTTPError as e:
        raise DiretorErro(f"Não consegui falar com a API do Diretor: {e}") from e
    if r.status_code != 200:
        raise DiretorErro(f"API do Diretor respondeu {r.status_code}: {r.text[:200]}")
    dados = r.json()
    if not isinstance(dados.get("cenas"), list) or not dados["cenas"]:
        raise DiretorErro("A IA não devolveu cenas.")
    return dados


_FUNCOES = {"gancho", "desenvolvimento", "virada", "climax", "respiro", "encerramento"}


def _sem_acento(s: str) -> str:
    """'Clímax' -> 'climax' (a IA às vezes devolve o nome, não o id)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").strip().lower())
        if unicodedata.category(c) != "Mn"
    )


def _normalizar(dados: dict, titulo_fallback: str) -> dict:
    """Monta o roteiro no formato que o Diretor espera."""
    agora = int(time.time() * 1000)
    cenas = []
    for c in dados.get("cenas", []):
        funcao = _sem_acento(str(c.get("funcao") or ""))
        cenas.append({
            "id": _novo_id("cen"),
            "descricao": str(c.get("descricao") or "").strip(),
            "tecnicaId": (c.get("tecnicaId") or None) if c.get("tecnicaId") not in ("null", "") else None,
            "funcao": funcao if funcao in _FUNCOES else None,
            "emocao": None, "imagemId": None, "comp": None, "luz": None,
            "dica": "", "local": "", "horario": "", "equipamento": "", "gravada": False,
        })
    return {
        "id": _novo_id("rot"),
        "nome": (str(dados.get("nome") or titulo_fallback or "Roteiro do Newsletter"))[:80],
        "mensagem": str(dados.get("mensagem") or "").strip(),
        "criadoEm": agora,
        "atualizadoEm": agora,
        "cenas": cenas,
        "origem": "newsletter",
    }


def enviar_roteiro(texto: str, titulo: str = "") -> dict:
    """Decupa o texto em cenas e publica na conta do usuário no Diretor.

    Devolve o roteiro gravado. Lança DiretorErro com mensagem amigável.
    """
    texto = (texto or "").strip()
    if not texto:
        raise DiretorErro("Roteiro vazio.")
    uid = _uid()
    db_fs = _firestore()  # valida credencial antes de gastar chamada de IA
    roteiro = _normalizar(_decupar(texto, titulo), titulo)
    db_fs.collection("users").document(uid).collection("roteiros").document(roteiro["id"]).set(roteiro)
    log.info("Roteiro '%s' enviado ao Diretor (%d cenas).", roteiro["nome"], len(roteiro["cenas"]))
    return roteiro
