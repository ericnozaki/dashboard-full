"""
Versão refatorada do endpoint Mercado Livre.
Melhorias:
- Paginação completa de pedidos e anúncios
- Session reutilizada
- Busca de itens em lote
- Retry simples para 429/timeout
- Timezone preservado
- JSON UTF-8
- Relatório ordenado
"""

from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler
from zoneinfo import ZoneInfo
import json
import time
import requests

ACCESS_TOKEN = "COLOQUE_SEU_ACCESS_TOKEN"

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "User-Agent": "EstoqueFull/2.0"
}

session = requests.Session()
session.headers.update(HEADERS)

TZ = ZoneInfo("America/Sao_Paulo")

ESTOQUE_MISTURADO = {
    "MLB5579973070",
    "MLB4286968229",
    "MLB4649295965",
    "MLB4652255149",
    "MLB4711530649",
}


def get(url, params=None, timeout=10):
    for tentativa in range(3):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(2)
                continue
            r.raise_for_status()
            return r
        except requests.RequestException:
            if tentativa == 2:
                raise
            time.sleep(1)


def obter_usuario():
    return get("https://api.mercadolibre.com/users/me").json()["id"]


def obter_vendas(user_id):
    hoje = datetime.now(TZ)
    inicio = (hoje - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00-03:00")

    vendas = {}
    offset = 0

    while True:
        dados = get(
            "https://api.mercadolibre.com/orders/search",
            params={
                "seller": user_id,
                "order.status": "paid",
                "order.date_created.from": inicio,
                "offset": offset,
                "limit": 50,
            },
        ).json()

        total = dados["paging"]["total"]

        for pedido in dados.get("results", []):
            dc = pedido.get("date_created")
            if not dc:
                continue
            data = datetime.fromisoformat(dc.replace("Z", "+00:00")).astimezone(TZ)
            dias = (hoje - data).days

            for item in pedido.get("order_items", []):
                iid = item["item"]["id"]
                vendas.setdefault(iid, {"7d": 0})
                if dias <= 7:
                    vendas[iid]["7d"] += item.get("quantity", 0)

        offset += 50
        if offset >= total:
            break

    return vendas


def obter_itens(user_id):
    ids = []
    offset = 0

    while True:
        dados = get(
            f"https://api.mercadolibre.com/users/{user_id}/items/search",
            params={"offset": offset, "limit": 50},
        ).json()

        ids.extend(dados["results"])

        offset += 50
        if offset >= dados["paging"]["total"]:
            break

    return list(dict.fromkeys(ids))


def detalhes(ids):
    resultado = []
    for i in range(0, len(ids), 20):
        bloco = ",".join(ids[i:i+20])
        resposta = get(
            "https://api.mercadolibre.com/items",
            params={"ids": bloco},
        ).json()

        for item in resposta:
            if item.get("code") == 200:
                resultado.append(item["body"])
    return resultado


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        try:
            user = obter_usuario()
            vendas = obter_vendas(user)
            ids = obter_itens(user)

            relatorio = []

            for item in detalhes(ids):

                if item.get("shipping", {}).get("logistic_type") != "fulfillment":
                    continue

                iid = item["id"]

                estoque = 0 if iid in ESTOQUE_MISTURADO else item.get("available_quantity", 0)

                v7 = vendas.get(iid, {}).get("7d", 0)

                media = v7 / 7 if v7 else 0

                dias = round(estoque / media) if media else None

                enviar = max(0, round(media * 60 - estoque))

                if dias is None:
                    status = "Sem vendas"
                elif dias < 15:
                    status = "CRÍTICO"
                elif dias < 30:
                    status = "ATENÇÃO"
                else:
                    status = "OK"

                relatorio.append({
                    "titulo": item["title"],
                    "estoque": estoque,
                    "vendas_7d": v7,
                    "dias_estoque": dias,
                    "status": status,
                    "enviar_60d": enviar,
                })

            relatorio.sort(key=lambda x: x["enviar_60d"], reverse=True)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps(relatorio, ensure_ascii=False).encode("utf-8")
            )

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                json.dumps({"error": str(e)}, ensure_ascii=False).encode("utf-8")
            )
