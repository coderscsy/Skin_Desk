#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Steam 账号读取请求的 token 续期与单次重试测试。"""
from steam_session import SteamSession


class FakeResponse:
    def __init__(self, status_code=200, text="", content_type="application/json", url="https://steamcommunity.com/data"):
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": content_type}
        self.url = url


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def make_steam(responses):
    steam = object.__new__(SteamSession)
    steam.session = FakeSession(responses)
    steam.access_token = "present-but-never-logged"
    steam.refresh_token = "present-but-never-logged"
    steam.ensure_ready = lambda: True
    renewals = []
    steam._renew_web_session_for_read = lambda: renewals.append(True)
    return steam, renewals


def test_login_html_renews_cookie_and_retries_once():
    steam, renewals = make_steam([
        FakeResponse(text="<html><title>Sign In</title></html>", content_type="text/html"),
        FakeResponse(text='{"success":true}'),
    ])
    response = steam.authenticated_get("https://steamcommunity.com/data", timeout=12)
    assert response.status_code == 200
    assert renewals == [True]
    assert len(steam.session.calls) == 2


def test_401_renews_cookie_and_retries_once():
    steam, renewals = make_steam([FakeResponse(401), FakeResponse(200)])
    steam.authenticated_get("https://steamcommunity.com/data")
    assert renewals == [True]
    assert len(steam.session.calls) == 2


def test_json_success_does_not_renew_or_retry():
    steam, renewals = make_steam([FakeResponse(200, text='{"success":true}')])
    steam.authenticated_get("https://steamcommunity.com/data")
    assert renewals == []
    assert len(steam.session.calls) == 1


def test_429_does_not_renew_or_retry():
    steam, renewals = make_steam([FakeResponse(429)])
    steam.authenticated_get("https://steamcommunity.com/data")
    assert renewals == []
    assert len(steam.session.calls) == 1


if __name__ == "__main__":
    test_login_html_renews_cookie_and_retries_once()
    test_401_renews_cookie_and_retries_once()
    test_json_success_does_not_renew_or_retry()
    test_429_does_not_renew_or_retry()
    print("ALL PASS")
