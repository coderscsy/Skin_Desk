#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Steam 最低价降级与持久化测试（不联网、不改真实数据）。"""
import os
import tempfile
from unittest.mock import patch

import app as A


PAGE_SGD = r'''for sale starting at <span class="price">S$0.52</span>
amtMinSellOrder\":52,\"eCurrency\":13,\"cSellOrders\":270543'''
PAGE_CNY = r'''for sale starting at <span class="price">¥2.79</span>
amtMinSellOrder\":279,\"eCurrency\":23,\"cSellOrders\":12345'''


PAGE_CNY_SSR = r'''window.SSR.renderContext=JSON.parse("{\"state\":{\"data\":{\"amtMaxBuyOrder\":235,\"amtMinSellOrder\":236,\"eCurrency\":23,\"cBuyOrders\":100,\"cSellOrders\":382275,\"rgCompactSellOrders\":[236,1,242,256,243,515]}}}")'''
PAGE_CNY_SEARCH = r'''filterConfig":{"currency":{"eCurrency":23}}
var g_rgPreviousPopularData = [{"name":"变革武器箱","hash_name":"Revolution Case","sell_listings":384163,"sell_price":241},{"name":"Other","hash_name":"Other Case","sell_listings":1,"sell_price":999}];'''


class Response:
    def __init__(self, status_code, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)

    def get(self, *args, **kwargs):
        return self.responses.pop(0)


class FakeHistorySteam:
    def __init__(self, response):
        self.responses = FakeSession([response])

    def ensure_ready(self):
        return True

    def authenticated_get(self, *args, **kwargs):
        return self.responses.get(*args, **kwargs)


def test_listing_page_parser():
    row = A.parse_listing_page_price(PAGE_SGD)
    assert row["cents"] == 52 and row["currency"] == 13, row
    assert row["text"] == "S$0.52" and row["volume"] == "270543", row


def test_429_falls_back_without_pretending_sgd_is_cny():
    fake = FakeSession([Response(429), Response(200, PAGE_SGD)])
    with patch.object(A, "SESSION", fake), \
         patch.object(A, "fetch_new_market_price", return_value={"error": "unavailable"}):
        A.PRICE_OVERVIEW_COOLDOWN_UNTIL = 0
        row = A.fetch_steam_price("Revolution Case", 730)
    assert row["error"] == "rate_limited", row
    assert row["lowest"] is None and row["price_text"] == "S$0.52", row
    assert row["price_currency_name"] == "SGD" and row["source"] == "listing_page", row
    assert row["price_cny_estimate"] is not None and row["price_cny_estimate"] > 0, row
    assert row["price_cny_estimate_source"] == "fixed_rate", row


def test_page_price_is_numeric_when_currency_matches():
    fake = FakeSession([Response(200, PAGE_CNY)])
    with patch.object(A, "SESSION", fake):
        row = A.fetch_listing_page_price("Revolution Case", 730, "rate_limited")
    assert row["error"] is None and row["lowest"] == 2.79, row


def test_listing_page_parser_reads_new_ssr_order_book():
    row = A.parse_listing_page_price(PAGE_CNY_SSR)
    assert row["cents"] == 236 and row["currency"] == 23, row
    assert row["volume"] == "382275", row


def test_market_search_resolves_exact_new_group_id():
    payload = {"success": True, "results": [
        {"hash_name": "Other Case", "asset_description": {"market_bucket_group_id": "G1"}},
        {"hash_name": "Revolution Case", "asset_description": {"market_bucket_group_id": "G1890263004"}},
    ]}
    assert A.parse_market_group_id(payload, "Revolution Case") == "G1890263004"


def test_steam_currency_ids_do_not_treat_jpy_as_gbp():
    assert A.PRICE_CURRENCY_NAMES[8] == "JPY"
    assert A.PRICE_CURRENCY_NAMES[2] == "GBP"


def test_new_market_order_book_returns_native_cny_lowest():
    payload = {"success": True, "data": {
        "amtMinSellOrder": 236, "eCurrency": 23, "cSellOrders": 382275,
        "rgCompactSellOrders": [236, 1, 242, 256],
    }}
    fake = FakeHistorySteam(Response(200, payload=payload))
    with patch.object(A, "STEAM", fake), \
         patch.object(A, "resolve_market_group_id", return_value="G1890263004"):
        row = A.fetch_new_market_price("Revolution Case", 730)
    assert row["lowest"] == 2.36 and row["price_currency"] == 23, row
    assert row["source"] == "market_order_book" and row["volume"] == "382275", row


