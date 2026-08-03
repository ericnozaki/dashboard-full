import os
import concurrent.futures
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
import json
import requests
from urllib.parse import urlparse, parse_qs

# Credenciais do seu App (Fixas)
CLIENT_ID = "1194661319744999"
CLIENT_SECRET = "0GnE2ffoGHUkit67Zl3aQrXlIRs2Ck6U"

# Senhas invisíveis do Banco de Dados que a Vercel gera
KV_URL = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")

def redis_get(key):
    if not KV_URL or not KV_TOKEN: return None
    url = f"{KV_URL.rstrip('/')}/get/{key}"
    r = requests.get(url, headers={"Authorization": f"Bearer {KV_TOKEN}"})
    if r.status_code == 200:
        data = r.json().get("result")
        if data: return json.loads(data)
    return None

def redis_set(key, value):
    if not KV_URL or not KV_TOKEN: return False
    url = f"{KV_URL.rstrip('/')}/set/{key}"
    r = requests.post(url, headers={"Authorization": f"Bearer {KV_TOKEN}"}, data=json.dumps(value))
    return r.status_code == 200

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        qs = parse_qs(parsed_path.query)

        # ---------------------------------------------------------
        # FUNÇÃO MÁGICA: Gravar o primeiro token no banco de dados!
        # ---------------------------------------------------------
        if 'code' in qs:
            code = qs['code'][0]
            payload = {
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "code": code,
                "redirect_uri": "https://www.google.com"
            }
            resp = requests.post("https://api.mercadolibre.com/oauth/token", data=payload)
            if resp.status_code == 200:
                tokens = resp.json()
                redis_set("ml_tokens", tokens)
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"sucesso": "✅ BANCO CONFIGURADO PARA SEMPRE! Pode fechar esta aba e abrir o seu Painel."}).encode('utf-8'))
            else:
                self.send_response(400)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"erro": "Falha no codigo TG, gere outro", "detalhe": resp.text}).encode('utf-8'))
            return

        try:
            # ---------------------------------------------------------
            # FLUXO AUTÔNOMO: Ler do banco e renovar sozinho se vencer
            # ---------------------------------------------------------
            tokens = redis_get("ml_tokens")
            if not tokens:
                self.send_response(401)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Banco vazio. Faça a ativação inicial com a URL especial."}).encode("utf-8"))
                return

            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {access_token}", "User-Agent": "Mozilla/5.0"})

            # Testa se a chave atual ainda funciona
            resp_user = session.get("https://api.mercadolibre.com/users/me", timeout=5)
            
            # Se deu erro, significa que as 6 horas passaram. O Python renova sozinho!
            if resp_user.status_code != 200:
                payload = {
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "refresh_token": refresh_token
                }
                resp_refresh = requests.post("https://api.mercadolibre.com/oauth/token", data=payload)
                
                if resp_refresh.status_code == 200:
                    tokens = resp_refresh.json()
                    redis_set("ml_tokens", tokens) # Salva a chave nova no banco
                    access_token = tokens.get("access_token")
                    session.headers.update({"Authorization": f"Bearer {access_token}"})
                    resp_user = session.get("https://api.mercadolibre.com/users/me", timeout=5)
                else:
                    self.send_response(401)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Ocorreu um erro na renovação."}).encode("utf-8"))
                    return

            user_id = resp_user.json().get("id")
            hoje = datetime.now(timezone.utc)
            data_7_dias = (hoje - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000-00:00")
            
            def fetch_orders():
                vendas = {}
                offset = 0
                while True:
                    url = f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid&order.date_created.from={data_7_dias}&offset={offset}&limit=50"
                    resp = session.get(url, timeout=5)
                    if resp.status_code != 200: break
                    dados = resp.json()
                    resultados = dados.get("results", [])
                    if not resultados: break
                    for pedido in resultados:
                        for item in pedido.get("order_items", []):
                            item_id = item.get("item", {}).get("id")
                            qtd = item.get("quantity", 0)
                            if item_id: vendas[item_id] = vendas.get(item_id, 0) + int(qtd)
                    offset += 50
                    if offset >= dados.get("paging", {}).get("total", 0): break
                return vendas

            def fetch_items():
                item_ids = []
                offset = 0
                while True:
                    url = f"https://api.mercadolibre.com/users/{user_id}/items/search?limit=50&offset={offset}"
                    resp = session.get(url, timeout=5)
                    if resp.status_code != 200: break
                    dados = resp.json()
                    resultados = dados.get("results", [])
                    if not resultados: break
                    item_ids.extend(resultados)
                    offset += 50
                    if offset >= dados.get("paging", {}).get("total", 0): break
                return list(set(item_ids)) 

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                future_orders = executor.submit(fetch_orders)
                future_items = executor.submit(fetch_items)
                vendas = future_orders.result()
                item_ids = future_items.result()

            relatorio = []
            IDs_com_estoque_misturado = []
            chunks = [item_ids[i:i + 20] for i in range(0, len(item_ids), 20)]
            
            for chunk in chunks:
                ids_str = ",".join(chunk)
                resp_detalhe = session.get(f"https://api.mercadolibre.com/items?ids={ids_str}", timeout=10)
                if resp_detalhe.status_code != 200: continue
                
                for item_json in resp_detalhe.json():
                    if item_json.get("code") != 200: continue
                    item_data = item_json.get("body", {})
                    if item_data.get("shipping", {}).get("logistic_type") != "fulfillment": continue

                    item_id = item_data.get("id")
                    titulo = str(item_data.get("title", ""))
                    estoque_full = int(item_data.get("available_quantity", 0))
                    preco = float(item_data.get("price", 0.0))
                    
                    if item_id in IDs_com_estoque_misturado: estoque_full = 0

                    vendas_7d = int(vendas.get(item_id, 0))
                    venda_diaria_7d = vendas_7d / 7.0
                    semanas_7d = float(estoque_full / (venda_diaria_7d * 7)) if venda_diaria_7d > 0 else 999.0
                    estoque_ideal_60d = venda_diaria_7d * 60
                    enviar_60d = int(max(0, round(estoque_ideal_60d - estoque_full)))

                    relatorio.append({
                        "titulo": titulo,
                        "estoque": estoque_full,
                        "vendas_7d": vendas_7d,
                        "semanas_7d": round(semanas_7d, 1),
                        "enviar_60d": enviar_60d,
                        "preco": preco
                    })

            relatorio.sort(key=lambda x: x["enviar_60d"], reverse=True)
            self.send_response(200)
            self.send_header("Content-type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(relatorio, ensure_ascii=False).encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
