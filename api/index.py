import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
import json
import requests

ACCESS_TOKEN = (
    "APP_USR-1194661319744999-080206-ba131362d77213fa93130fdbb45f61dd-1327156852"
)

class handler(BaseHTTPRequestHandler):
  def do_GET(self):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "User-Agent": "Mozilla/5.0",
    }

    try:
      resp_user = requests.get(
          "https://api.mercadolibre.com/users/me", headers=headers, timeout=5
      )
      if resp_user.status_code != 200:
        self.send_response(401)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"error": "Token expirado ou inválido"}).encode("utf-8")
        )
        return

      user_id = resp_user.json().get("id")
      
      # DELEGA O FILTRO PARA O MERCADO LIVRE (Pega apenas pedidos dos últimos 7 dias)
      hoje = datetime.utcnow()
      data_7_dias = (hoje - timedelta(days=7)).strftime("%Y-%m-%dT00:00:00.000-00:00")

      vendas = {}
      offset = 0
      
      while offset < 150:
        url_orders = f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid&order.date_created.from={data_7_dias}&offset={offset}&limit=50"
        resp_orders = requests.get(url_orders, headers=headers, timeout=5)
        
        if resp_orders.status_code != 200:
          break
          
        dados_ord = resp_orders.json()
        resultados = dados_ord.get("results", [])
        
        if not resultados:
          break
          
        for pedido in resultados:
          for item in pedido.get("order_items", []):
            item_id = item.get("item", {}).get("id")
            qtd = item.get("quantity", 0)
            if item_id:
              # Soma a quantidade vendida direto na variável
              vendas[item_id] = vendas.get(item_id, 0) + int(qtd)
              
        offset += 50

      # Busca de anúncios
      resp_items = requests.get(
          f"https://api.mercadolibre.com/users/{user_id}/items/search?limit=50",
          headers=headers,
          timeout=5,
      )
      item_ids = resp_items.json().get("results", [])

      relatorio = []
      IDs_com_estoque_misturado = [
          "MLB5579973070",
          "MLB4286968229",
          "MLB4649295965",
          "MLB4652255149",
          "MLB4711530649",
      ]

      for item_id in item_ids:
        resp_detalhe = requests.get(
            f"https://api.mercadolibre.com/items?ids={item_id}",
            headers=headers,
            timeout=3,
        )
        if resp_detalhe.status_code != 200:
          continue
        item_json = resp_detalhe.json()[0]
        if item_json.get("code") != 200:
          continue

        item_data = item_json.get("body", {})
        if item_data.get("shipping", {}).get("logistic_type") != "fulfillment":
          continue

        # Forçando o tipo correto dos dados para os botões do site não quebrarem
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

      self.send_response(200)
      self.send_header("Content-type", "application/json")
      self.send_header("Access-Control-Allow-Origin", "*")
      self.end_headers()
      self.wfile.write(json.dumps(relatorio).encode("utf-8"))

    except Exception as e:
      self.send_response(500)
      self.send_header("Content-type", "application/json")
      self.end_headers()
      self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