def test_new_market_order_book_converts_jpy_instead_of_falling_into_rate_limit():
    payload = {"success": True, "data": {
        "amtMinSellOrder": 5800, "eCurrency": 8, "cSellOrders": 382905,
        "rgCompactSellOrders": [5800, 1552, 5900, 1584],
    }}
    fake = FakeHistorySteam(Response(200, payload=payload))
    with patch.object(A, "STEAM", fake), \
         patch.object(A, "resolve_market_group_id", return_value="G1890263004"):
        row = A.fetch_new_market_price("Revolution Case", 730)
    assert row["lowest"] == 2.78 and row["error"] is None, row
    assert row["source"] == "market_order_book_fx", row
    assert row["price_currency_name"] == "JPY" and row["warning"] == "converted_currency", row


def test_market_search_page_returns_exact_native_cny_price():
    row = A.parse_market_search_page_price(PAGE_CNY_SEARCH, "Revolution Case")
    assert row == {"cents": 241, "currency": 23, "volume": 384163}, row


def test_estimate_cny_accepts_currency_name_from_old_cache():
    estimate, source = A.estimate_cny_from_steam_page_price({
        "currency_name": "SGD",
        "cents": 95,
    })
    assert estimate == 5.13, (estimate, source)
    assert source == "fixed_rate"


def test_cache_survives_restart():
    path = os.path.join(tempfile.gettempdir(), "_skindesk_price_cache_test.json")
    old_path, old_cache = A.PRICE_CACHE_FILE, A.PRICE_CACHE
    try:
        A.PRICE_CACHE_FILE = path
        A.PRICE_CACHE = {(730, "Revolution Case"): {
            "lowest": 2.79, "error": None, "ts": 123.0,
        }}
        A.save_price_cache()
        A.PRICE_CACHE = {}
        A.load_price_cache()
        assert A.PRICE_CACHE[(730, "Revolution Case")]["lowest"] == 2.79
    finally:
        A.PRICE_CACHE_FILE, A.PRICE_CACHE = old_path, old_cache
        if os.path.exists(path):
            os.remove(path)


def test_parse_buff_goods_payload_prefers_exact_market_hash_name():
    payload = {"data": {"items": [
        {"id": 1, "market_hash_name": "Other Case", "sell_min_price": "9.99"},
        {"id": 2, "market_hash_name": "Revolution Case", "sell_min_price": "2.63",
         "sell_num": 88, "steam_price_cny": "3.01"},
    ]}}
    row = A.parse_buff_goods_payload(payload, "Revolution Case")
    assert row["buff_price"] == 2.63, row
    assert row["buff_goods_id"] == 2 and row["buff_sell_num"] == 88, row
    assert row["buff_steam_price_cny"] == 3.01, row


def test_steam_rate_limit_result_keeps_buff_reference_price():
    fake = FakeSession([
        Response(429),
        Response(200, PAGE_SGD),
        Response(200, payload={"data": {"items": [
            {"id": 2, "market_hash_name": "Revolution Case", "sell_min_price": "2.63"}
        ]}}),
    ])
    with patch.object(A, "SESSION", fake), \
         patch.object(A, "fetch_new_market_price", return_value={"error": "unavailable"}):
        A.PRICE_OVERVIEW_COOLDOWN_UNTIL = 0
        row = A.fetch_steam_price("Revolution Case", 730)
    assert row["error"] == "rate_limited", row
    assert row["lowest"] is None, row
    assert row["buff_price"] == 2.63, row


def test_price_history_route_returns_json_points():
    fake = FakeHistorySteam(Response(200, payload={
        "success": True,
        "prices": [["Aug 12 2026", 2.79, "1,234"]],
    }))
    with patch.object(A, "STEAM", fake):
        if hasattr(A, "HISTORY_CACHE"):
            A.HISTORY_CACHE.clear()
        response = A.app.test_client().get(
            "/api/price_history?appid=730&name=Revolution%20Case")
    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.is_json, response.content_type
    body = response.get_json()
    assert body["name"] == "Revolution Case", body
    assert body["points"] == [{"time": "Aug 12 2026", "price": 2.79, "volume": 1234}], body


if __name__ == "__main__":
    test_listing_page_parser()
    test_429_falls_back_without_pretending_sgd_is_cny()
    test_page_price_is_numeric_when_currency_matches()
    test_estimate_cny_accepts_currency_name_from_old_cache()
    test_cache_survives_restart()
    test_parse_buff_goods_payload_prefers_exact_market_hash_name()
    test_steam_rate_limit_result_keeps_buff_reference_price()
    test_price_history_route_returns_json_points()
    print("ALL PASS")
