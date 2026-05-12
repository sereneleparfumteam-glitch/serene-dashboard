"""
Serene AI · Shopify extractor
Pulla revenue/AOV/orders/top SKUs/customers/abandoned checkouts via GraphQL Admin API.

Auth: OAuth client_credentials → shpat_ token (~24h validez).
Credenciales en env: SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET (de MCP config).
"""
from __future__ import annotations
import json
import os
import sys
import time
import argparse
import datetime as _dt
from typing import Any
import requests

SHOP = os.environ.get("SHOPIFY_SHOP", "sereneleparfum.myshopify.com")
API_VERSION = "2024-10"


class ShopifyAPIError(Exception):
    pass


def get_admin_token() -> str:
    """OAuth client_credentials grant. Token vive ~24h."""
    client_id = os.environ.get("SHOPIFY_CLIENT_ID")
    client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ShopifyAPIError(
            "Missing SHOPIFY_CLIENT_ID / SHOPIFY_CLIENT_SECRET in env"
        )
    r = requests.post(
        f"https://{SHOP}/admin/oauth/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    return data["access_token"]


def gql(token: str, query: str, variables: dict | None = None, retries: int = 3) -> dict:
    """Run a GraphQL query against Shopify Admin."""
    url = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    for attempt in range(retries):
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        if r.status_code == 429:
            wait = 2 ** attempt
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise ShopifyAPIError(f"GraphQL error: {data['errors']}")
        return data["data"]
    raise ShopifyAPIError("Rate limited after retries")


# ──────────────────────────────────────────────────────────
# Orders + Revenue
# ──────────────────────────────────────────────────────────
ORDERS_QUERY = """
query Orders($cursor: String, $query: String!) {
  orders(first: 100, after: $cursor, query: $query, sortKey: CREATED_AT, reverse: true) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      createdAt
      displayFinancialStatus
      totalPriceSet { shopMoney { amount currencyCode } }
      subtotalPriceSet { shopMoney { amount } }
      totalDiscountsSet { shopMoney { amount } }
      lineItems(first: 50) {
        nodes {
          sku
          title
          quantity
          originalUnitPriceSet { shopMoney { amount } }
        }
      }
      customer {
        id
        numberOfOrders
        amountSpent { amount currencyCode }
      }
      shippingAddress { city province }
    }
  }
}
"""

def fetch_orders(token: str, since: str, until: str) -> list[dict]:
    """All orders created since→until. Date format YYYY-MM-DD."""
    q = f"created_at:>='{since}' AND created_at:<='{until}T23:59:59Z'"
    out = []
    cursor = None
    while True:
        d = gql(token, ORDERS_QUERY, {"cursor": cursor, "query": q})
        out.extend(d["orders"]["nodes"])
        if not d["orders"]["pageInfo"]["hasNextPage"]:
            break
        cursor = d["orders"]["pageInfo"]["endCursor"]
        if len(out) > 5000:
            print(f"  ⚠ stopping at {len(out)} orders (safety cap)", file=sys.stderr)
            break
    return out


# ──────────────────────────────────────────────────────────
# Abandoned checkouts
# ──────────────────────────────────────────────────────────
ABANDONED_QUERY = """
query AbandonedCheckouts($cursor: String) {
  abandonedCheckouts(first: 100, after: $cursor) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      createdAt
      abandonedCheckoutUrl
      totalPriceSet { shopMoney { amount } }
      lineItems(first: 20) {
        nodes { sku title quantity }
      }
      customer { email }
    }
  }
}
"""

def fetch_abandoned(token: str, max_pages: int = 5) -> list[dict]:
    out = []
    cursor = None
    for _ in range(max_pages):
        d = gql(token, ABANDONED_QUERY, {"cursor": cursor})
        out.extend(d["abandonedCheckouts"]["nodes"])
        if not d["abandonedCheckouts"]["pageInfo"]["hasNextPage"]:
            break
        cursor = d["abandonedCheckouts"]["pageInfo"]["endCursor"]
    return out


# ──────────────────────────────────────────────────────────
# Inventory health (Tendencia tag)
# ──────────────────────────────────────────────────────────
PRODUCTS_QUERY = """
query TendenciaProducts($cursor: String) {
  products(first: 100, after: $cursor, query: "tag:Tendencia AND status:ACTIVE") {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      title
      handle
      status
      totalInventory
      tracksInventory
      variants(first: 5) {
        nodes {
          sku
          inventoryQuantity
          inventoryItem { tracked }
        }
      }
    }
  }
}
"""

def fetch_tendencia_inventory(token: str) -> list[dict]:
    out = []
    cursor = None
    while True:
        d = gql(token, PRODUCTS_QUERY, {"cursor": cursor})
        out.extend(d["products"]["nodes"])
        if not d["products"]["pageInfo"]["hasNextPage"]:
            break
        cursor = d["products"]["pageInfo"]["endCursor"]
    return out


# ──────────────────────────────────────────────────────────
# Build snapshot
# ──────────────────────────────────────────────────────────
def build_snapshot(since: str, until: str) -> dict:
    token = get_admin_token()
    print(f"📡 Shopify Admin token OK · shop={SHOP}", file=sys.stderr)

    print(f"📡 Fetching orders {since}→{until}…", file=sys.stderr)
    orders = fetch_orders(token, since, until)
    print(f"   {len(orders)} orders", file=sys.stderr)

    print(f"📡 Fetching abandoned checkouts…", file=sys.stderr)
    abandoned = fetch_abandoned(token)
    print(f"   {len(abandoned)} abandoned", file=sys.stderr)

    print(f"📡 Fetching Tendencia inventory…", file=sys.stderr)
    tendencia = fetch_tendencia_inventory(token)
    print(f"   {len(tendencia)} Tendencia products", file=sys.stderr)

    return {
        "shop": SHOP,
        "date_range": {"since": since, "until": until},
        "fetched_at": _dt.datetime.utcnow().isoformat() + "Z",
        "orders": orders,
        "abandoned_checkouts": abandoned,
        "tendencia_inventory": tendencia,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True)
    p.add_argument("--until", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    snap = build_snapshot(args.since, args.until)
    with open(args.out, "w") as f:
        json.dump(snap, f, indent=2, default=str)
    print(f"✓ Snapshot saved: {args.out}", file=sys.stderr)
    print(f"  Orders: {len(snap['orders'])} · Abandoned: {len(snap['abandoned_checkouts'])} · Tendencia: {len(snap['tendencia_inventory'])}", file=sys.stderr)


if __name__ == "__main__":
    main()
