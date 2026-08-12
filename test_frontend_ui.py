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
    assert '<th class="batch-profit-col">本批总盈利</th>' in html
    assert "function batchProfitCell" in html
    assert 'class="batch-profit-col"' in html
    assert ".compact-view th:nth-child(15)" in html
