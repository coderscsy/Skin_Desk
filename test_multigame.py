#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多游戏支持的后端测试。用 Flask test_client，不占端口、不碰你正在跑的实例。
把数据文件重定向到临时路径，绝不覆盖你真实的 watchlist.json。
跑：  .venv\\Scripts\\python.exe test_multigame.py
"""
import os
import tempfile

import app as A

# 隔离：别动真实数据 / 别触发任何 Steam 网络写操作（本测试不调上架接口）
A.DATA_FILE = os.path.join(tempfile.gettempdir(), "_mg_test_watchlist.json")
A.ITEMS = []
client = A.app.test_client()


def main():
    # 1) /api/config 带 games 列表
    cfg = client.get("/api/config").get_json()
    games = {g["appid"]: g["name"] for g in cfg.get("games", [])}
    assert games.get(252490) == "Rust", games
    assert games.get(730) == "CS2", games
    print("  ok  /api/config 返回 games（含 Rust 252490 / CS2 730）")

    # 2) 添加 Rust 物品 -> appid/game/到手价 正确
    r = client.post("/api/items", json={
        "name": "Panda Rug", "appid": 252490, "qty": 2,
        "purchase": 100, "listing_price": 120, "fee": 13})
    j = r.get_json()
    assert j["appid"] == 252490 and j["game"] == "Rust", j
    assert j["qty"] == 2, j
    # 买家价 ¥120，按 Steam 整分手续费反算到手，不再直接粗暴乘 (1-fee)
    assert j["listing"] <= 120 and j["listing"] > 119.8, j
    assert j["net"] == 106.2, j
    print("  ok  add Rust 'Panda Rug': Steam fee rounding, listing<=120 net=106.2 qty=2")

    # 直接指定买家看到的上架价格优先于溢价，并按最低 1 分费用反算
    r_price = client.post("/api/items", json={
        "name": "Exact", "purchase": 0.01, "markup": 999, "fee": 15, "listing_price": 0.05})
    exact = r_price.get_json()
    assert exact["listing_target"] == 0.05 and exact["listing"] == 0.05, exact
    assert exact["net"] == 0.03 and exact["fee_amount"] == 0.02, exact
    assert exact["balance_discount"] == 33.33, exact  # 0.01 成本 / 0.03 到手余额

    # 3) 改游戏：Rust -> TF2
    iid = j["id"]
    j2 = client.put(f"/api/items/{iid}", json={"appid": 440}).get_json()
    assert j2["appid"] == 440 and j2["game"] == "TF2", j2
    print("  ok  改游戏到 TF2")

    # 4) 不带 appid 的老物品默认 CS2
    r3 = client.post("/api/items", json={"name": "Snakebite Case", "purchase": 4.35, "markup": 40, "fee": 13})
    j3 = r3.get_json()
    assert j3["appid"] == 730 and j3["game"] == "CS2", j3
    print("  ok  不填游戏默认 CS2(730)")

    # 5) 价格缓存按 (appid, name) 区分：不同游戏同名不串味
    A.PRICE_CACHE[(730, "Foo")] = {"lowest": 1.0, "ts": 9e9, "error": None}
    A.PRICE_CACHE[(252490, "Foo")] = {"lowest": 2.0, "ts": 9e9, "error": None}
    v_cs = A.item_view({"id": "a", "name": "Foo", "appid": 730})
    v_rust = A.item_view({"id": "b", "name": "Foo", "appid": 252490})
    assert v_cs["lowest"] == 1.0 and v_rust["lowest"] == 2.0, (v_cs["lowest"], v_rust["lowest"])
    print("  ok  价格缓存按 (appid,name) 区分，不同游戏同名互不串")

    # 6) helpers
    assert A.item_appid({"appid": 252490}) == 252490
    assert A.item_appid({}) == 730
    assert A.appid_context(753) == 6 and A.appid_context(252490) == 2 and A.appid_context(730) == 2
    print("  ok  item_appid / appid_context")

    if os.path.exists(A.DATA_FILE):
        os.remove(A.DATA_FILE)
    print("ALL PASS")


if __name__ == "__main__":
    print("running multi-game backend tests…")
    main()
