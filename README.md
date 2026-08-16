# Newsletter Central

Newsletter pessoal diária, gerada a partir de RSS de fontes confiáveis e resumida pela OpenAI.
**A IA nunca busca na web nem inventa fatos** — ela só resume, traduz e ranqueia o que foi coletado dos feeds.

## Instalação (Windows)

```powershell
cd "D:\Repositórios locais\Newsletter central"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Configuração

1. O arquivo `.env` já está criado com sua chave OpenAI.
   - Se quiser trocar o horário, edite `DAILY_TIME` (formato `HH:MM`, 24h).
2. Os temas iniciais (Edição de Vídeo, Avanços da IA, Pokémon, Finanças) ficam em `config/feeds.yaml`.
   - Esse arquivo só é lido na **primeira execução**. Depois disso, gerencie tudo pela interface web.

## Rodar

```powershell
.venv\Scripts\activate
python run.py
```

Acesse `http://localhost:8000`.

- **`/`** — última edição gerada + histórico.
- **`/temas`** — adicionar/remover temas e fontes RSS.
- **Botão "Gerar agora"** — força a geração imediata (útil pra testar).
- **Job automático** — roda todo dia no horário do `.env` (default 12:00, fuso São Paulo).

## Rodar 24/7 no PC

Opções no Windows:
- **Tarefa do Iniciar do Windows:** crie um atalho para `run.py` na pasta Inicializar.
- **NSSM** (recomendado): instala o `python run.py` como serviço do Windows.

## Segurança da chave

- A chave fica só em `.env`, que está no `.gitignore`. **Nunca** commite esse arquivo.
- Se a chave vazar (screenshot, paste em chat, push acidental), revogue em
  https://platform.openai.com/api-keys e gere uma nova.

## Como evitamos alucinação

- Coleta é 100% RSS via `feedparser` — links e títulos são os da fonte original.
- A IA recebe **apenas** o texto bruto coletado e devolve um JSON.
- Validamos que cada link retornado pela IA existe na lista original; se não existir, descartamos.
- Se a OpenAI falhar ou retornar lixo, caímos pra um fallback que usa o trecho cru do RSS.
- Cada item da edição mostra a fonte e um link "abrir original" pra você conferir.

## Ponte com o Diretor (app de cenas)

Manda um roteiro daqui direto pro [Diretor](https://directors-app-scenes.vercel.app),
já decupado em cenas (cada `[pausa]` vira um corte). O roteiro aparece na sua
conta do app, em qualquer aparelho.

**Configurar (uma vez):**

1. `pip install -r requirements.txt` (traz o `firebase-admin`).
2. No Diretor: **Configurações → Conta (nuvem) → Copiar ID**. Cole em `DIRETOR_UID` no `.env`.
3. No [Console do Firebase](https://console.firebase.google.com) → ⚙ **Configurações do projeto**
   → aba **Contas de serviço** → **Gerar nova chave privada**. Salve o `.json`
   **fora do repositório** e aponte `DIRETOR_CREDENCIAL` pra ele.
   > Esse arquivo dá acesso total ao projeto — nunca commite nem compartilhe.

**Usar:** abra qualquer roteiro (de notícia ou livre) e clique em **🎬 Enviar pro Diretor**.

## Próximos passos

- [ ] Bot Telegram (fase 2)
- [ ] Push pro celular
- [ ] Envio automático pro Diretor junto da geração diária
