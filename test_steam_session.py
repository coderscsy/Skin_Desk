#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""steam_session 里“可确定性验证”的逻辑的小测试（不联网）。
跑：  .venv\\Scripts\\python.exe test_steam_session.py
"""
import base64
import random

import steam_session as ss


# ---- 工具：纯 Python 生成一个 RSA 测试密钥，用于验证加密能被正确解密 ---- #
def _is_probable_prime(n, k=16):
    if n < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if n % p == 0:
            return n == p
    d, r = n - 1, 0
    while d % 2 == 0:
        d //= 2
        r += 1
    for _ in range(k):
        a = random.randrange(2, n - 1)
        x = pow(a, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = x * x % n
            if x == n - 1:
                break
        else:
            return False
    return True


def _gen_prime(bits):
    while True:
        n = random.getrandbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(n):
            return n


def test_rsa_roundtrip():
    p, q = _gen_prime(512), _gen_prime(512)
    n = p * q
    e = 65537
    d = pow(e, -1, (p - 1) * (q - 1))
    mod_hex, exp_hex = format(n, "x"), format(e, "x")
    k = (n.bit_length() + 7) // 8

    for pw in ("test-password!", "短", "a-fairly-long-password-1234567890"):
        b64 = ss._pkcs1v15_encrypt(pw.encode("utf-8"), mod_hex, exp_hex)
        c = int.from_bytes(base64.b64decode(b64), "big")
        eb = pow(c, d, n).to_bytes(k, "big")
        assert eb[0] == 0x00 and eb[1] == 0x02, "PKCS#1 头不对"
        sep = eb.index(0x00, 2)               # PS 之后的 00 分隔符
        assert sep - 2 >= 8, "PS 少于 8 字节"
        assert eb[sep + 1:].decode("utf-8") == pw, "解密结果和原文不一致"
    print("  ok  RSA PKCS#1 v1.5 加解密往返")


def test_sessionid():
    s = ss._gen_sessionid()
    assert len(s) == 24 and all(c in "0123456789abcdef" for c in s)
    print("  ok  sessionid 生成")


def test_cookie_parse_steamid(tmp="_t_login.json"):
    import os
    sess = ss.SteamSession(tmp)
    try:
        st = sess.set_cookie_login("76561198000000001%7C%7CABCDEF0123456789")
        assert st["steamid"] == "76561198000000001", st
        assert sess._login_cookie().startswith("76561198000000001%7C%7C")
        # 非法 cookie 应报错
        bad = False
        try:
            sess.set_cookie_login("garbage")
        except ValueError:
            bad = True
        assert bad, "非法 cookie 应抛 ValueError"
        print("  ok  cookie 解析 steamid + 校验")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def test_parse_inventory():
    data = {
        "success": 1,
        "assets": [
            {"assetid": "111", "classid": "c1", "instanceid": "i1"},
            {"assetid": "222", "classid": "c1", "instanceid": "i1"},   # 同款两件
            {"assetid": "333", "classid": "c2", "instanceid": "0"},
            {"assetid": "444", "classid": "cX", "instanceid": "iX"},   # 无对应描述 -> 跳过
        ],
        "descriptions": [
            {"classid": "c1", "instanceid": "i1", "market_hash_name": "Snakebite Case", "marketable": 1},
            {"classid": "c2", "instanceid": "0", "market_hash_name": "AK-47 | Redline", "marketable": 0},
        ],
    }
    out = ss.parse_inventory(data)
    assert set(out.keys()) == {"Snakebite Case", "AK-47 | Redline"}, out
    assert [x["assetid"] for x in out["Snakebite Case"]] == ["111", "222"], out
    assert out["AK-47 | Redline"][0]["marketable"] == 0
    assert ss.parse_inventory({"success": 0}) == {}
    assert ss.parse_inventory({}) == {}
    print("  ok  库存解析 name->assetid（含同款多件 / 缺描述 / marketable）")


if __name__ == "__main__":
    print("running steam_session tests…")
    test_rsa_roundtrip()
    test_sessionid()
    test_cookie_parse_steamid()
    test_parse_inventory()
    print("ALL PASS")
