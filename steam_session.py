#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
steam_session.py —— Steam 网页会话（登录 + 库存 + 上架）

目标：拿到一个“已登录的” requests.Session（带 steamLoginSecure / sessionid cookie）
和你的 steamid，然后就能调 Steam 官方市场接口批量上架。

设计要点：
  * 零额外依赖：只用 requests（项目已装）+ 标准库。密码的 RSA 加密用纯 Python 实现，
    不需要 cryptography / rsa（Python 3.14 上这些不一定有 wheel）。
  * 两种登录方式：
      1) 账号密码自动登录（Steam 新版 IAuthenticationService 流程）。
         首次需要你在手机上输入 6 位令牌码，或在手机 Steam App 点“同意登录”。
         —— 这就是“令牌手动确认”。成功后缓存 refresh_token（长效），
            以后直接换新的网页 cookie，不用再过 2FA。
      2) 粘贴 steamLoginSecure cookie（后备，最稳，从国内也能用）。
  * 上架（sellitem）只创建挂单，**不自动确认**。挂单后由你在手机令牌里手动确认。
    全程不需要 identity_secret。

注意：自动登录这条路依赖 Steam 未公开的登录接口，可能随 Steam 改版失效；
真出问题就用 Cookie 粘贴兜底。
"""

import os
import json
import time
import base64
import re
import hashlib
import hmac
import struct
import secrets as _secrets

import requests

API = "https://api.steampowered.com/IAuthenticationService"
COMMUNITY = "https://steamcommunity.com"
STORE = "https://store.steampowered.com"
LOGIN = "https://login.steampowered.com"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# EAuthSessionGuardType
class Guard:
    NONE = 1
    EMAIL_CODE = 2
    DEVICE_CODE = 3            # 手机令牌 6 位码（TOTP）
    DEVICE_CONFIRMATION = 4    # 手机 App 点“同意”
    EMAIL_CONFIRMATION = 5


class NeedAuth(Exception):
    """还没登录 / 会话失效，需要重新登录。"""


# ------------------------------------------------------------------ #
# 纯 Python RSA（PKCS#1 v1.5），用于加密 Steam 登录密码                  #
# ------------------------------------------------------------------ #
def _pkcs1v15_encrypt(message: bytes, mod_hex: str, exp_hex: str) -> str:
    n = int(mod_hex, 16)
    e = int(exp_hex, 16)
    k = (n.bit_length() + 7) // 8            # 模长（字节）
    if len(message) > k - 11:
        raise ValueError("password too long for this RSA key")
    # 填充串 PS：k - len(msg) - 3 个“非零”随机字节
    ps_len = k - len(message) - 3
    ps = bytearray()
    while len(ps) < ps_len:
        for b in os.urandom(ps_len - len(ps)):
            if b != 0:
                ps.append(b)
                if len(ps) == ps_len:
                    break
    eb = b"\x00\x02" + bytes(ps) + b"\x00" + message      # EB = 00 02 PS 00 M
    m = int.from_bytes(eb, "big")
    c = pow(m, e, n)
    return base64.b64encode(c.to_bytes(k, "big")).decode("ascii")


def _gen_sessionid() -> str:
    return _secrets.token_hex(12)            # 24 个十六进制字符


def _looks_logged_out(s) -> str:
    s = (s or "").lower()
    return any(k in s for k in ("logged in", "log in", "sign in", "not been logged", "please login"))


def parse_inventory(data: dict) -> dict:
    """把 Steam 库存 JSON 解析成 {market_hash_name: [{'assetid','marketable'}, ...]}。
    抽成纯函数方便单测。"""
    if not data or data.get("success") == 0:
        return {}
    descs = {}
    for d in data.get("descriptions") or []:
        descs[(str(d.get("classid")), str(d.get("instanceid")))] = d
    out = {}
    for a in data.get("assets") or []:
        d = descs.get((str(a.get("classid")), str(a.get("instanceid"))))
        if not d:
            continue
        name = d.get("market_hash_name")
        if not name:
            continue
        out.setdefault(name, []).append({
            "assetid": str(a.get("assetid")),
            "marketable": int(d.get("marketable", 0) or 0),
        })
    return out


def parse_inventory_items(data: dict) -> list:
    """把库存 JSON 解析成可展示/勾选的列表（按 market_hash_name 聚合数量）。
    返回 [{market_hash_name, name, count, marketable, tradable, icon}]，按数量降序。"""
    if not data or data.get("success") == 0:
        return []
    descs = {}
    for d in data.get("descriptions") or []:
        descs[(str(d.get("classid")), str(d.get("instanceid")))] = d
    agg = {}
    for a in data.get("assets") or []:
        d = descs.get((str(a.get("classid")), str(a.get("instanceid"))))
        if not d:
            continue
        name = d.get("market_hash_name")
        if not name:
            continue
        e = agg.get(name)
        if not e:
            e = agg[name] = {
                "market_hash_name": name,
                "name": d.get("name") or name,
                "count": 0,
                "marketable": int(d.get("marketable", 0) or 0),
                "tradable": int(d.get("tradable", 0) or 0),
                "icon": d.get("icon_url") or "",
            }
        e["count"] += 1
    # 可上架的排前面，再按数量多、名字排序
    return sorted(agg.values(), key=lambda x: (-x["marketable"], -x["count"], x["name"].lower()))


class SteamSession:
    def __init__(self, store_path: str):
        self.store_path = store_path
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.steamid = None
        self.account_name = None
        self.refresh_token = None
        self.access_token = None       # 轮询拿到的 web access_token，可直接当 cookie（绕过 login.steampowered.com）
        self._sessionid = None
        self.method = None                   # 'auto' | 'cookie' | None
        self._pending = None                 # 自动登录中间态
        self._load()

    # -------------------- 持久化 -------------------- #
    def _load(self):
        if not os.path.exists(self.store_path):
            return
        try:
            d = json.load(open(self.store_path, encoding="utf-8"))
        except Exception as e:
            print("steam_login 读取失败：", e)
            return
        self.steamid = d.get("steamid")
        self.account_name = d.get("account_name")
        self.refresh_token = d.get("refresh_token")
        self.access_token = d.get("access_token")
        self.method = d.get("method")
        self._sessionid = d.get("sessionid") or _gen_sessionid()
        for name, val in (d.get("cookies") or {}).items():
            self._set_cookie(name, val)

    def _save(self):
        cookies = {}
        for c in self.session.cookies:
            if c.name in ("steamLoginSecure", "sessionid"):
                cookies[c.name] = c.value
        data = {
            "steamid": self.steamid,
            "account_name": self.account_name,
            "refresh_token": self.refresh_token,
            "access_token": self.access_token,
            "method": self.method,
            "sessionid": self._sessionid,
            "cookies": cookies,
        }
        try:
            with open(self.store_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            try:
                os.chmod(self.store_path, 0o600)
            except Exception:
                pass
        except Exception as e:
            print("steam_login 保存失败：", e)

    # -------------------- cookie 工具 -------------------- #
    def _set_cookie(self, name, value):
        for domain in ("steamcommunity.com", "store.steampowered.com"):
            self.session.cookies.set(name, value, domain=domain, path="/")

    def _login_cookie(self):
        for c in self.session.cookies:
            if c.name == "steamLoginSecure" and c.value:
                return c.value
        return None

    def _has_cookies(self):
        return bool(self._login_cookie())

    def _ensure_sessionid_cookie(self):
        if not self._sessionid:
            self._sessionid = _gen_sessionid()
        self._set_cookie("sessionid", self._sessionid)

    # -------------------- 对外状态 -------------------- #
    def status(self):
        return {
            "logged_in": bool(self._has_cookies() or self.refresh_token),
            "steamid": self.steamid,
            "account_name": self.account_name,
            "method": self.method,
            "has_cookie": self._has_cookies(),
            "has_refresh": bool(self.refresh_token),
            "pending": bool(self._pending),
        }

    def check_session(self):
        """真正验证 web 会话是否有效：/my/ 跳到 /login 就是失效。返回 bool。"""
        return self.session_health().get("valid") is True

    def session_health(self):
        """三态校验：网络/限流返回 valid=None，避免误报会话过期。"""
        if not self._has_cookies():
            return {"valid": False, "reason": "no_cookie"}
        try:
            r = self.session.get(f"{COMMUNITY}/my/", allow_redirects=False, timeout=12)
            if r.status_code in (301, 302):
                location = (r.headers.get("Location") or "").lower()
                return {"valid": "login" not in location,
                        "reason": "login_redirect" if "login" in location else "redirect"}
            if r.status_code == 200:
                return {"valid": True, "reason": "ok"}
            if r.status_code in (401, 403):
                return {"valid": False, "reason": f"http_{r.status_code}"}
            return {"valid": None, "reason": f"http_{r.status_code}"}
        except Exception as e:
            return {"valid": None, "reason": "network", "detail": str(e)[:120]}

    def logout(self):
        self.session.cookies.clear()
        self.steamid = self.account_name = self.refresh_token = self.access_token = None
        self.method = None
        self._pending = None
        try:
            if os.path.exists(self.store_path):
                os.remove(self.store_path)
        except Exception:
            pass

    # -------------------- 方式二：粘贴 cookie -------------------- #
    def set_cookie_login(self, steam_login_secure: str, sessionid: str = None):
        v = (steam_login_secure or "").strip()
        if v.lower().startswith("steamloginsecure="):
            v = v.split("=", 1)[1].strip()
        v = v.strip('"\'')
        if not v or "%7C%7C" not in v and "||" not in v:
            raise ValueError("steamLoginSecure 看起来不对（应形如 765611980xxxxx%7C%7C....）")
        # cookie 值里 || 前面那段就是 steamid64
        sid = v.split("%7C%7C")[0].split("||")[0]
        if not sid.isdigit():
            raise ValueError("无法从 cookie 解析出 steamid，请确认复制完整")
        self.steamid = sid
        self._sessionid = (sessionid or "").strip() or _gen_sessionid()
        self._set_cookie("steamLoginSecure", v)
        self._ensure_sessionid_cookie()
        # Cookie 登录是独立会话，不能让旧账号密码登录的 token 在校验失败时覆盖它。
        self.refresh_token = None
        self.access_token = None
        self._pending = None
        self.method = "cookie"
        self._save()
        return self.status()

    # -------------------- 方式一：账号密码自动登录 -------------------- #
    def begin_login(self, username: str, password: str):
        username = (username or "").strip()
        if not username or not password:
            raise ValueError("用户名 / 密码不能为空")
        # 1) 取 RSA 公钥
        r = self.session.get(f"{API}/GetPasswordRSAPublicKey/v1/",
                             params={"account_name": username}, timeout=20)
        resp = r.json().get("response") or {}
        mod, exp, ts = resp.get("publickey_mod"), resp.get("publickey_exp"), resp.get("timestamp")
        if not mod:
            raise RuntimeError("取 RSA 公钥失败（用户名可能不对，或被 Steam 限流）")
        enc_pw = _pkcs1v15_encrypt(password.encode("utf-8"), mod, exp)
        # 2) 开始登录会话
        r = self.session.post(f"{API}/BeginAuthSessionViaCredentials/v1/", data={
            "account_name": username,
            "encrypted_password": enc_pw,
            "encryption_timestamp": ts,
            "persistence": 1,                 # 持久会话
            "website_id": "Community",
            "device_friendly_name": "Skin Desk (Skin Desk)",
        }, timeout=20)
        resp = r.json().get("response") or {}
        if not resp.get("client_id"):
            raise RuntimeError("BeginAuthSession 失败（密码错误？或 Steam 拒绝）")
        allowed = {c.get("confirmation_type") for c in (resp.get("allowed_confirmations") or [])}
        self._pending = {
            "client_id": resp["client_id"],
            "request_id": resp["request_id"],
            "steamid": resp.get("steamid"),
            "interval": resp.get("interval", 5),
            "allowed": allowed,
            "username": username,
        }
        self.account_name = username
        if Guard.DEVICE_CONFIRMATION in allowed:
            need = "confirm"       # 优先：手机 App 点同意，免输码
        elif Guard.DEVICE_CODE in allowed:
            need = "code"          # 输手机令牌码
        elif Guard.EMAIL_CODE in allowed:
            need = "email_code"    # 输邮箱验证码
        else:
            need = "none"          # 无需 2FA，直接可轮询
        return {
            "need": need,
            "can_confirm": Guard.DEVICE_CONFIRMATION in allowed,
            "can_code": (Guard.DEVICE_CODE in allowed) or (Guard.EMAIL_CODE in allowed),
            "is_email": (Guard.EMAIL_CODE in allowed) and (Guard.DEVICE_CODE not in allowed),
            "allowed": sorted(allowed),
            "steamid": resp.get("steamid"),
        }

    def submit_code(self, code: str):
        if not self._pending:
            raise NeedAuth("没有正在进行的登录，请重新开始")
        code = (code or "").strip()
        if not code:
            raise ValueError("验证码不能为空")
        allowed = self._pending["allowed"]
        code_type = Guard.DEVICE_CODE if Guard.DEVICE_CODE in allowed else Guard.EMAIL_CODE
        r = self.session.post(f"{API}/UpdateAuthSessionWithSteamGuardCode/v1/", data={
            "client_id": self._pending["client_id"],
            "steamid": self._pending["steamid"],
            "code": code,
            "code_type": code_type,
        }, timeout=20)
        # 返回里有 agreement_session_url 之类一般忽略；错误时 HTTP 头 x-eresult != 1
        eresult = r.headers.get("x-eresult")
        if eresult and eresult not in ("1", "0"):
            raise RuntimeError(f"验证码被拒（x-eresult={eresult}），核对后重试")
        return self.poll_once()

    def poll_once(self):
        """轮询一次登录状态。返回 {'state': 'done'|'waiting', ...}。
        关键：批准后『换网页 cookie』要连 login.steampowered.com，国内常超时——
        这时不报错、不丢进度，存下 refresh_token 返回 waiting，下次自动重试换 cookie，直到通。"""
        # 已拿到 refresh_token 但还没换到 cookie：直接重试换 cookie（不再问 Steam 批准状态）
        if self.refresh_token and not self._has_cookies():
            try:
                self._mint_web_cookies()
                self._pending = None
                self._save()
                return {"state": "done", "steamid": self.steamid}
            except Exception:
                return {"state": "waiting"}
        if not self._pending:
            raise NeedAuth("没有正在进行的登录")
        r = self.session.post(f"{API}/PollAuthSessionStatus/v1/", data={
            "client_id": self._pending["client_id"],
            "request_id": self._pending["request_id"],
        }, timeout=20)
        resp = r.json().get("response") or {}
        if resp.get("refresh_token"):
            self.refresh_token = resp["refresh_token"]
            self.access_token = resp.get("access_token") or self.access_token
            self.account_name = resp.get("account_name") or self.account_name
            self.steamid = self._pending.get("steamid") or self.steamid
            self.method = "auto"
            self._save()                   # 先把 refresh_token 落盘，换 cookie 失败也不丢
            try:
                self._mint_web_cookies()
                self._pending = None
                self._save()
                return {"state": "done", "steamid": self.steamid}
            except Exception:
                return {"state": "waiting"}   # 批准已收到，换 cookie 这步被墙，下次重试
        return {"state": "waiting"}

    # -------------------- refresh_token -> 网页 cookie -------------------- #
    def _del_cookie(self, name):
        for d in ("steamcommunity.com", "store.steampowered.com"):
            try:
                self.session.cookies.clear(d, "/", name)
            except KeyError:
                pass

    def _mint_web_cookies(self, force_refresh=False):
        self._sessionid = self._sessionid or _gen_sessionid()
        self._ensure_sessionid_cookie()
        # 优先：access_token 直接拼 steamLoginSecure（走 api.steampowered.com，绕过被墙的 login.steampowered.com）
        if self.access_token and not force_refresh:
            self._set_cookie("steamLoginSecure", f"{self.steamid}%7C%7C{self.access_token}")
            try:
                health = self.session_health()
                if health.get("valid") is True:
                    return True
                if health.get("valid") is None:
                    raise RuntimeError("Steam 网络暂不可用，无法验证新会话")
            except Exception:
                raise
            self._del_cookie("steamLoginSecure")   # 这个 token 不顶用，删掉走回退
        # 回退：finalizelogin（要连 login.steampowered.com，国内可能超时）
        if not self.refresh_token:
            raise NeedAuth("没有 refresh_token")
        r = self.session.post(f"{LOGIN}/jwt/finalizelogin", data={
            "nonce": self.refresh_token,
            "sessionid": self._sessionid,
            "redir": f"{COMMUNITY}/login/home/?goto=",
        }, headers={"Origin": COMMUNITY, "Referer": f"{COMMUNITY}/"}, timeout=15)
        j = r.json()
        transfers = j.get("transfer_info") or []
        if not transfers:
            raise RuntimeError(f"finalizelogin 没返回 transfer_info：{str(j)[:200]}")
        steamid = j.get("steamID") or self.steamid
        self.steamid = steamid
        ok = False
        for ti in transfers:
            try:
                params = dict(ti.get("params") or {})
                params["steamID"] = steamid
                self.session.post(ti["url"], data=params, timeout=15)
                ok = True
            except Exception as e:
                print("settoken 失败：", ti.get("url"), e)
        if not ok:
            raise RuntimeError("设置 steamLoginSecure cookie 失败")
        self._ensure_sessionid_cookie()
        return True

    def ensure_ready(self):
        """确保会话可用于上架。不可用且无法自动恢复时抛 NeedAuth。"""
        if self._has_cookies():
            self._ensure_sessionid_cookie()
            return True
        if self.refresh_token:
            self._mint_web_cookies()
            self._save()
            return True
        raise NeedAuth("尚未登录 Steam")

    # -------------------- 库存 / 上架 -------------------- #
    def _inventory_raw(self, appid, contextid):
        """拉某游戏库存的原始合并数据（assets+descriptions），自动翻页。
        浏览公开库存不强制登录（只要知道 steamid）；私密库存且无有效会话会 403。
        注意：count 用 2000——5000 会被 Steam 拒成 HTTP 400。"""
        if not self.steamid:
            raise NeedAuth("还不知道你的 steamid，请先登录一次")
        if self._has_cookies():
            self._ensure_sessionid_cookie()
        base = f"{COMMUNITY}/inventory/{self.steamid}/{appid}/{contextid}"
        headers = {"Referer": f"{COMMUNITY}/profiles/{self.steamid}/inventory"}
        merged = {"success": 1, "assets": [], "descriptions": []}
        last = None
        for _ in range(25):                       # 最多 25 页（5 万件）足够
            params = {"l": "english", "count": 2000}
            if last:
                params["start_assetid"] = last
            r = self.session.get(base, params=params, headers=headers, timeout=25)
            if r.status_code in (401, 403):
                raise NeedAuth("拉库存被拒（会话失效，或该库存设了私密）")
            if r.status_code != 200:
                raise RuntimeError(f"库存接口返回 HTTP {r.status_code}（不是空库存，是接口报错）")
            data = r.json()
            if not isinstance(data, dict) or not data.get("success"):
                break                             # 私密 / 真的空
            merged["assets"].extend(data.get("assets") or [])
            merged["descriptions"].extend(data.get("descriptions") or [])
            if data.get("more_items") and data.get("last_assetid"):
                last = data["last_assetid"]
                time.sleep(0.6)
            else:
                break
        return merged

    def fetch_inventory(self, appid=730, contextid=2):
        """{market_hash_name: [{'assetid','marketable'}, ...]}（上架用）。"""
        return parse_inventory(self._inventory_raw(appid, contextid))

    def fetch_inventory_items(self, appid=730, contextid=2):
        """可展示/勾选的物品列表（浏览选物用）。"""
        return parse_inventory_items(self._inventory_raw(appid, contextid))

    def create_sell_listing(self, assetid, price_cents, appid=730, contextid=2, _retry=True):
        """挂一个市场卖单，价格为“到手金额”（单位：分）。不自动确认。"""
        self.ensure_ready()
        price_cents = int(round(price_cents))
        if price_cents <= 0:
            return {"ok": False, "msg": "价格为 0，请先填写上架价格"}
        headers = {
            "Referer": f"{COMMUNITY}/profiles/{self.steamid}/inventory",
            "Origin": COMMUNITY,
            "X-Requested-With": "XMLHttpRequest",
        }
        data = {
            "sessionid": self._sessionid,
            "appid": str(appid),
            "contextid": str(contextid),
            "assetid": str(assetid),
            "amount": "1",
            "price": str(price_cents),
        }
        try:
            r = self.session.post(f"{COMMUNITY}/market/sellitem/", data=data,
                                 headers=headers, timeout=25)
        except Exception as e:
            return {"ok": False, "msg": f"网络错误：{e}"}
        try:
            j = r.json()
        except Exception:
            txt = (r.text or "")[:200]
            # 没登录时 Steam 返回登录页 HTML
            if _retry and (_looks_logged_out(txt) or r.status_code in (401, 403)):
                return self._retry_after_relogin(assetid, price_cents, appid, contextid)
            return {"ok": False, "msg": f"HTTP {r.status_code}: {txt}"}
        if j.get("success"):
            needs = bool(j.get("requires_confirmation")) or bool(j.get("needs_mobile_confirmation"))
            listing_ids = []
            if j.get("listingid"):
                listing_ids.append(str(j["listingid"]))
            for row in (j.get("sell_listings") or []):
                lid = row.get("listingid") or row.get("id")
                if lid:
                    listing_ids.append(str(lid))
            return {"ok": True, "needs_confirmation": needs,
                    "listing_ids": list(dict.fromkeys(listing_ids))}
        msg = j.get("message") or "上架失败（未知原因）"
        if _retry and _looks_logged_out(msg):
            return self._retry_after_relogin(assetid, price_cents, appid, contextid)
        return {"ok": False, "msg": msg}

    def fetch_my_listings(self):
        """读取当前账号的市场挂单，返回 [{listingid, name, appid, assetid}]。"""
        self.ensure_ready()
        self._ensure_sessionid_cookie()
        found = []
        start = 0
        for _ in range(20):
            r = self.session.get(f"{COMMUNITY}/market/mylistings/render/", params={
                "query": "", "start": start, "count": 100,
            }, headers={"Referer": f"{COMMUNITY}/market/"}, timeout=25)
            if r.status_code in (401, 403):
                raise NeedAuth("市场会话已失效，请重新登录")
            if r.status_code != 200:
                raise RuntimeError(f"读取市场挂单失败：HTTP {r.status_code}")
            data = r.json()
            assets = {}
            for appid, contexts in (data.get("assets") or {}).items():
                for contextid, entries in (contexts or {}).items():
                    for assetid, asset in (entries or {}).items():
                        assets[(str(appid), str(contextid), str(assetid))] = asset or {}
            page = []
            for listingid, info in (data.get("listinginfo") or {}).items():
                asset = info.get("asset") or {}
                appid = str(asset.get("appid") or "")
                contextid = str(asset.get("contextid") or "")
                assetid = str(asset.get("id") or asset.get("assetid") or "")
                desc = {**asset, **assets.get((appid, contextid, assetid), {})}
                name = desc.get("market_hash_name") or desc.get("name")
                if name:
                    page.append({"listingid": str(listingid), "name": name,
                                 "appid": int(appid or 0), "assetid": assetid})
            # 某些 Steam 返回不含 listinginfo，从 hover 脚本恢复 listing -> asset 映射。
            if not page:
                hover = data.get("hovers") or ""
                html = data.get("results_html") or ""
                # hovers 还会混入“待确认/失败回库”记录；只有可见正式挂单行里的 ID 才有效。
                visible_ids = set(re.findall(r"(?:id=['\"]mylisting_|mylisting_)(\d+)", html))
                # 新版市场：CreateItemHoverFromContainer(g_rgAssets,
                # 'mylisting_<listingid>_name', appid, 'contextid', 'assetid', 1)
                pattern = (r"mylisting_(\d+)_(?:name|image)'\s*,\s*(\d+)\s*,\s*"
                           r"'(\d+)'\s*,\s*'(\d+)'\s*,")
                for m in re.finditer(pattern, hover, re.S):
                    listingid, appid, contextid, assetid = m.groups()
                    if visible_ids and listingid not in visible_ids:
                        continue
                    desc = assets.get((appid, contextid, assetid), {})
                    name = desc.get("market_hash_name") or desc.get("name")
                    if name:
                        page.append({"listingid": listingid, "name": name,
                                     "appid": int(appid), "assetid": assetid})
            if not page:
                html = data.get("results_html") or ""
                for m in re.finditer(r"CancelMarketListing\([^,]+,\s*'?(\d+)'?,\s*(\d+),\s*'?(\d+)'?,\s*'?(\d+)'?", html):
                    listingid, appid, contextid, assetid = m.groups()
                    desc = assets.get((appid, contextid, assetid), {})
                    name = desc.get("market_hash_name") or desc.get("name")
                    if name:
                        page.append({"listingid": listingid, "name": name,
                                     "appid": int(appid), "assetid": assetid})
            known = {x["listingid"] for x in found}
            for item in page:
                if item["listingid"] not in known:
                    found.append(item)
                    known.add(item["listingid"])
            total = int(data.get("total_count", len(found)) or 0)
            # Steam 市场测试版会无视 count=100，实际 pagesize 常为 10。
            page_size = int(data.get("pagesize") or 0) or max(1, len(page))
            start += page_size
            if start >= total:
                break
        return found

    def fetch_hidden_pending_listings(self):
        """读取市场返回中存在但未显示为正式挂单的隐藏/被吞待确认记录。"""
        self.ensure_ready()
        r = self.session.get(f"{COMMUNITY}/market/mylistings/render/", params={
            "query": "", "start": 0, "count": 100,
        }, headers={"Referer": f"{COMMUNITY}/market/"}, timeout=25)
        if r.status_code in (401, 403):
            raise NeedAuth("市场会话已失效，请重新登录")
        if r.status_code != 200:
            raise RuntimeError(f"读取待确认记录失败：HTTP {r.status_code}")
        data = r.json()
        assets = {}
        for appid, contexts in (data.get("assets") or {}).items():
            for contextid, entries in (contexts or {}).items():
                for assetid, asset in (entries or {}).items():
                    assets[(str(appid), str(contextid), str(assetid))] = asset or {}
        html = data.get("results_html") or ""
        hover = data.get("hovers") or ""
        visible_ids = set(re.findall(r"(?:id=['\"]mylisting_|mylisting_)(\d+)", html))
        pattern = (r"mylisting_(\d+)_(?:name|image)'\s*,\s*(\d+)\s*,\s*"
                   r"'(\d+)'\s*,\s*'(\d+)'\s*,")
        hidden = []
        seen = set()
        for m in re.finditer(pattern, hover, re.S):
            listingid, appid, contextid, assetid = m.groups()
            if listingid in visible_ids or listingid in seen:
                continue
            desc = assets.get((appid, contextid, assetid), {})
            name = desc.get("market_hash_name") or desc.get("name")
            if name:
                hidden.append({"listingid": listingid, "name": name,
                               "appid": int(appid), "assetid": assetid})
                seen.add(listingid)
        return hidden

    def remove_listing(self, listingid):
        """撤下一个正式市场挂单。"""
        self.ensure_ready()
        try:
            r = self.session.post(f"{COMMUNITY}/market/removelisting/{listingid}",
                                  data={"sessionid": self._sessionid},
                                  headers={"Origin": COMMUNITY, "Referer": f"{COMMUNITY}/market/"},
                                  timeout=15)
        except requests.RequestException as e:
            return {"ok": False, "msg": f"网络请求失败：{str(e)[:120]}"}
        try:
            data = r.json()
        except Exception:
            data = {}
        # Steam 新版撤单成功时可能返回 []，旧版返回 {success: 1}。
        if r.status_code == 200 and (
                isinstance(data, list) or
                (isinstance(data, dict) and data.get("success") in (True, 1, None))):
            return {"ok": True}
        msg = data.get("message") if isinstance(data, dict) else None
        return {"ok": False, "msg": msg or f"HTTP {r.status_code}"}

    @staticmethod
    def _confirmation_key(identity_secret, timestamp, tag):
        payload = struct.pack(">Q", int(timestamp)) + (tag or "").encode("ascii")[:32]
        secret = base64.b64decode(identity_secret)
        return base64.b64encode(hmac.new(secret, payload, hashlib.sha1).digest()).decode()

    def confirm_market_listings(self, identity_secret, device_id, listing_ids):
        """只确认 creator_id 命中本次 listing_ids 的市场出售，不触碰交易报价。"""
        self.ensure_ready()
        wanted = {str(x) for x in (listing_ids or []) if x}
        if not wanted:
            return {"confirmed": 0, "pending": 0, "msg": "本次上架未返回可确认的挂单编号"}
        now = int(time.time())
        params = {"p": device_id, "a": self.steamid, "t": now, "m": "android", "tag": "conf",
                  "k": self._confirmation_key(identity_secret, now, "conf")}
        data = self.session.get(f"{COMMUNITY}/mobileconf/getlist", params=params, timeout=25).json()
        if not data.get("success"):
            raise RuntimeError(data.get("message") or "读取手机确认列表失败")
        matches = [c for c in (data.get("conf") or data.get("confirmations") or [])
                   if int(c.get("type", 0) or 0) == 3 and str(c.get("creator_id") or "") in wanted]
        confirmed, errors = 0, []
        for c in matches:
            ts = int(time.time())
            op = {"op": "allow", "cid": c.get("id"), "ck": c.get("nonce") or c.get("key"),
                  "p": device_id, "a": self.steamid, "t": ts, "m": "android", "tag": "allow",
                  "k": self._confirmation_key(identity_secret, ts, "allow")}
            result = self.session.get(f"{COMMUNITY}/mobileconf/ajaxop", params=op, timeout=25).json()
            if result.get("success"):
                confirmed += 1
            else:
                errors.append(result.get("message") or "确认失败")
            time.sleep(0.5)
        msg = f"已确认 {confirmed} 条"
        if not matches:
            msg = "确认列表尚未同步到本次挂单，请稍后重试"
        elif errors:
            msg += "；" + "；".join(dict.fromkeys(errors))[:80]
        return {"confirmed": confirmed, "pending": max(0, len(wanted) - confirmed), "msg": msg}

    def _retry_after_relogin(self, assetid, price_cents, appid, contextid):
        """会话失效：用 refresh_token 重新换 cookie 后重试一次。
        国内常连不上 login.steampowered.com，连不上就提示改用 Cookie 登录。"""
        if not self.refresh_token:
            return {"ok": False, "msg": "会话已失效，请用『粘贴 Cookie 登录』重新登录"}
        try:
            self._mint_web_cookies()
        except Exception:
            return {"ok": False, "msg": "会话已失效；自动重登也连不上 login.steampowered.com（国内常见），请改用『粘贴 Cookie 登录』"}
        return self.create_sell_listing(assetid, price_cents, appid, contextid, _retry=False)
