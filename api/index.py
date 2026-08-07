import os
import concurrent.futures
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
import json
import requests
from urllib.parse import urlparse, parse_qs

CLIENT_ID = "1194661319744999"
CLIENT_SECRET = "0GnE2ffoGHUkit67Zl3aQrXlIRs2Ck6U"

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
                redis_set("ml_tokens", resp.json())
                self.send_response(200)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"sucesso": "✅ AUTORIZADO!"}).encode('utf-8'))
            return

        try:
            tokens = redis_get("ml_tokens")
            if not tokens:
                self.send_response(401)
                self.send_header("Content-type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Token ausente."}).encode("utf-8"))
                return

            access_token = tokens.get("access_token")
            refresh_token = tokens.get("refresh_token")
            
            session = requests.Session()
            session.headers.update({"Authorization": f"Bearer {access_token}", "User-Agent": "Mozilla/5.0"})

            resp_user = session.get("https://api.mercadolibre.com/users/me", timeout=5)
            
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
                    redis_set("ml_tokens", tokens)
                    session.headers.update({"Authorization": f"Bearer {tokens.get('access_token')}"})
                    resp_user = session.get("https://api.mercadolibre.com/users/me", timeout=5)
                else:
                    self.send_response(401)
                    self.send_header("Content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Erro ao atualizar token."}).encode("utf-8"))
                    return

            user_id = resp_user.json().get("id")
            hoje = datetime.now(timezone.utc)
            data_30_dias = (hoje - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000-00:00")
            
            def fetch_orders():
                vendas = {}
                offset = 0
                while True:
                    url = f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid&order.date_created.from={data_30_dias}&offset={offset}&limit=50"
                    resp = session.get(url, timeout=5)
                    if resp.status_code != 200: break
                    dados = resp.json()
                    resultados = dados.get("results", [])
                    if not resultados: break
                    
                    for pedido in resultados:
                        data_criacao_str = pedido.get("date_created", "")[:19]
                        try:
                            data_pedido = datetime.strptime(data_criacao_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                            dias_atras = (hoje - data_pedido).days
                        except:
                            dias_atras = 0
                            data_pedido = datetime.min.replace(tzinfo=timezone.utc)

                        for item in pedido.get("order_items", []):
                            item_id = item.get("item", {}).get("id")
                            qtd = int(item.get("quantity", 0))
                            preco_pago = float(item.get("unit_price", 0.0))
                            
                            if item_id:
                                if item_id not in vendas:
                                    vendas[item_id] = {
                                        "7d": 0, "15d": 0, "30d": 0, 
                                        "ultimo_preco": preco_pago, 
                                        "data_ultimo_pedido": data_pedido
                                    }
                                
                                vendas[item_id]["30d"] += qtd
                                if dias_atras <= 15: vendas[item_id]["15d"] += qtd
                                if dias_atras <= 7: vendas[item_id]["7d"] += qtd

                                if data_pedido > vendas[item_id]["data_ultimo_pedido"]:
                                    vendas[item_id]["ultimo_preco"] = preco_pago
                                    vendas[item_id]["data_ultimo_pedido"] = data_pedido
                                
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
            chunks = [item_ids[i:i + 20] for i in range(0, len(item_ids), 20)]
            
            for chunk in chunks:
                ids_str = ",".join(chunk)
                resp_detalhe = session.get(f"https://api.mercadolibre.com/items?ids={ids_str}", timeout=10)
                if resp_detalhe.status_code != 200: continue
                
                for item_json in resp_detalhe.json():
                    if item_json.get("code") != 200: continue
                    item_data = item_json.get("body", {})
                    
                    item_id = item_data.get("id")
                    titulo = str(item_data.get("title", ""))
                    estoque_full = int(item_data.get("available_quantity", 0))
                    preco_base = float(item_data.get("price", 0.0))
                    
                    # IDENTIFICA SE O ANÚNCIO ESTÁ NO FULL
                    logistic_type = item_data.get("shipping", {}).get("logistic_type", "")
                    is_full = (logistic_type == "fulfillment")
                    
                    vendas_item = vendas.get(item_id, {"7d": 0, "15d": 0, "30d": 0, "ultimo_preco": 0.0})
                    ultimo_preco_venda = vendas_item.get("ultimo_preco", 0.0)

                    if ultimo_preco_venda > 0 and ultimo_preco_venda < preco_base:
                        preco_final = ultimo_preco_venda
                    else:
                        preco_final = preco_base

                    listing_type = item_data.get("listing_type_id", "")
                    comissao_perc = 16.5 if "pro" in listing_type else 11.5

                    relatorio.append({
                        "id": item_id,
                        "titulo": titulo,
                        "estoque": estoque_full,
                        "vendas_7d": vendas_item["7d"],
                        "vendas_15d": vendas_item["15d"],
                        "vendas_30d": vendas_item["30d"],
                        "preco": preco_final,
                        "comissao_ml": comissao_perc,
                        "is_full": is_full
                    })

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
