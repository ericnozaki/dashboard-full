import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
import json
import requests

# Cole o seu Access Token atual diretamente entre as aspas abaixo
ACCESS_TOKEN = os.getenv(
    "TG-6a6f14894d48c60001adaf47-1327156852&zx=1785664652148",
    "APP_USR-1194661319744999-080116-d9cf051171f6ea4afa8e6363019a9dcd-1327156852"
)

class handler(BaseHTTPRequestHandler):

  def do_GET(self):
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "User-Agent": "Mozilla/5.0",
    }

    try:
      resp_user = requests.get(
          "https://api.mercadolibre.com/users/me", headers=headers
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
      hoje = datetime.now()
      data_30_dias = (hoje - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00.000-00:00")

      vendas = {}
      offset = 0
      total_pedidos = 1

      while offset < total_pedidos:
        url_orders = f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.status=paid&order.date_created.from={data_30_dias}&offset={offset}"
        resp_orders = requests.get(url_orders, headers=headers)
        if resp_orders.status_code != 200:
          break
        dados_ord = resp_orders.json()
        total_pedidos = dados_ord.get("paging", {}).get("total", 0)

        for pedido in dados_ord.get("results", []):
          data_str = pedido.get("date_created")[:19]
          data_pedido = datetime.strptime(data_str, "%Y-%m-%dT%H:%M:%S")
          dias_atras = (hoje - data_pedido).days

          for item in pedido.get("order_items", []):
            item_id = item.get("item", {}).get("id")
            qtd = item.get("quantity", 0)

            if item_id not in vendas:
              vendas[item_id] = {"7d": 0}

            if dias_atras <= 7:
              vendas[item_id]["7d"] += qtd
        offset += 50

      resp_items = requests.get(
          f"https://api.mercadolibre.com/users/{user_id}/items/search?limit=50",
          headers=headers,
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
            f"https://api.mercadolibre.com/items?ids={item_id}", headers=headers
        )
        if resp_detalhe.status_code != 200:
          continue
        item_json = resp_detalhe.json()[0]
        if item_json.get("code") != 200:
          continue

        item_data = item_json.get("body", {})
        if item_data.get("shipping", {}).get("logistic_type") != "fulfillment":
          continue

        titulo = item_data.get("title", "")
        estoque_full = item_data.get("available_quantity", 0)

        if item_id in IDs_com_estoque_misturado:
          estoque_full = 0

        vendas_item = vendas.get(item_id, {"7d": 0})
        venda_diaria_7d = vendas_item["7d"] / 7.0

        semanas_7d = (estoque_full / (venda_diaria_7d * 7)) if venda_diaria_7d > 0 else 999
        estoque_ideal_60d = venda_diaria_7d * 60
        enviar_60d = int(max(0, round(estoque_ideal_60d - estoque_full)))

        relatorio.append({
            "titulo": titulo,
            "estoque": estoque_full,
            "vendas_7d": vendas_item["7d"],
            "semanas_7d": round(semanas_7d, 1),
            "enviar_60d": enviar_60d,
        })

      self.send_response(200)
      self.send_header("Content-type", "application/json")
      self.end_headers()
      self.wfile.write(json.dumps(relatorio).encode("utf-8"))

    except Exception as e:
      self.send_response(500)
      self.send_header("Content-type", "application/json")
      self.end_headers()
      self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
