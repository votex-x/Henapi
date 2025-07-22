import aiohttp
import json
import os
import uuid
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup

API_PORT = 3000
ARQUIVO_HISTORICO = "historico.json"
ARQUIVO_CONFIG = "config.json"
HEADERS = {'User-Agent': 'HenAPI/1.0'}

if not os.path.exists(ARQUIVO_HISTORICO):
    with open(ARQUIVO_HISTORICO, 'w') as f:
        json.dump({}, f)

def salvar_historico(data):
    with open(ARQUIVO_HISTORICO, 'w') as f:
        json.dump(data, f, indent=2)

with open(ARQUIVO_HISTORICO, 'r') as f:
    historico = json.load(f)

def ja_enviado(link):
    return link in historico.get("enviados", [])

def registrar_envio(link):
    historico.setdefault("enviados", []).append(link)
    salvar_historico(historico)

if not os.path.exists(ARQUIVO_CONFIG):
    with open(ARQUIVO_CONFIG, 'w') as f:
        json.dump({}, f)

def carregar_config():
    with open(ARQUIVO_CONFIG, 'r') as f:
        return json.load(f)

def salvar_config(cfg):
    with open(ARQUIVO_CONFIG, 'w') as f:
        json.dump(cfg, f, indent=2)

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
        "limite": 5
    })
    salvar_config(configs)
    return {"token": token}

@app.post("/buscar")
async def buscar(req: RequisicaoBusca, request: Request):
    cfg = request.state.config
    resultados = []
    async with aiohttp.ClientSession() as session:
        for site in ["reddit", "rule34", "e621"]:
            try:
                if site == "reddit":
                    dados = await buscar_reddit(session, req.query)
                elif site == "rule34":
                    dados = await buscar_rule34(session, req.query)
                elif site == "e621":
                    dados = await buscar_e621(session, req.query)
                for d in dados:
                    if req.apenas_videos and not d["url"].endswith(('.mp4', '.webm')):
                        continue
                    if not ja_enviado(d["url"]):
                        registrar_envio(d["url"])
                        resultados.append(d)
            except:
                continue
    return {"resultados": resultados[:cfg.get("limite", 5)]}

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
        if r.content_type != 'application/json':
            return []
        data = await r.json()
        return [{'url': p['file']['url'], 'title': " ".join(p.get('tags', {}).get('general', []))} for p in data['posts'] if 'file' in p][:max_results]
