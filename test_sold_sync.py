#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Steam 同步后售出/回库状态判定测试（不联网）。"""
import time

import app as A


def test_listed_item_becomes_sold_when_listing_and_inventory_are_zero():
    item = {
        "id": "x", "name": "Revolution Case", "status": "listed",
        "last_listed_count": 2, "listing_price": 3.0, "net": 2.61,
        "profit": 0.91,
    }
    now = 123456.0
    A.apply_steam_sync_state(item, inv_count=0, active_count=0, hidden_count=0, now=now)

    assert item["status"] == "sold"
    assert item["sold_at"] == now
    assert item["sold_listing_price"] == 3.0
    assert item["last_msg"].startswith("Steam 同步：挂单已消失且库存为 0")


def test_listed_item_returns_to_watching_when_listing_zero_but_inventory_exists():
    item = {"id": "x", "name": "Revolution Case", "status": "listed", "last_listed_count": 2}

    A.apply_steam_sync_state(item, inv_count=2, active_count=0, hidden_count=0, now=time.time())

    assert item["status"] == "watching"
    assert item.get("sold_at") is None
    assert item["operation_qty"] == 2


def test_pending_item_does_not_become_sold_while_hidden_pending_exists():
    item = {"id": "x", "name": "Revolution Case", "status": "pending", "last_listed_count": 2}

    A.apply_steam_sync_state(item, inv_count=0, active_count=0, hidden_count=2, now=time.time())

    assert item["status"] == "pending"
    assert item.get("sold_at") is None


def test_sold_item_stays_readonly_even_if_same_name_inventory_appears_later():
    item = {"id": "x", "name": "Revolution Case", "status": "sold",
            "sold_at": 123.0, "last_listed_count": 1}

    A.apply_steam_sync_state(item, inv_count=1, active_count=0, hidden_count=0, now=456.0)

    assert item["status"] == "sold"
    assert item["sold_at"] == 123.0


if __name__ == "__main__":
    test_listed_item_becomes_sold_when_listing_and_inventory_are_zero()
    test_listed_item_returns_to_watching_when_listing_zero_but_inventory_exists()
    test_pending_item_does_not_become_sold_while_hidden_pending_exists()
    test_sold_item_stays_readonly_even_if_same_name_inventory_appears_later()
    print("ALL PASS")
