#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
倒货台 (Skin Desk) — 后端
Steam 饰品实时价格监控 / 盈亏计算 / 定时批量上架(可选)

仅本地自用：只监听 127.0.0.1，绝不要绑 0.0.0.0 暴露到公网，
否则别人能通过它操作你的 Steam 账号。

运行：  pip install flask requests
       python3 app.py
       浏览器打开 http://127.0.0.1:8777
"""

import os
import re
import json
import time
import threading
import traceback
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory, Response

from steam_session import SteamSession, NeedAuth

HERE = os.path.dirname(os.path.abspath(__file__))
# 数据目录：默认在代码同目录；Docker/NAS 可用环境变量 SKINDESK_DATA 指到挂载卷里持久化
DATA_DIR = os.environ.get("SKINDESK_DATA") or HERE
os.makedirs(DATA_DIR, exist_ok=True)
DATA_FILE = os.path.join(DATA_DIR, "watchlist.json")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
OPERATIONS_FILE = os.path.join(DATA_DIR, "operations.json")
SECRET_FILE = os.path.join(DATA_DIR, "secret.json")   # 网页里填的账号/密钥（本机、勿外传）
STEAM_LOGIN_FILE = os.path.join(DATA_DIR, "steam_login.json")  # 缓存 Steam 网页登录态（本机、勿外传）

# ------------------- 配置 -------------------
DEFAULT_CONFIG = {
    "currency": 23,            # 23 = 人民币(CNY)
    "appid": 730,             # CS2
    "port": 8777,
    "cache_ttl": 600,         # 价格缓存秒数（Steam priceoverview 很容易限流，默认 10 分钟）
    "auto_list": False,       # True = 定时到点真的上架+自动确认
                              #   ⚠️ 违反 Steam 用户协议、有封号风险，且需令牌密钥
    "steam_api_key": "",      # https://steamcommunity.com/dev/apikey
    "steam_username": "",
    "steam_password": "",
    "steam_proxy": "",       # 加速器/代理地址，例如 http://127.0.0.1:7890；留空走系统网络
    "steam_ssl_verify": True, # False 仅用于会重签 HTTPS 证书的可信本机加速器
    "steamguard_file": "steamguard.json",  # 含 shared_secret/identity_secret 的 maFile
    "enable_trade": False,    # True = 允许“送出/发货”：把物品真的发到某交易链接（比上架更危险，发出即难撤回）
    "web_password": "",       # 设了就启用网页登录密码（联网 / Docker / NAS 访问务必设一个强密码）
    "host": "127.0.0.1",      # 本地用别动；要从手机/别的设备访问设 "0.0.0.0"（且必须同时设 web_password）
}

# 支持的游戏：appid -> {显示名, contextid}。物品默认 CS2，可在网页里按物品选游戏。
GAMES = {
    730:    {"name": "CS2",   "context": 2},
    252490: {"name": "Rust",  "context": 2},
    440:    {"name": "TF2",   "context": 2},
    570:    {"name": "Dota2", "context": 2},
    753:    {"name": "Steam", "context": 6},
}


def item_appid(it):
    try:
        return int(it.get("appid", 730) or 730)
    except Exception:
        return 730


def appid_context(appid):
    return GAMES.get(int(appid), {}).get("context", 2)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    for path in (CONFIG_FILE, SECRET_FILE):   # secret.json 覆盖 config.json
        if os.path.exists(path):
            try:
                cfg.update(json.load(open(path, encoding="utf-8")))
            except Exception as e:
                print(f"{os.path.basename(path)} 读取失败，忽略：", e)
    return cfg


CONFIG = load_config()

SETTINGS = {"my_trade_link": "", "inventory_appid": 730}


def load_settings():
    global SETTINGS
    if os.path.exists(SETTINGS_FILE):
        try:
            SETTINGS.update(json.load(open(SETTINGS_FILE, encoding="utf-8")))
        except Exception as e:
            print("settings 读取失败：", e)


def save_settings():
    try:
        json.dump(SETTINGS, open(SETTINGS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e:
        print("settings 保存失败：", e)


app = Flask(__name__)
LOCK = threading.Lock()
ITEMS = []                # 监控列表
OPERATIONS = []           # 最近操作流水
JOBS = {}                 # 后台批量任务
PRICE_CACHE = {}          # name -> {lowest, median, volume, error, ts}
HISTORY_CACHE = {}        # (appid, name) -> {points, ts, error, cooldown_until}
_steam_client = None      # 复用的 steampy 客户端（仅旧的“送出/发货”用）

STEAM = SteamSession(STEAM_LOGIN_FILE)   # 新的网页会话（登录 + 上架）
STEAM_LOCK = threading.Lock()            # 串行化 Steam 操作，避免并发登录/上架打架
_INV_CACHE = {}                          # appid -> {data, ts}：每个游戏的库存分别短缓存

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
})


def apply_steam_proxy():
    """让价格、登录、库存和上架共用同一条加速器代理线路。"""
    proxy = str(CONFIG.get("steam_proxy") or "").strip()
    if proxy and "://" not in proxy:
        proxy = "http://" + proxy
    proxies = {"http": proxy, "https": proxy} if proxy else {}
    SESSION.proxies.clear()
    STEAM.session.proxies.clear()
    SESSION.proxies.update(proxies)
    STEAM.session.proxies.update(proxies)
    verify = bool(CONFIG.get("steam_ssl_verify", True))
    SESSION.verify = verify
    STEAM.session.verify = verify
    return proxy


apply_steam_proxy()


def friendly_steam_error(e):
    """把 requests/Steam 的底层网络错误翻译成前端能看懂的提示。"""
    msg = str(e)
    low = msg.lower()
    if "certificate_verify_failed" in low or "sslcertverificationerror" in low:
        return ("Steam HTTPS 证书校验失败。通常是本机加速器/代理重签了 steamcommunity.com 证书，"
                "但系统不信任它。处理方式：优先安装代理工具的根证书；或在账号设置里勾选"
                "“兼容本机加速器证书（关闭 HTTPS 证书校验）”后保存再重试。")
    if "connecttimeout" in low or "read timed out" in low or "timed out" in low:
        return ("连接 Steam 超时。请确认加速器已覆盖 Steam 社区/市场，或填写可用的本地 HTTP 代理端口，"
                "例如 http://127.0.0.1:7890。")
    if "connectionreseterror" in low or "10054" in low:
        return ("连接被远程重置。常见于加速器没有接管 Python 程序流量，或 Steam 社区出口不稳定。"
                "建议选择 Steam 社区/市场加速，或改用带本地 HTTP 代理端口的工具。")
    return msg


# ------------------- 持久化 -------------------
def save_items():
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(ITEMS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("保存失败：", e)


def log_operation(action, item=None, **detail):
    row = {"id": f"op-{time.time_ns()}", "ts": time.time(), "action": action,
           "item_id": (item or {}).get("id"), "name": (item or {}).get("name"), **detail}
    OPERATIONS.append(row)
    del OPERATIONS[:-500]
    try:
        json.dump(OPERATIONS, open(OPERATIONS_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception:
        pass
    return row


def load_items():
    global ITEMS, OPERATIONS
    if os.path.exists(DATA_FILE):
        try:
            ITEMS = json.load(open(DATA_FILE, encoding="utf-8"))
        except Exception as e:
            print("读取 watchlist 失败：", e)
            ITEMS = []
    # 兼容旧数据：qty 保留为展示总数，operation_qty 专门表示下次操作数量。
    for it in ITEMS:
        it.setdefault("operation_qty", max(1, int(it.get("qty", 1) or 1)))
    if os.path.exists(OPERATIONS_FILE):
        try:
            OPERATIONS = json.load(open(OPERATIONS_FILE, encoding="utf-8"))[-500:]
        except Exception:
            OPERATIONS = []


# ------------------- 价格 -------------------
def parse_price(s):
    if not s:
        return None
    s = s.replace(",", "")
    m = re.search(r"\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def fetch_steam_price(name, appid=None):
    params = {
        "country": "CN",
        "currency": CONFIG["currency"],
        "appid": int(appid or CONFIG["appid"]),
        "market_hash_name": name,
    }
    try:
        r = SESSION.get("https://steamcommunity.com/market/priceoverview/",
                        params=params, timeout=15)
        if r.status_code == 429:
            return {"error": "rate_limited"}
        if r.status_code != 200:
            return {"error": f"http_{r.status_code}"}
        d = r.json()
        if not d.get("success"):
            return {"error": "no_data"}
        return {
            "lowest": parse_price(d.get("lowest_price")),
            "median": parse_price(d.get("median_price")),
            "volume": d.get("volume"),
            "error": None,
        }
    except Exception as e:
        return {"error": str(e)}


def price_refresher():
    """后台线程：轮流刷新过期价格，单条之间留间隔以防限流。"""
    while True:
        try:
            with LOCK:
                keys = sorted({(item_appid(it), it["name"]) for it in ITEMS})
            for appid, name in keys:
                c = PRICE_CACHE.get((appid, name))
                if (not c) or (time.time() - c.get("ts", 0) > CONFIG["cache_ttl"]):
                    res = fetch_steam_price(name, appid)
                    res["ts"] = time.time()
                    # 如果 Steam 限流/网络失败，但之前有成功价格，保留旧价格用于展示和参考。
                    if res.get("error") and c and c.get("lowest") is not None:
                        keep = dict(c)
                        keep.update({
                            "error": res.get("error"),
                            "ts": res["ts"],
                            "stale": True,
                            "last_success_ts": c.get("last_success_ts") or c.get("ts"),
                        })
                        PRICE_CACHE[(appid, name)] = keep
                    else:
                        if not res.get("error"):
                            res["stale"] = False
                            res["last_success_ts"] = res["ts"]
                        PRICE_CACHE[(appid, name)] = res
                    time.sleep(20)  # Steam 价格接口很敏感，放慢节奏避免 429
        except Exception:
            traceback.print_exc()
        time.sleep(15)


def refresh_prices_once(force=False):
    """手动刷新当前监控表价格；失败时保留旧成功价格。"""
    refreshed = 0
    limited = 0
    errors = 0
    with LOCK:
        keys = sorted({(item_appid(it), it["name"]) for it in ITEMS})
    for appid, name in keys:
        c = PRICE_CACHE.get((appid, name))
        if (not force) and c and (time.time() - c.get("ts", 0) <= CONFIG["cache_ttl"]):
            continue
        res = fetch_steam_price(name, appid)
        res["ts"] = time.time()
        if res.get("error") == "rate_limited":
            limited += 1
        elif res.get("error"):
            errors += 1
        else:
            refreshed += 1
        if res.get("error") and c and c.get("lowest") is not None:
            keep = dict(c)
            keep.update({
                "error": res.get("error"),
                "ts": res["ts"],
                "stale": True,
                "last_success_ts": c.get("last_success_ts") or c.get("ts"),
            })
            PRICE_CACHE[(appid, name)] = keep
        else:
            if not res.get("error"):
                res["stale"] = False
                res["last_success_ts"] = res["ts"]
            PRICE_CACHE[(appid, name)] = res
        time.sleep(20)
    return {"refreshed": refreshed, "limited": limited, "errors": errors, "total": len(keys)}


# ------------------- 计算 -------------------
def steam_fees_cents(net_cents, fee_percent=15):
    """按 Steam 实际的整分取整和最低 1 分规则计算费用。"""
    net_cents = max(0, int(net_cents))
    total_rate = max(0.0, float(fee_percent or 0))
    steam_rate = min(5.0, total_rate)
    game_rate = max(0.0, total_rate - steam_rate)
    steam_fee = max(1, int(net_cents * steam_rate / 100)) if steam_rate else 0
    game_fee = max(1, int(net_cents * game_rate / 100)) if game_rate else 0
    return steam_fee + game_fee


def net_from_buyer_cents(buyer_cents, fee_percent=15):
    """由买家看到的总价反算 sellitem 接口需要的卖家到手金额。"""
    buyer_cents = max(0, int(buyer_cents))
    for net in range(buyer_cents, -1, -1):
        if net + steam_fees_cents(net, fee_percent) <= buyer_cents:
            return net
    return 0


def sync_item_pricing(it, changed=None):
    """保持购入价、上架价、余额折扣三者联动。折扣=购入成本/Steam到手余额。"""
    if changed == "markup":
        it["listing_price"] = 0
        return
    try:
        buy = float(it.get("purchase", 0) or 0)
        listing = float(it.get("listing_price", 0) or 0)
    except (TypeError, ValueError):
        return
    if buy > 0 and listing > 0:
        fee = float(it.get("fee", 15) or 0)
        net = net_from_buyer_cents(round(listing * 100), fee) / 100
        if net > 0:
            it["markup"] = round(buy / net * 100, 2)


def compute(it):
    buy = float(it.get("purchase", 0) or 0)
    markup = float(it.get("markup", 0) or 0)
    fee = float(it.get("fee", 15) or 0)
    requested = float(it.get("listing_price", 0) or 0)
    if requested > 0:
        target_cents = max(0, round(requested * 100))
        net_cents = net_from_buyer_cents(target_cents, fee)
    elif buy > 0 and markup > 0:
        # 已知现金成本和余额折扣，先反推期望到手余额，再加 Steam 手续费。
        net_cents = max(0, round(buy * 10000 / markup))
        target_cents = net_cents + steam_fees_cents(net_cents, fee)
    else:
        target_cents = max(0, round(buy * 100))
        net_cents = net_from_buyer_cents(target_cents, fee)
    actual_cents = net_cents + steam_fees_cents(net_cents, fee) if net_cents else 0
    listing = actual_cents / 100                       # Steam 买家实际看到的价格
    net = net_cents / 100                              # sellitem 的 price 参数/实际到手
    profit = round(net - buy, 2)
    profit_pct = round(profit / buy * 100, 2) if buy else None
    balance_discount = round(buy / net * 100, 2) if buy and net else 0
    return {"listing": listing, "listing_target": target_cents / 100, "net": net,
            "fee_amount": round(listing - net, 2), "balance_discount": balance_discount,
            "profit": profit, "profit_pct": profit_pct}


def item_view(it):
    appid = item_appid(it)
    pc = PRICE_CACHE.get((appid, it["name"]), {})
    return {
        **it,
        **compute(it),
        "appid": appid,
        "game": GAMES.get(appid, {}).get("name", str(appid)),
        "lowest": pc.get("lowest"),
        "median": pc.get("median"),
        "volume": pc.get("volume"),
        "price_error": pc.get("error"),
        "price_ts": pc.get("ts"),
        "price_stale": bool(pc.get("stale")),
        "price_last_success_ts": pc.get("last_success_ts"),
    }


# ------------------- steampy 自动上架 -------------------
def get_steam_client():
    global _steam_client
    if _steam_client is not None:
        return _steam_client
    from steampy.client import SteamClient
    c = SteamClient(CONFIG["steam_api_key"])
    c.login(CONFIG["steam_username"], CONFIG["steam_password"],
            os.path.join(HERE, CONFIG["steamguard_file"]))
    _steam_client = c
    return c


def get_inventory(appid=730, contextid=None, force=False):
    """拉某个游戏的库存并短缓存（60s），按 appid 分别缓存。返回 {name: [{'assetid','marketable'}]}。"""
    appid = int(appid)
    if contextid is None:
        contextid = appid_context(appid)
    c = _INV_CACHE.get(appid)
    if not force and c and time.time() - c["ts"] < 60:
        return c["data"]
    inv = STEAM.fetch_inventory(appid, contextid)
    _INV_CACHE[appid] = {"data": inv, "ts": time.time()}
    return inv


def do_list(it, inv=None, used=None, force=False):
    """把一个物品按“到手价”挂到 Steam 市场；不自动确认（由你手机令牌手动确认）。
    这是显式上架（手动点/批量点），不看 auto_list 开关。返回 (ok, message)。"""
    it["last_listed_count"] = 0
    it["last_failed_count"] = 0
    if it.get("status") in ("listed", "pending") or int(it.get("steam_listing_count", 0) or 0) > 0:
        return False, "该物品已挂出或正在等待确认，请先下架并同步 Steam 后再重新上架"
    net = compute(it)["net"]
    if net <= 0:
        return False, "上架价为 0，请直接填写买家看到的上架价格"
    appid = item_appid(it)
    ctx = appid_context(appid)
    gname = GAMES.get(appid, {}).get("name", str(appid))
    try:
        STEAM.ensure_ready()
    except NeedAuth as e:
        return False, f"未登录 Steam：{e}（去顶部「Steam 登录」面板登录）"
    if inv is None:
        try:
            inv = get_inventory(appid, ctx, force=force)
        except NeedAuth as e:
            return False, f"未登录 Steam：{e}"
        except Exception as e:
            return False, f"拉 {gname} 库存失败：{e}"
    used = used if used is not None else set()
    try:
        qty = max(1, int(it.get("operation_qty", it.get("qty", 1)) or 1))
    except Exception:
        qty = 1
    it["last_failed_count"] = qty
    all_copies = [c for c in (inv.get(it["name"]) or []) if c["assetid"] not in used]
    cands = [c["assetid"] for c in all_copies if c.get("marketable", 1)]
    if not cands:
        if all_copies:   # 在库存里、但都不可上架
            return False, "这件现在不可上架（受保护/交易保护期，或该物品不可在市场出售）；等可上架了再挂。"
        return False, f"{gname} 库存里没有这件（名字对不上 / 不在库存 / 已挂 / 已售）"
    cents = round(net * 100)
    attempt_qty = min(qty, len(cands))  # 数量可表示 Steam 总数，实际只提交当前库存中可上架的件数
    it["last_failed_count"] = attempt_qty
    ok_n = 0
    need_confirm = False
    errs = []
    listing_ids = []
    for asset in cands[:attempt_qty]:
        res = STEAM.create_sell_listing(asset, cents, appid, ctx)
        if res.get("ok"):
            used.add(asset)
            ok_n += 1
            if res.get("needs_confirmation"):
                need_confirm = True
            listing_ids.extend(res.get("listing_ids") or [])
        else:
            errs.append(res.get("msg", "失败"))
        time.sleep(0.8)              # 限速，别触发风控
    remaining = max(0, attempt_qty - ok_n)
    it["last_listed_count"] = ok_n
    it["last_failed_count"] = remaining
    if ok_n == 0:
        return False, "上架失败：" + ("；".join(dict.fromkeys(errs)) or "未知")
    it["needs_confirm"] = need_confirm    # 给调用方决定状态是“待确认”还是“已挂出”
    it["pending_listing_ids"] = list(dict.fromkeys(listing_ids))
    it["last_list_attempt_ts"] = time.time()
    tip = f"挂了 {ok_n}/{attempt_qty} 件（到手 ¥{net}/件）"
    if len(cands) < qty:
        tip += f"；库存只有 {len(cands)} 件可挂"
    if need_confirm:
        tip += "；去手机令牌逐笔确认"
    if errs:
        tip += "；部分失败：" + "；".join(dict.fromkeys(errs))[:60]
    if remaining:
        # 聚合行只保留没挂成功的数量，下次点上架只重试失败部分。
        it["operation_qty"] = remaining
        tip = f"成功 {ok_n} 件，剩余 {remaining} 件未上架；数量已改为 {remaining}，可直接重试"
        if errs:
            tip += "。原因：" + "；".join(dict.fromkeys(errs))[:80]
        return False, tip
    return True, tip


TRADE_RE = re.compile(r"^https://steamcommunity\.com/tradeoffer/new/\?partner=(\d+)&token=([\w-]+)$")


def parse_trade_url(url):
    m = TRADE_RE.match((url or "").strip())
    return (m.group(1), m.group(2)) if m else (None, None)


def do_send(it):
    """通过交易链接把物品发出去（送出/发货），自动确认。返回 (ok, message)。"""
    url = (it.get("trade_url") or "").strip()
    partner, _ = parse_trade_url(url)
    if not partner:
        return False, "交易链接格式不对（应为 steamcommunity.com/tradeoffer/new/?partner=..&token=..）"
    if not CONFIG.get("enable_trade"):
        return False, f"enable_trade 未开启（已校验 partner={partner}，但没真的发出）"
    try:
        from steampy.models import Asset
        try:
            from steampy.models import GameOptions
        except ImportError:
            from steampy.utils import GameOptions
        c = get_steam_client()
        inv = c.get_my_inventory(GameOptions.CS)
        asset_id = None
        for aid, x in inv.items():
            if x.get("market_hash_name") == it["name"]:
                asset_id = aid
                break
        if not asset_id:
            return False, "库存里没找到该物品（可能交易保护中/已售/已发）"
        resp = c.make_offer_with_url([Asset(asset_id, GameOptions.CS)], [], url, message="")
        return True, f"已发出报价给 partner={partner}（{resp}）"
    except Exception as e:
        return False, f"发货失败：{e}"


def scheduler():
    """后台线程：到点的 scheduled 物品自动上架/提醒。"""
    while True:
        try:
            now = datetime.now(timezone.utc).timestamp()
            due = []
            with LOCK:
                for it in ITEMS:
                    if it.get("status") == "scheduled" and it.get("sell_at") and it["sell_at"] <= now:
                        due.append(it)
            for it in due:
                if not CONFIG.get("auto_list"):
                    with LOCK:
                        it["status"] = "due"
                        it["last_msg"] = "到点待挂（自动上架未开启，手动点「上架」或「批量上架」）"
                        save_items()
                    print(f"[定时] {it['name']}: 到点待挂（auto_list 未开）")
                    continue
                with STEAM_LOCK:
                    ok, msg = do_list(it)
                with LOCK:
                    it["status"] = "listed" if ok else "error"
                    it["last_msg"] = msg
                    save_items()
                print(f"[定时] {it['name']}: {msg}")
        except Exception:
            traceback.print_exc()
        time.sleep(5)


# ------------------- API -------------------
@app.before_request
def _auth_gate():
    """设了 web_password 就用 HTTP Basic 保护所有页面（联网/Docker 访问的安全闸）。
    没设密码 = 不启用（本地 127.0.0.1 自用，向后兼容）。"""
    pw = CONFIG.get("web_password") or ""
    if not pw:
        return None
    auth = request.authorization
    if auth and (auth.password == pw or auth.username == pw):
        return None
    return Response("需要登录\n", 401, {"WWW-Authenticate": 'Basic realm="Skin Desk"'})


@app.route("/")
def index():
    # 不缓存网页：改了前端，刷新就一定拿到最新版（避免“页面没更新”）
    resp = send_from_directory(HERE, "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/api/config")
def api_config():
    return jsonify({"currency": CONFIG["currency"], "auto_list": CONFIG["auto_list"],
                    "enable_trade": CONFIG.get("enable_trade", False),
                    "games": [{"appid": a, "name": g["name"]} for a, g in GAMES.items()]})


@app.route("/api/items", methods=["GET"])
def api_get_items():
    with LOCK:
        return jsonify([item_view(it) for it in ITEMS])


@app.route("/api/prices/refresh", methods=["POST"])
def api_refresh_prices():
    """手动刷新 Steam 最低价；避免后台频繁访问 priceoverview 导致 429。"""
    try:
        force = bool((request.get_json(silent=True) or {}).get("force", True))
        result = refresh_prices_once(force=force)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": friendly_steam_error(e)}), 502


@app.route("/api/price_history")
def api_price_history():
    """读取 Steam 官方市场价格历史：中位售价 + 成交量。"""
    name = (request.args.get("name") or "").strip()
    try:
        appid = int(request.args.get("appid", CONFIG["appid"]))
    except Exception:
        appid = CONFIG["appid"]
    if not name:
        return jsonify({"error": "missing_name"}), 400
    key = (appid, name)
    now = time.time()
    cached = HISTORY_CACHE.get(key) or {}
    # 成功数据 30 分钟内直接复用；Steam 历史接口比最低价更容易限流。
    if cached.get("points") and now - cached.get("ts", 0) < 1800:
        return jsonify({"appid": appid, "name": name, "points": cached["points"],
                        "cached": True, "cache_age": int(now - cached.get("ts", now))})
    cooldown_until = cached.get("cooldown_until", 0) or 0
    if cooldown_until > now:
        retry_after = int(cooldown_until - now)
        if cached.get("points"):
            return jsonify({"appid": appid, "name": name, "points": cached["points"],
                            "cached": True, "stale": True,
                            "cache_age": int(now - cached.get("ts", now)),
                            "warning": f"Steam 价格历史接口限流中，显示缓存；约 {max(1, retry_after // 60)} 分钟后再试"})
        return jsonify({"error": f"Steam 价格历史接口仍在限流冷却中，约 {max(1, retry_after // 60)} 分钟后再试",
                        "retry_after": retry_after}), 429
    try:
        with STEAM_LOCK:
            STEAM.ensure_ready()
            r = STEAM.session.get("https://steamcommunity.com/market/pricehistory/",
                                  params={"appid": appid, "market_hash_name": name},
                                  headers={"Referer": f"https://steamcommunity.com/market/listings/{appid}/{quote(name, safe='')}"},
                                  timeout=25)
            if r.status_code in (401, 403):
                raise NeedAuth("Steam 会话已失效，请重新登录")
            if r.status_code == 429:
                HISTORY_CACHE[key] = {**cached, "cooldown_until": now + 1800}
                if cached.get("points"):
                    return jsonify({"appid": appid, "name": name, "points": cached["points"],
                                    "cached": True, "stale": True,
                                    "cache_age": int(now - cached.get("ts", now)),
                                    "warning": "Steam 价格历史接口限流中，显示上一次缓存"}), 200
                return jsonify({"error": "Steam 价格历史接口限流中，已进入 30 分钟冷却；现在继续点只会延长限流",
                                "retry_after": 1800}), 429
            if r.status_code != 200:
                return jsonify({"error": f"Steam 价格历史接口 HTTP {r.status_code}"}), 502
            try:
                data = r.json()
            except ValueError:
                return jsonify({"error": "Steam 返回了网页内容而不是价格历史数据，请确认 Steam 会话有效后重试"}), 502
    except NeedAuth:
        return jsonify({"error": "need_auth"}), 401
    except Exception as e:
        return jsonify({"error": friendly_steam_error(e)}), 502
    if not data.get("success"):
        return jsonify({"error": "Steam 没有返回价格历史，可能是物品名不匹配或市场暂不可用"}), 502
    points = []
    for row in data.get("prices") or []:
        if len(row) < 3:
            continue
        try:
            price = float(row[1])
        except (TypeError, ValueError):
            continue
        try:
            volume = int(str(row[2]).replace(",", ""))
        except (TypeError, ValueError):
            volume = 0
        points.append({"time": str(row[0]), "price": price, "volume": volume})
    HISTORY_CACHE[key] = {"points": points, "ts": time.time(), "cooldown_until": 0}
    return jsonify({"appid": appid, "name": name, "points": points})


@app.route("/api/inventory")
def api_inventory():
    """拉当前登录用户某游戏的库存（聚合成可勾选的列表）。浏览用——公开库存不需有效会话。"""
    try:
        appid = int(request.args.get("appid", 730))
    except Exception:
        appid = 730
    ctx = appid_context(appid)
    gname = GAMES.get(appid, {}).get("name", str(appid))
    if not STEAM.steamid:
        return jsonify({"error": "先登录 Steam（至少登录一次拿到 steamid，才能拉你的库存）"}), 401
    with STEAM_LOCK:
        try:
            items = STEAM.fetch_inventory_items(appid, ctx)
        except NeedAuth:
            return jsonify({"error": "need_auth"}), 401
        except Exception as e:
            return jsonify({"error": f"拉 {gname} 库存失败：{e}"}), 502
        try:
            valid = STEAM.check_session()   # 会话失效时库存只返回公开部分，会缺东西
        except Exception:
            valid = True
    return jsonify({"appid": appid, "game": gname, "count": len(items),
                    "items": items, "session_valid": valid})


@app.route("/api/items", methods=["POST"])
def api_add_item():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    try:
        qty = max(1, int(float(body.get("qty", 1) or 1)))
    except Exception:
        qty = 1
    try:
        appid = int(body.get("appid", 730) or 730)
    except Exception:
        appid = 730
    it = {
        "id": str(int(time.time() * 1000)),
        "name": name,
        "appid": appid,                   # 游戏：CS2=730 / Rust=252490 / TF2=440 / Dota2=570 ...
        "qty": qty,                       # 上架数量（每件挂 qty 个单）
        "operation_qty": qty,             # 本次操作数量；与 Steam 实际总数分离
        "purchase": float(body.get("purchase", 0) or 0),
        "markup": float(body.get("markup", 0) or 0),
        "fee": float(body.get("fee", 15) or 15),
        "listing_price": float(body.get("listing_price", 0) or 0),
        "sell_at": body.get("sell_at"),  # unix 秒 或 None
        "trade_url": (body.get("trade_url") or ""),
        "status": "scheduled" if body.get("sell_at") else "watching",
        "last_msg": "",
    }
    sync_item_pricing(it)
    with LOCK:
        ITEMS.append(it)
        save_items()
    return jsonify(item_view(it))


@app.route("/api/items/<iid>", methods=["PUT"])
def api_update_item(iid):
    body = request.get_json(force=True)
    with LOCK:
        for it in ITEMS:
            if it["id"] == iid:
                for k in ("name", "purchase", "markup", "fee", "listing_price", "sell_at", "status", "trade_url", "qty", "operation_qty", "appid"):
                    if k in body:
                        it[k] = body[k]
                if "qty" in body:
                    try:
                        it["qty"] = max(1, int(float(it.get("qty") or 1)))
                    except Exception:
                        it["qty"] = 1
                if "operation_qty" in body:
                    try:
                        it["operation_qty"] = max(1, int(float(it.get("operation_qty") or 1)))
                    except Exception:
                        it["operation_qty"] = 1
                if "appid" in body:
                    try:
                        it["appid"] = int(float(it.get("appid") or 730))
                    except Exception:
                        it["appid"] = 730
                if "listing_price" in body:
                    try:
                        it["listing_price"] = max(0, round(float(it.get("listing_price") or 0), 2))
                    except (TypeError, ValueError):
                        it["listing_price"] = 0
                if "listing_price" in body or "purchase" in body:
                    sync_item_pricing(it)
                elif "markup" in body:
                    sync_item_pricing(it, "markup")
                if it.get("sell_at") and it.get("status") in ("watching", "due", "error"):
                    it["status"] = "scheduled"
                if not it.get("sell_at") and it.get("status") == "scheduled":
                    it["status"] = "watching"
                save_items()
                return jsonify(item_view(it))
    return jsonify({"error": "not found"}), 404


@app.route("/api/items/<iid>", methods=["DELETE"])
def api_delete_item(iid):
    with LOCK:
        before = len(ITEMS)
        ITEMS[:] = [x for x in ITEMS if x["id"] != iid]
        save_items()
    return jsonify({"deleted": before - len(ITEMS)})


@app.route("/api/items/<iid>/list_now", methods=["POST"])
def api_list_now(iid):
    with LOCK:
        it = next((x for x in ITEMS if x["id"] == iid), None)
    if not it:
        return jsonify({"error": "not found"}), 404
    with STEAM_LOCK:
        ok, msg = do_list(it, force=True)
    with LOCK:
        it["status"] = ("pending" if it.get("needs_confirm") else "listed") if ok else "error"
        it["last_msg"] = msg
        save_items()
    return jsonify({"ok": ok, "msg": msg, "item": item_view(it),
                    "listed_count": int(it.get("last_listed_count", 0) or 0),
                    "remaining_count": int(it.get("last_failed_count", 0) or 0)})


@app.route("/api/list_batch", methods=["POST"])
def api_list_batch():
    """批量上架：传 {ids:[...]}。一次性拉库存，逐个按到手价挂单（不自动确认）。"""
    body = request.get_json(force=True)
    ids = body.get("ids") or []
    job_id = body.get("job_id")
    with LOCK:
        targets = [it for it in ITEMS if it["id"] in ids]
    if not targets:
        return jsonify({"error": "没有选中的物品"}), 400
    with STEAM_LOCK:
        try:
            STEAM.ensure_ready()
        except NeedAuth:
            return jsonify({"error": "need_auth"}), 401
        # 按游戏分别预热库存（每个 appid 拉一次新的）
        for appid in {item_appid(it) for it in targets}:
            try:
                get_inventory(appid, appid_context(appid), force=True)
            except NeedAuth:
                return jsonify({"error": "need_auth"}), 401
            except Exception:
                pass   # 某个游戏拉失败，下面 do_list 会就那条报错
        used = set()
        results = []
        for it in targets:
            ok, msg = do_list(it, used=used)   # do_list 用各自游戏已预热的库存缓存
            with LOCK:
                it["status"] = "listed" if ok else "error"
                it["last_msg"] = msg
                save_items()
            results.append({"id": it["id"], "name": it["name"],
                            "net": compute(it)["net"], "ok": ok, "msg": msg,
                            "listed_count": int(it.get("last_listed_count", 0) or 0),
                            "remaining_count": int(it.get("last_failed_count", 0) or 0)})
            if job_id in JOBS:
                JOBS[job_id]["progress"] = len(results)
            time.sleep(1)   # 温柔点，别触发风控
    ok_n = sum(1 for r in results if r["ok"])
    return jsonify({"results": results, "ok_count": ok_n, "total": len(results),
                    "listed_items": sum(r["listed_count"] for r in results),
                    "remaining_items": sum(r["remaining_count"] for r in results)})


@app.route("/api/listings_status", methods=["POST"])
def api_listings_status():
    """用库存核对挂单：取消上架后物品会回到库存。
    某件又出现在库存(可上架)=已取消/没挂出 → 状态改回『盯价中』；不在库存=保持『已挂出』。
    单件场景准；你若本来就拥有多件同名物品会有歧义（提示里说明）。"""
    with LOCK:
        targets = [it for it in ITEMS if it.get("status") in ("listed", "pending")]
    if not targets:
        return jsonify({"checked": 0, "back": 0, "msg": "没有要核对的（只核对『已挂出/待确认』的）"})
    with STEAM_LOCK:
        try:
            STEAM.ensure_ready()
        except NeedAuth:
            return jsonify({"error": "need_auth"}), 401
        invs = {}
        try:
            market_listings = STEAM.fetch_my_listings()
        except Exception:
            market_listings = []
        for appid in {item_appid(it) for it in targets}:
            try:
                invs[appid] = STEAM.fetch_inventory(appid, appid_context(appid))
            except Exception:
                invs[appid] = None
        checked = back = confirmed_total = failed_total = 0
        for it in targets:
            inv = invs.get(item_appid(it))
            if inv is None:
                continue
            checked += 1
            copies = [c for c in (inv.get(it["name"]) or []) if c.get("marketable", 1)]
            active = sum(1 for x in market_listings
                         if x["name"] == it["name"] and x["appid"] == item_appid(it))
            submitted = max(1, int(it.get("last_listed_count", it.get("qty", 1)) or 1))
            with LOCK:
                if it.get("status") == "pending" and active >= submitted:
                    it["status"] = "listed"
                    it["needs_confirm"] = False
                    it["last_msg"] = f"确认完成：{submitted} 件已正式上架"
                    confirmed_total += submitted
                elif it.get("status") == "pending" and copies and active + len(copies) >= submitted:
                    remaining = max(1, submitted - min(active, submitted))
                    it["operation_qty"] = remaining
                    it["status"] = "error"
                    it["needs_confirm"] = False
                    it["last_failed_count"] = remaining
                    it["last_msg"] = f"手机确认结果：成功 {min(active, submitted)} 件，失败回库 {remaining} 件；数量已改为 {remaining}，可直接重新上架"
                    confirmed_total += min(active, submitted)
                    failed_total += remaining
                    back += remaining
                elif copies:                 # 正式挂单取消后回库存
                    it["status"] = "watching"
                    it["last_msg"] = f"核对：发现 {len(copies)} 件回到库存（已取消/未挂出）"
                    back += len(copies)
                else:
                    it["last_msg"] = f"核对：正式挂单 {active} 件，仍可能有确认结果尚未同步"
                save_items()
    return jsonify({"checked": checked, "back": back,
                    "confirmed": confirmed_total, "failed": failed_total})


@app.route("/api/delist_batch", methods=["POST"])
def api_delist_batch():
    """撤下所选监控项对应的全部正式市场挂单。"""
    body = request.get_json(force=True) or {}
    ids = body.get("ids") or []
    job_id = body.get("job_id")
    with LOCK:
        targets = [it for it in ITEMS if it.get("id") in ids]
    if not targets:
        return jsonify({"error": "没有选中物品"}), 400
    with STEAM_LOCK:
        try:
            listings = STEAM.fetch_my_listings()
        except NeedAuth:
            return jsonify({"error": "need_auth"}), 401
        except Exception as e:
            return jsonify({"error": friendly_steam_error(e)}), 502
        results = []
        for it in targets:
            matches = [x for x in listings if x["name"] == it["name"] and x["appid"] == item_appid(it)]
            removed = 0
            errors = []
            for listing in matches:
                try:
                    res = STEAM.remove_listing(listing["listingid"])
                except Exception as e:
                    res = {"ok": False, "msg": f"撤单异常：{str(e)[:120]}"}
                if res.get("ok"):
                    removed += 1
                    listings.remove(listing)
                else:
                    errors.append(res.get("msg", "撤单失败"))
                time.sleep(0.35)
                if job_id in JOBS:
                    JOBS[job_id]["progress"] = JOBS[job_id].get("progress", 0) + 1
            msg = f"已下架 {removed} 件"
            if not matches:
                msg = "未找到正式在售挂单（可能仍在待确认、已售出或已撤下）"
            elif errors:
                msg += "；部分失败：" + "；".join(dict.fromkeys(errors))[:80]
            with LOCK:
                it["status"] = "watching" if removed else it.get("status", "watching")
                it["last_msg"] = msg
            results.append({"id": it["id"], "name": it["name"], "found": len(matches),
                            "removed": removed, "msg": msg})
        with LOCK:
            save_items()
        _INV_CACHE.clear()
    return jsonify({"removed": sum(x["removed"] for x in results), "results": results})


@app.route("/api/delist_preview", methods=["POST"])
def api_delist_preview():
    """只读预览：以 Steam 实际正式挂单为准，不使用库存/监控数量猜测。"""
    ids = (request.get_json(force=True) or {}).get("ids") or []
    with LOCK:
        targets = [it for it in ITEMS if it.get("id") in ids]
    if not targets:
        return jsonify({"error": "没有选中物品"}), 400
    try:
        with STEAM_LOCK:
            listings = STEAM.fetch_my_listings()
    except NeedAuth:
        return jsonify({"error": "need_auth"}), 401
    except Exception as e:
        return jsonify({"error": friendly_steam_error(e)}), 502
    rows = []
    for it in targets:
        count = sum(1 for x in listings if x["name"] == it["name"] and x["appid"] == item_appid(it))
        rows.append({"id": it["id"], "name": it["name"], "count": count})
    return jsonify({"count": sum(x["count"] for x in rows), "rows": rows})


@app.route("/api/cleanup_pending_batch", methods=["POST"])
def api_cleanup_pending_batch():
    """清理由 Steam 返回、但未出现在正式挂单列表中的被吞待确认记录。"""
    ids = (request.get_json(force=True) or {}).get("ids") or []
    with LOCK:
        targets = [it for it in ITEMS if it.get("id") in ids]
    if not targets:
        return jsonify({"error": "没有选中物品"}), 400
    try:
        with STEAM_LOCK:
            hidden = STEAM.fetch_hidden_pending_listings()
            matches = [x for x in hidden if any(x["name"] == it["name"] and x["appid"] == item_appid(it) for it in targets)]
            removed, errors = 0, []
            for listing in matches:
                res = STEAM.remove_listing(listing["listingid"])
                if res.get("ok"):
                    removed += 1
                else:
                    errors.append(res.get("msg", "清理失败"))
                time.sleep(0.35)
    except NeedAuth:
        return jsonify({"error": "need_auth"}), 401
    except Exception as e:
        return jsonify({"error": friendly_steam_error(e)}), 502
    _INV_CACHE.clear()
    with LOCK:
        for it in targets:
            if removed:
                it["status"] = "error"
                it["operation_qty"] = removed
                it["needs_confirm"] = False
                it["last_msg"] = f"已清理 {removed} 条被吞的待确认记录；等待物品回库后可重新上架"
        save_items()
    return jsonify({"found": len(matches), "removed": removed,
                    "errors": list(dict.fromkeys(errors))})


@app.route("/api/steam_sync", methods=["POST"])
def api_steam_sync():
    """双向同步：更新已有行，并把 Steam 端存在但本地缺失的物品自动导入。"""
    with LOCK:
        targets = list(ITEMS)
    try:
        with STEAM_LOCK:
            active_rows = STEAM.fetch_my_listings()
            hidden_rows = STEAM.fetch_hidden_pending_listings()
            inventories = {}
            appids = ({int(SETTINGS.get("inventory_appid", 730) or 730)} |
                      {item_appid(it) for it in targets} |
                      {int(x["appid"]) for x in active_rows + hidden_rows})
            for appid in appids:
                try:
                    inventories[appid] = STEAM.fetch_inventory(appid, appid_context(appid))
                except Exception:
                    inventories[appid] = {}  # 单个游戏库存失败不影响挂单和其他游戏同步
    except NeedAuth:
        return jsonify({"error": "need_auth"}), 401
    except Exception as e:
        return jsonify({"error": friendly_steam_error(e)}), 502

    now = time.time()
    result = []
    added = []
    with LOCK:
        existing = {(item_appid(it), it.get("name")): it for it in ITEMS}
        # 自动新增只认正式在架商品；纯库存和隐藏待确认不会擅自加入监控表。
        discovered = {(int(x["appid"]), x["name"]) for x in active_rows}
        for idx, (appid, name) in enumerate(sorted(discovered)):
            if (appid, name) in existing:
                continue
            inv_count = sum(1 for x in (inventories.get(appid, {}).get(name) or []) if x.get("marketable", 1))
            active_count = sum(1 for x in active_rows if x["appid"] == appid and x["name"] == name)
            hidden_count = sum(1 for x in hidden_rows if x["appid"] == appid and x["name"] == name)
            status = "pending" if hidden_count else ("listed" if active_count else "watching")
            it = {
                "id": f"sync-{time.time_ns()}-{idx}", "name": name, "appid": appid,
                "qty": max(1, inv_count + active_count + hidden_count),
                "operation_qty": max(1, inv_count or 1),
                "purchase": 0.0, "markup": 0.0, "fee": 15.0, "listing_price": 0.0,
                "sell_at": None, "trade_url": "", "status": status,
                "last_msg": "由 Steam 同步自动加入",
            }
            ITEMS.append(it)
            targets.append(it)
            existing[(appid, name)] = it
            added.append({"id": it["id"], "name": name, "appid": appid})
        for it in targets:
            appid = item_appid(it)
            name = it["name"]
            inv_count = sum(1 for x in (inventories.get(appid, {}).get(name) or []) if x.get("marketable", 1))
            active_count = sum(1 for x in active_rows if x["appid"] == appid and x["name"] == name)
            hidden_count = sum(1 for x in hidden_rows if x["appid"] == appid and x["name"] == name)
            it["steam_inventory_count"] = inv_count
            it["steam_listing_count"] = active_count
            it["steam_pending_count"] = hidden_count
            it["steam_sync_ts"] = now
            actual_total = inv_count + active_count + hidden_count
            if actual_total > 0:
                it["qty"] = actual_total
            if inv_count > 0:
                it["operation_qty"] = inv_count

            submitted = max(0, int(it.get("last_listed_count", 0) or 0))
            if hidden_count:
                it["status"] = "pending"
                it["last_msg"] = f"Steam 同步：正式在架 {active_count}，隐藏待确认 {hidden_count}，库存 {inv_count}"
            elif submitted and it.get("status") in ("pending", "error") and active_count < submitted and inv_count:
                remaining = min(inv_count, max(1, submitted - active_count))
                it["status"] = "error"
                it["last_failed_count"] = remaining
                it["last_msg"] = f"Steam 同步：成功在架 {active_count}，失败回库 {remaining}；可重新上架"
            elif active_count:
                it["status"] = "listed"
                it["needs_confirm"] = False
                it["last_msg"] = f"Steam 同步：正式在架 {active_count}，库存 {inv_count}"
            elif inv_count and it.get("status") in ("listed", "pending"):
                it["status"] = "watching"
                it["needs_confirm"] = False
                it["last_msg"] = f"Steam 同步：无正式挂单，{inv_count} 件在库存"
            result.append({"id": it["id"], "inventory": inv_count,
                           "listed": active_count, "pending": hidden_count,
                           "status": it.get("status")})
        save_items()
    return jsonify({"synced": len(result), "added": len(added), "added_items": added, "items": result,
                    "inventory": sum(x["inventory"] for x in result),
                    "listed": sum(x["listed"] for x in result),
                    "pending": sum(x["pending"] for x in result)})


def load_guard_credentials():
    path = CONFIG.get("steamguard_file") or "steamguard.json"
    if not os.path.isabs(path):
        path = os.path.join(HERE, path)
    if not os.path.exists(path):
        raise RuntimeError(f"找不到令牌文件：{path}")
    data = json.load(open(path, encoding="utf-8-sig"))
    secret = data.get("identity_secret")
    device_id = data.get("device_id") or data.get("deviceId")
    if not secret or not device_id:
        raise RuntimeError("令牌文件缺少 identity_secret 或 device_id，无法批量确认")
    return secret, device_id


@app.route("/api/confirm_market_batch", methods=["POST"])
def api_confirm_market_batch():
    """只确认本工具本次记录到的市场出售挂单，不确认交易报价。"""
    ids = (request.get_json(force=True) or {}).get("ids") or []
    with LOCK:
        targets = [it for it in ITEMS if it.get("id") in ids]
        listing_ids = [lid for it in targets for lid in (it.get("pending_listing_ids") or [])]
    if not targets:
        return jsonify({"error": "没有选中物品"}), 400
    try:
        identity_secret, device_id = load_guard_credentials()
        with STEAM_LOCK:
            result = STEAM.confirm_market_listings(identity_secret, device_id, listing_ids)
    except NeedAuth:
        return jsonify({"error": "need_auth"}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    if result["confirmed"]:
        with LOCK:
            for it in targets:
                if it.get("pending_listing_ids"):
                    it["status"] = "listed" if not result.get("pending") else "pending"
                    it["last_msg"] = result["msg"]
                    if not result.get("pending"):
                        it["pending_listing_ids"] = []
            save_items()
    return jsonify(result)


# ------------------- Steam 登录（网页会话） -------------------
@app.route("/api/auth/status")
def api_auth_status():
    return jsonify(STEAM.status())


@app.route("/api/auth/check")
def api_auth_check():
    """三态验证；确认失效时尝试用长效 refresh token 自动恢复。"""
    try:
        with STEAM_LOCK:
            health = STEAM.session_health()
            recovered = False
            if health.get("valid") is False and STEAM.refresh_token:
                try:
                    STEAM._mint_web_cookies(force_refresh=True)
                    STEAM._save()
                    health = STEAM.session_health()
                    recovered = health.get("valid") is True
                except Exception as e:
                    health["recover_error"] = str(e)[:160]
            return jsonify({**health, "recovered": recovered,
                            "has_refresh": bool(STEAM.refresh_token)})
    except Exception as e:
        return jsonify({"valid": None, "reason": "check_error", "detail": str(e)[:120]})


@app.route("/api/auth/begin", methods=["POST"])
def api_auth_begin():
    """开始账号密码自动登录。用户名/密码取自「账号设置」(secret.json)。"""
    user = (CONFIG.get("steam_username") or "").strip()
    pw = CONFIG.get("steam_password") or ""
    if not user or not pw:
        return jsonify({"error": "请先在「账号设置」里填好 Steam 用户名和密码再登录"}), 400
    try:
        with STEAM_LOCK:
            return jsonify(STEAM.begin_login(user, pw))
    except Exception as e:
        hint = "；若账号正在使用加速器，请在账号设置填写加速器的本地 HTTP 代理端口" if not CONFIG.get("steam_proxy") else "；请检查代理地址/端口是否正确且加速器已启动"
        return jsonify({"error": str(e) + hint}), 400


@app.route("/api/auth/code", methods=["POST"])
def api_auth_code():
    body = request.get_json(force=True)
    try:
        with STEAM_LOCK:
            return jsonify(STEAM.submit_code(body.get("code")))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/poll", methods=["POST"])
def api_auth_poll():
    try:
        with STEAM_LOCK:
            return jsonify(STEAM.poll_once())
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/cookie", methods=["POST"])
def api_auth_cookie():
    """后备登录：粘贴 steamLoginSecure（和可选 sessionid）。"""
    body = request.get_json(force=True)
    try:
        with STEAM_LOCK:
            status = STEAM.set_cookie_login(body.get("steamLoginSecure"), body.get("sessionid"))
            health = STEAM.session_health()
            if health.get("valid") is False:
                STEAM.logout()
                return jsonify({"error": "Cookie 被 Steam 拒绝。请确认复制完整，并让浏览器与倒货台使用同一个加速器/代理出口。",
                                "reason": health.get("reason")}), 400
            return jsonify({**status, "verified": health.get("valid"),
                            "verify_reason": health.get("reason")})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/auth/logout", methods=["POST"])
def api_auth_logout():
    with STEAM_LOCK:
        STEAM.logout()
        _INV_CACHE.clear()
    return jsonify(STEAM.status())


@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    return jsonify(SETTINGS)


@app.route("/api/secret", methods=["GET"])
def api_get_secret():
    # 只返回掩码：永远不回传真实密码/密钥
    return jsonify({
        "has_api_key": bool(CONFIG.get("steam_api_key")),
        "steam_username": CONFIG.get("steam_username", ""),
        "has_password": bool(CONFIG.get("steam_password")),
        "steam_proxy": CONFIG.get("steam_proxy", ""),
        "steam_ssl_verify": bool(CONFIG.get("steam_ssl_verify", True)),
        "steamguard_file": CONFIG.get("steamguard_file", "steamguard.json"),
        "auto_list": bool(CONFIG.get("auto_list", False)),
        "enable_trade": bool(CONFIG.get("enable_trade", False)),
        "has_web_password": bool(CONFIG.get("web_password")),
    })


@app.route("/api/operations")
def api_operations():
    try:
        limit = min(200, max(1, int(request.args.get("limit", 50))))
    except Exception:
        limit = 50
    return jsonify(list(reversed(OPERATIONS[-limit:])))


def _job_worker(job_id, action, ids):
    job = JOBS[job_id]
    job.update(status="running", started_at=time.time())
    try:
        endpoint = api_list_batch if action == "list" else api_delist_batch
        path = "/api/list_batch" if action == "list" else "/api/delist_batch"
        with app.test_request_context(path, method="POST", json={"ids": ids, "job_id": job_id}):
            rv = endpoint()
            response = rv[0] if isinstance(rv, tuple) else rv
            status_code = rv[1] if isinstance(rv, tuple) else response.status_code
            data = response.get_json()
        if status_code >= 400:
            raise RuntimeError((data or {}).get("error") or f"HTTP {status_code}")
        job.update(status="completed", progress=job.get("total", len(ids)), result=data,
                   finished_at=time.time())
        log_operation("batch_" + action, count=len(ids), ok=True, result=data)
    except Exception as e:
        job.update(status="failed", error=str(e), finished_at=time.time())
        log_operation("batch_" + action, count=len(ids), ok=False, error=str(e))


@app.route("/api/jobs", methods=["POST"])
def api_create_job():
    body = request.get_json(force=True) or {}
    action, ids = body.get("action"), body.get("ids") or []
    if action not in ("list", "delist") or not ids:
        return jsonify({"error": "action/ids invalid"}), 400
    job_id = f"job-{time.time_ns()}"
    JOBS[job_id] = {"id": job_id, "action": action, "status": "queued",
                    "progress": 0, "total": len(ids), "created_at": time.time()}
    threading.Thread(target=_job_worker, args=(job_id, action, ids), daemon=True).start()
    return jsonify(JOBS[job_id]), 202


@app.route("/api/jobs/<job_id>")
def api_job_status(job_id):
    job = JOBS.get(job_id)
    return jsonify(job) if job else (jsonify({"error": "job not found"}), 404)


@app.route("/api/secret", methods=["PUT"])
def api_set_secret():
    global _steam_client
    body = request.get_json(force=True)
    updates = {}
    # 密码/Key：留空 = 不改（不覆盖已存的）
    for k in ("steam_api_key", "steam_password"):
        if k in body and str(body[k]).strip() != "":
            updates[k] = str(body[k]).strip()
    for k in ("steam_username", "steamguard_file", "steam_proxy"):
        if k in body:
            updates[k] = str(body[k]).strip()
    for k in ("auto_list", "enable_trade"):
        if k in body:
            updates[k] = bool(body[k])
    if "steam_ssl_verify" in body:
        updates["steam_ssl_verify"] = bool(body["steam_ssl_verify"])
    if "web_password" in body:              # 允许设空=关闭密码（本地用）
        updates["web_password"] = str(body.get("web_password") or "")

    data = {}
    if os.path.exists(SECRET_FILE):
        try:
            data = json.load(open(SECRET_FILE, encoding="utf-8"))
        except Exception:
            data = {}
    data.update(updates)
    try:
        with open(SECRET_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(SECRET_FILE, 0o600)   # 仅本人可读写
        except Exception:
            pass
    except Exception as e:
        return jsonify({"error": f"保存失败：{e}"}), 500

    CONFIG.update(updates)
    apply_steam_proxy()
    _steam_client = None    # 凭据变了，下次操作重新登录
    return api_get_secret()


@app.route("/api/settings", methods=["PUT"])
def api_set_settings():
    body = request.get_json(force=True)
    if "my_trade_link" in body:
        SETTINGS["my_trade_link"] = (body["my_trade_link"] or "").strip()
    if "inventory_appid" in body:
        try:
            appid = int(body["inventory_appid"])
            if appid in GAMES:
                SETTINGS["inventory_appid"] = appid
        except (TypeError, ValueError):
            pass
    save_settings()
    return jsonify(SETTINGS)


@app.route("/api/items/<iid>/send", methods=["POST"])
def api_send(iid):
    with LOCK:
        it = next((x for x in ITEMS if x["id"] == iid), None)
    if not it:
        return jsonify({"error": "not found"}), 404
    ok, msg = do_send(it)
    with LOCK:
        it["status"] = "sent" if ok else "error"
        it["last_msg"] = msg
        save_items()
    return jsonify({"ok": ok, "msg": msg, "item": item_view(it)})


def session_keepalive():
    """启动即检查，之后每小时检查；确认失效才用 refresh token 恢复。"""
    while True:
        try:
            with STEAM_LOCK:
                health = STEAM.session_health()
                if health.get("valid") is False and STEAM.refresh_token:
                    STEAM._mint_web_cookies(force_refresh=True)
                    STEAM._save()
                    print("[保活] Steam 会话已自动恢复")
                elif health.get("valid") is None:
                    print("[保活] Steam 网络暂不可用，不判定为过期：", health.get("reason"))
        except Exception as e:
            print("[保活] 自动恢复失败，下次继续重试：", e)
        time.sleep(3600)


def main():
    load_items()
    load_settings()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=session_keepalive, daemon=True).start()
    host = os.environ.get("SKINDESK_HOST") or CONFIG.get("host") or "127.0.0.1"
    if host != "127.0.0.1" and not (CONFIG.get("web_password") or ""):
        print("⚠️ 警告：绑定了非本地地址却没设 web_password——任何能连到这台机器的人都能操作你的 Steam！")
        print("   请先在 secret.json 里设 web_password，再用 0.0.0.0。")
    print(f"倒货台启动 → http://{host}:{CONFIG['port']}   (auto_list={CONFIG['auto_list']})")
    app.run(host=host, port=CONFIG["port"], threaded=True)


if __name__ == "__main__":
    main()
