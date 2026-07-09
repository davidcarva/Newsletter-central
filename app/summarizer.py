"""Resumo/tradução via OpenAI. SEM busca na web — só processa texto recebido."""
from __future__ import annotations
import os
import json
import logging
from typing import Iterable
from openai import OpenAI
from .collector import Item
from . import db

log = logging.getLogger(__name__)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
MAX_POR_TEMA = 6  # quantos itens entram no resumo final por tema

SYSTEM_PROMPT = (
    "Você é um curador de notícias. Sua tarefa é APENAS resumir e organizar "
    "notícias que já foram coletadas de feeds RSS. "
    "REGRAS RÍGIDAS:\n"
    "1. NUNCA invente fatos, datas, números ou nomes que não estejam no material fornecido.\n"
    "2. NUNCA adicione informação externa nem suposições.\n"
    "3. Se o conteúdo de um item for vago, escreva um resumo curto e honesto sem completar lacunas.\n"
    "4. Mantenha o link original exatamente como recebido.\n"
    "5. Escreva em português do Brasil, tom direto e claro.\n"
    "6. O resumo de cada item deve ter 2-4 frases.\n"
    "Retorne JSON estritamente no formato pedido."
)


def _client() -> OpenAI | None:
    key = os.getenv("OPENAI_API_KEY")
    if not key or key.startswith("cole-"):
        log.warning("OPENAI_API_KEY ausente — modo fallback (sem resumo IA).")
        return None
    return OpenAI(api_key=key)


def _fallback(itens: list[Item]) -> list[dict]:
    """Sem IA: usa título + trecho cru do RSS."""
    return [
        {
            "titulo": it.titulo,
            "resumo": (it.resumo_original or "")[:400],
            "link": it.link,
            "fonte": it.fonte,
        }
        for it in itens[:MAX_POR_TEMA]
    ]


