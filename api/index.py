import os
import concurrent.futures
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler
import json
import requests

ACCESS_TOKEN = (
    "APP_USR-1194661319744999-080217-b381f374c3487f33f5e37fd7e073647d-1327156852"
)

class handler(BaseHTTPRequestHandler):
  def do_GET(self):
    # Otimização 1: Reutilização de conexões TCP/IP
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "User-Agent": "Mozilla/5.0"
    })

    try:
      resp_user = session.get("https://api.mercadolibre.com/users/me", timeout=5)
      if resp_user.status_code != 200:
        self.send_response(401)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"error": "Token expirado ou inválido"}).encode("utf-8"))
        return

      user_id = resp_user.json().get("id")
      
      hoje = datetime.now(timezone.utc)
      data_7_dias = (hoje - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000-00:00")

      # Função para Thread 1: Puxar todas as vendas pagas dos últimos 7 dias
      def fetch_orders():
        vendas = {}
        offset = 0
        while True:
          url = f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid&order.date_created.from={data_7_dias}&offset={offset}&limit=50"
          resp = session.get(url, timeout=5)
          if resp.status_code != 200:
            break
          dados = resp.json()
          resultados = dados.get("results", [])
          if not resultados:
            break
            
          for pedido in resultados:
            for item in pedido.get("order_items", []):
              item_id = item.get("item", {}).get("id")
              qtd = item.get("quantity", 0)
              if item_id:
                vendas[item_id] = vendas.get(item_id, 0) + int(qtd)
                
          offset += 50
          if offset >= dados.get("paging", {}).get("total", 0):
            break
        return vendas

      # Função para Thread 2: Puxar todos os IDs de anúncios ativos
      def fetch_items():
        item_ids = []
        offset = 0
        while True:
          url = f"https://api.mercadolibre.com/users/{user_id}/items/search?limit=50&offset={offset}"
          resp = session.get(url, timeout=5)
          if resp.status_code != 200:
            break
          dados = resp.json()
          resultados = dados.get("results", [])
          if not resultados:
            break
            
          item_ids.extend(resultados)
          offset += 50
          if offset >= dados.get("paging", {}).get("total", 0):
            break
        return list(set(item_ids)) # Remove possíveis IDs duplicados

      # Otimização 2: Executar buscas de I/O em paralelo
      with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_orders = executor.submit(fetch_orders)
        future_items = executor.submit(fetch_items)
        
        vendas = future_orders.result()
        item_ids = future_items.result()

      relatorio = []
      IDs_com_estoque_misturado = []

      # Otimização 3: Consulta de detalhes em lotes de 20 IDs por requisição
      chunks = [item_ids[i:i + 20] for i in range(0, len(item_ids), 20)]
      
      for chunk in chunks:
        ids_str = ",".join(chunk)
        resp_detalhe = session.get(f"https://api.mercadolibre.com/items?ids={ids_str}", timeout=10)
        
        if resp_detalhe.status_code != 200:
          continue
          
        for item_json in resp_detalhe.json():
          if item_json.get("code") != 200:
            continue

          item_data = item_json.get("body", {})
          if item_data.get("shipping", {}).get("logistic_type") != "fulfillment":
            continue

          item_id = item_data.get("id")
          titulo = str(item_data.get("title", ""))
          estoque_full = int(item_data.get("available_quantity", 0))
          
          if item_id in IDs_com_estoque_misturado:
            estoque_full = 0

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
          })

      # Otimização 4: Ordenação direto no backend para aliviar o front
      relatorio.sort(key=lambda x: x["enviar_60d"], reverse=True)

      self.send_response(200)
      # Otimização 5: Cabeçalho com charset para suportar acentuação perfeitamente
      self.send_header("Content-type", "application/json; charset=utf-8")
      self.send_header("Access-Control-Allow-Origin", "*")
      self.end_headers()
      # Garantia de UTF-8 limpo sem caracteres convertidos
      self.wfile.write(json.dumps(relatorio, ensure_ascii=False).encode("utf-8"))

    except Exception as e:
      self.send_response(500)
      self.send_header("Content-type", "application/json")
      self.end_headers()
      self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
