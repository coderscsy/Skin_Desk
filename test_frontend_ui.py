#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""前端名称展示与状态分组契约测试。"""
from pathlib import Path


def test_frontend_contains_grouping_and_bilingual_name_contract():
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    assert "function itemDisplayPriority" in html
    assert "soldGroupExpanded" in html
    assert "data-sold-group" in html
    assert "it.name_zh" in html
    assert "item-name-en" in html


def test_frontend_shows_batch_profit_as_a_separate_visible_column():
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    assert '<th class="batch-profit-col">本批盈利</th>' in html
    assert '<th class="batch-total-col">本批总额</th>' in html
    assert "function batchProfitCell" in html
    assert "function batchTotalCell" in html
    assert 'class="batch-profit-col"' in html
    assert 'class="batch-total-col"' in html
    assert ".compact-view th:nth-child(16)" in html


def test_steam_history_chart_has_hover_tooltip():
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    assert 'class="chart-tooltip"' in html
    assert 'class="chart-hover-target"' in html
    assert "function bindSteamHistoryHover" in html
    assert 'addEventListener("pointerenter"' in html
    assert 'addEventListener("pointermove"' in html


def test_compact_view_hides_verbose_status_details():
    html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
    assert '<th class="l status-col">状态</th>' in html
    assert 'class="l status-col"' in html
    assert ".compact-view .status-col .msg{display:none}" in html