def resumir_tema(tema: str, itens: list[Item]) -> list[dict]:
    if not itens:
        return []

    client = _client()
    if client is None:
        return _fallback(itens)

    # Monta payload enxuto pro modelo
    payload = [
        {
            "i": idx,
            "titulo": it.titulo,
            "fonte": it.fonte,
            "link": it.link,
            "trecho": it.resumo_original[:800],
        }
        for idx, it in enumerate(itens)
    ]

    user_msg = (
        f"Tema: {tema}\n\n"
        f"Itens coletados (JSON): {json.dumps(payload, ensure_ascii=False)}\n\n"
        f"Selecione os {MAX_POR_TEMA} mais relevantes/recentes. "
        "Para cada um, devolva: titulo (pode traduzir pt-BR), resumo (2-4 frases em pt-BR), "
        "link (idêntico ao recebido), fonte (idêntica). "
        'Formato: {"itens": [{"titulo":..., "resumo":..., "link":..., "fonte":...}, ...]}'
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        data = json.loads(resp.choices[0].message.content)
        resultados = data.get("itens", [])
        # Sanidade: garante que os links retornados existem nos originais
        links_validos = {it.link for it in itens}
        limpos = []
        for r in resultados:
            if r.get("link") in links_validos and r.get("titulo") and r.get("resumo"):
                limpos.append({
                    "titulo": r["titulo"].strip(),
                    "resumo": r["resumo"].strip(),
                    "link": r["link"],
                    "fonte": r.get("fonte", "").strip() or "Fonte",
                })
        if not limpos:
            log.warning("IA não retornou itens válidos para %s — usando fallback", tema)
            return _fallback(itens)
        return limpos[:MAX_POR_TEMA]
    except Exception as e:
        log.exception("Falha ao chamar OpenAI para tema %s: %s", tema, e)
        return _fallback(itens)


def _prompt_base() -> str:
    """Prompt-base configurado pelo usuário — tom, persona, estilo. Aplicado em todo roteiro."""
    base = db.get_config("prompt_base_roteiro", "").strip()
    if not base:
        return ""
    return (
        "\n\n🎙️ ESTILO PESSOAL DO CRIADOR (aplique em TODO o roteiro sem quebrar as regras acima):\n"
        f"{base}\n"
    )


ROTEIRO_SYSTEM = (
    "Você é roteirista de vídeos curtos (Shorts/Reels/TikTok), 30 a 60 segundos. "
    "Escreva em português do Brasil, tom direto e envolvente, sem clickbait barato. "
    "REGRAS RÍGIDAS:\n"
    "1. NUNCA invente fatos, números, nomes ou citações que não estejam no material recebido.\n"
    "2. Se faltar informação, faça o roteiro mais curto — não complete lacunas com suposições.\n"
    "3. Não cite o link nem a URL no roteiro falado.\n"
    "4. Use marcações de leitura/voz pra facilitar a entrega (humano OU TTS):\n"
    "   - CAPSLOCK em palavras de ênfase forte\n"
    "   - Reticências (...) pra pausas dramáticas curtas\n"
    "   - Quebra de linha entre frases pra marcar respiração\n"
    "   - Ponto de exclamação só quando faz sentido emocional (não abuse)\n"
    "   - [pausa] entre colchetes pra pausa mais longa\n"
    "5. Estrutura obrigatória, com cabeçalhos exatamente como abaixo:\n"
    "   🎬 HOOK (3-5s):\n"
    "   📢 DESENVOLVIMENTO (20-40s):\n"
    "   ✨ CTA (3-5s):\n"
    "6. Após o roteiro, adicione:\n"
    "   📊 ESTIMATIVA: ~X segundos | ~Y palavras\n"
)


ROTEIRO_LIVRE_SYSTEM = (
    "Você é roteirista de vídeos curtos (Shorts/Reels/TikTok), 30 a 60 segundos. "
    "Escreva em português do Brasil, tom direto e envolvente. "
    "Este é um roteiro de TEMA LIVRE (não baseado numa notícia específica), "
    "então você pode falar do assunto de forma geral, didática ou opinativa.\n"
    "REGRAS:\n"
    "1. Não invente dados específicos (estatísticas, citações de pessoas reais, datas de eventos) "
    "que não tenham sido fornecidos no contexto. Se precisar de exemplo, use exemplos genéricos.\n"
    "2. Se o contexto do usuário já trouxer fatos/dados, pode usá-los normalmente.\n"
    "3. Foque em entregar valor pro espectador: dica, insight, curiosidade, opinião clara.\n"
    "4. Use marcações de leitura/voz:\n"
    "   - CAPSLOCK em palavras de ênfase forte\n"
    "   - Reticências (...) pra pausas curtas\n"
    "   - [pausa] pra pausas longas\n"
    "   - Quebra de linha entre frases pra respiração\n"
    "   - ! com moderação\n"
    "5. Estrutura obrigatória:\n"
    "   🎬 HOOK (3-5s):\n"
    "   📢 DESENVOLVIMENTO (20-40s):\n"
    "   ✨ CTA (3-5s):\n"
    "6. Termine com:\n"
    "   📊 ESTIMATIVA: ~X segundos | ~Y palavras\n"
)


def gerar_roteiro_livre(
    tema: str | None,
    titulo: str,
    contexto: str | None,
    evitar: list[str] | None = None,
) -> str:
    """Gera roteiro a partir de um tópico livre digitado pelo usuário.
    `evitar`: títulos de roteiros já usados no mesmo tema — IA é instruída a
    NÃO repetir abordagem/exemplos."""
    client = _client()
    if client is None:
        return "⚠️ Sem chave OpenAI configurada — roteiro não gerado."

    partes = [f"Título/Tópico do vídeo: {titulo}"]
    if tema:
        partes.append(f"Tema/área: {tema}")
    if contexto:
        partes.append(f"\nContexto adicional do criador:\n{contexto}")
    if evitar:
        lista = "\n".join(f"- {t}" for t in evitar[:15])
        partes.append(
            "\n⚠️ Roteiros JÁ USADOS recentemente neste tema "
            "(traga ABORDAGEM, EXEMPLOS e GANCHO diferentes — não repita ângulo):\n"
            f"{lista}"
        )
    partes.append(
        "\nGere o roteiro seguindo EXATAMENTE a estrutura e marcações pedidas. "
        "Devolva APENAS o roteiro em texto puro."
    )
    user_msg = "\n".join(partes)

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": ROTEIRO_LIVRE_SYSTEM + _prompt_base()},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.8,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.exception("Falha ao gerar roteiro livre: %s", e)
        return f"❌ Erro ao gerar roteiro: {e}"


def gerar_roteiro(titulo: str, resumo: str, fonte: str) -> str:
    """Gera roteiro de short a partir do título/resumo já validados."""
    client = _client()
    if client is None:
        return (
            "⚠️ Sem chave OpenAI configurada — roteiro não gerado.\n\n"
            f"Notícia: {titulo}\nResumo: {resumo}"
        )

    user_msg = (
        f"Notícia a transformar em roteiro de short:\n\n"
        f"Título: {titulo}\n"
        f"Resumo: {resumo}\n"
        f"Fonte: {fonte}\n\n"
        "Gere o roteiro seguindo EXATAMENTE a estrutura e marcações pedidas. "
        "Devolva APENAS o roteiro em texto puro (sem markdown extra além das marcações)."
    )

    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": ROTEIRO_SYSTEM + _prompt_base()},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.7,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        log.exception("Falha ao gerar roteiro: %s", e)
        return f"❌ Erro ao gerar roteiro: {e}"
