# henapi.py

import discord
from discord.ext import commands
import aiohttp
from bs4 import BeautifulSoup
import asyncio
import re
import json
import os
import uuid
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import uvicorn
import threading
import xml.etree.ElementTree as ET

# ====== CONFIGURAÇÃO ======
DISCORD_TOKEN = "SEU_TOKEN_AQUI"
COMMAND_PREFIX = "."
API_PORT = 3000
ARQUIVO_HISTORICO = "historico.json"
ARQUIVO_CONFIG = "config.json"
RESULTADOS_POR_CANAL = 3
CATEGORIA_BASE_PADRAO = "╍╍╍CATEGORIES"
HEADERS = {'User-Agent': 'HenAPI/1.0'}

# ====== HISTÓRICO POR SERVIDOR ======
if not os.path.exists(ARQUIVO_HISTORICO):
    with open(ARQUIVO_HISTORICO, 'w') as f:
        json.dump({}, f)

def salvar_historico(data):
    with open(ARQUIVO_HISTORICO, 'w') as f:
        json.dump(data, f, indent=2)

with open(ARQUIVO_HISTORICO, 'r') as f:
    historico = json.load(f)

def ja_enviado(guild_id, link):
    return link in historico.get(str(guild_id), [])

def registrar_envio(guild_id, link):
    gstr = str(guild_id)
    historico.setdefault(gstr, []).append(link)
    salvar_historico(historico)

# ====== CONFIGS E TOKENS ======
if not os.path.exists(ARQUIVO_CONFIG):
    with open(ARQUIVO_CONFIG, 'w') as f:
        json.dump({}, f)

def carregar_config():
    with open(ARQUIVO_CONFIG, 'r') as f:
        return json.load(f)

def salvar_config(cfg):
    with open(ARQUIVO_CONFIG, 'w') as f:
        json.dump(cfg, f, indent=2)

# ====== DISCORD BOT ======
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
ativado_por_guild = set()

@bot.command()
async def sethentai(ctx):
    ativado_por_guild.add(ctx.guild.id)
    await ctx.send("✅ Envio automático NSFW ativado neste servidor.")

def formatar_nome_canal(nome):
    nome = nome.lower()
    nome = re.sub(r'[^a-z0-9\s-]', '', nome)
    nome = nome.replace("vídeo", "").replace("video", "")
    return nome.strip()

def verificar_todas_palavras(title, palavras_chave):
    title = title.lower()
    return all(p in title for p in palavras_chave)

async def garantir_nsfw(canal):
    if not canal.is_nsfw():
        try: await canal.edit(nsfw=True)
        except: pass

async def listar_canais_categoria(guild, categoria_base):
    categorias = [c for c in guild.categories if c.name.startswith(categoria_base)]
    canais = []
    for cat in categorias:
        canais.extend([c for c in cat.channels if isinstance(c, discord.TextChannel)])
    return canais

# ====== SCRAPERS ======

async def buscar_reddit(session, query, max_results=5):
    url = f"https://www.reddit.com/search/?q={query}&include_over_18=1&type=link"
    async with session.get(url, headers=HEADERS) as r:
        soup = BeautifulSoup(await r.text(), 'html.parser')
        results = []
        for a in soup.find_all('a', href=True):
            href = a['href']
            if any(href.endswith(ext) for ext in ['.jpg', '.png', '.gif', '.mp4', '.webm']):
                title = a.get('title', '') + a.get_text()
                results.append({'url': href, 'title': title})
        return results[:max_results]

async def buscar_rule34(session, query, max_results=5):
    url = f"https://rule34.xxx/index.php?page=dapi&s=post&q=index&tags={query}&limit=50"
    async with session.get(url, headers=HEADERS) as r:
        soup = BeautifulSoup(await r.text(), 'xml')
        return [{'url': p['file_url'], 'title': p.get('tags', '')} for p in soup.find_all('post')][:max_results]

