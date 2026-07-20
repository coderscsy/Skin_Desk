#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量上架部分失败后的补挂回归测试（不联网、不改真实数据）。"""
from unittest.mock import patch

import app as A


class FakeSteam:
    def __init__(self):
        self.assets = []

    def ensure_ready(self):
        return None

    def create_sell_listing(self, assetid, price_cents, appid, contextid):
        self.assets.append(assetid)
        return {"ok": True, "needs_confirmation": False,
                "listing_ids": [f"listing-{assetid}"]}


def test_existing_listings_do_not_block_remaining_inventory():
    item = {
        "id": "partial", "name": "Revolution Case", "appid": 730,
        "qty": 66, "operation_qty": 2, "purchase": 1.71,
        "listing_price": 2.79, "fee": 15,
        "status": "listed", "steam_listing_count": 64,
        "steam_inventory_count": 2, "steam_pending_count": 0,
    }
    inventory = {"Revolution Case": [
        {"assetid": "remaining-1", "marketable": 1},
        {"assetid": "remaining-2", "marketable": 1},
    ]}
    fake = FakeSteam()
    with patch.object(A, "STEAM", fake), patch.object(A.time, "sleep", return_value=None):
        ok, msg = A.do_list(item, inv=inventory)

    assert ok, msg
    assert fake.assets == ["remaining-1", "remaining-2"], fake.assets
    assert item["last_listed_count"] == 2
    assert item["last_failed_count"] == 0


def test_pending_confirmation_still_blocks_new_listing():
    item = {
        "name": "Revolution Case", "operation_qty": 2,
        "status": "pending", "steam_listing_count": 64,
        "steam_inventory_count": 2, "steam_pending_count": 2,
    }
    fake = FakeSteam()
    with patch.object(A, "STEAM", fake):
        ok, msg = A.do_list(item, inv={"Revolution Case": []})

    assert not ok
    assert "待确认" in msg
    assert fake.assets == []


if __name__ == "__main__":
    test_existing_listings_do_not_block_remaining_inventory()
    test_pending_confirmation_still_blocks_new_listing()
    print("ALL PASS")