async def buscar_e621(session, query, max_results=5):
    url = f"https://e621.net/posts.json?tags={query}&limit=50"
    headers = HEADERS.copy()
    async with session.get(url, headers=headers) as r:
        if r.content_type != 'application/json': return []
        data = await r.json()
        return [{'url': p['file']['url'], 'title': " ".join(p.get('tags', {}).get('general', []))} for p in data['posts'] if 'file' in p][:max_results]

SCRAPERS = {
    "reddit": buscar_reddit,
    "rule34": buscar_rule34,
    "e621": buscar_e621
}

async def enviar_para_canal(canal, resultados, palavras_chave, apenas_videos, guild_id):
    enviados = 0
    for item in resultados:
        url = item['url']
        title = item.get('title', '')
        if ja_enviado(guild_id, url): continue
        if apenas_videos and not url.endswith(('.mp4', '.webm')): continue
        if not verificar_todas_palavras(title, palavras_chave): continue
        try:
            await canal.send(url)
            registrar_envio(guild_id, url)
            enviados += 1
            await asyncio.sleep(1)
        except:
            continue
        if enviados >= RESULTADOS_POR_CANAL:
            break

async def loop_auto():
    await bot.wait_until_ready()
    async with aiohttp.ClientSession() as session:
        while not bot.is_closed():
            for guild in bot.guilds:
                if guild.id not in ativado_por_guild:
                    continue
                cfg = carregar_config().get("default", {})
                cat_base = cfg.get("categoria_base", CATEGORIA_BASE_PADRAO)
                canais = await listar_canais_categoria(guild, cat_base)
                tarefas = [processar(canal, session, guild.id) for canal in canais]
                await asyncio.gather(*tarefas)
            await asyncio.sleep(15)

async def processar(canal, session, guild_id):
    nome_formatado = formatar_nome_canal(canal.name)
    palavras_chave = nome_formatado.split()
    if not palavras_chave: return
    await garantir_nsfw(canal)
    apenas_videos = "video" in canal.name.lower() or "vídeo" in canal.name.lower()
    resultados = []
    for scraper in SCRAPERS.values():
        try:
            dados = await scraper(session, "+".join(palavras_chave), 20)
            resultados.extend(dados)
        except:
            continue
    unicos = {item['url']: item for item in resultados}.values()
    await enviar_para_canal(canal, list(unicos), palavras_chave, apenas_videos, guild_id)

@bot.event
async def on_ready():
    print(f"🤖 Bot logado como {bot.user}")
    bot.loop.create_task(loop_auto())

# ====== FASTAPI ======
app = FastAPI()

class RequisicaoBusca(BaseModel):
    query: str
    apenas_videos: bool = False

@app.middleware("http")
async def verificar_token(request: Request, call_next):
    token = request.headers.get("x-api-token")
    configs = carregar_config()
    if token not in configs:
        raise HTTPException(status_code=401, detail="Token inválido.")
    request.state.config = configs[token]
    return await call_next(request)

@app.post("/generate-token")
def gerar_token():
    token = uuid.uuid4().hex
    configs = carregar_config()
    configs[token] = configs.get("default", {
        "categoria_base": CATEGORIA_BASE_PADRAO,
        "limite": 5
    })
    salvar_config(configs)
    print(f"[TOKEN CRIADO]: {token}")
    return {"token": token}

@app.post("/buscar")
async def buscar(req: RequisicaoBusca, request: Request):
    cfg = request.state.config
    resultados = []
    async with aiohttp.ClientSession() as session:
        for site in SCRAPERS:
            try:
                dados = await SCRAPERS[site](session, req.query, cfg.get("limite", 5))
                for d in dados:
                    if req.apenas_videos and not d["url"].endswith(('.mp4', '.webm')):
                        continue
                    resultados.append(d)
            except:
                continue
    return {"resultados": resultados[:cfg.get("limite", 5)]}

# ====== EXECUÇÃO ======
def iniciar_api():
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)

if __name__ == "__main__":
    threading.Thread(target=iniciar_api).start()
    bot.run(DISCORD_TOKEN)