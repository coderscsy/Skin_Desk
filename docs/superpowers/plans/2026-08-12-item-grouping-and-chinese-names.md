# Item Grouping and Chinese Names Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display Steam official Chinese item names while preserving English market identifiers, prioritize watching/listed rows, and place sold rows in a collapsible group.

**Architecture:** Steam inventory parsing returns both invariant `market_hash_name` and localized `name_zh`. Flask persists `name_zh` as display-only metadata while every Steam lookup continues to use `name`. The single-file frontend performs a stable presentation sort and renders sold records behind one collapsible table group.

**Tech Stack:** Python 3, Flask, pytest-compatible assertion tests, vanilla HTML/CSS/JavaScript.

## Global Constraints

- Keep `name` as the exact English Steam `market_hash_name` used for prices, inventory matching, and listing.
- Do not call third-party translation services.
- Only use Steam official localized inventory descriptions; fall back to the English name.
- Preserve valid UTF-8 in every edited file.
- Do not modify credentials, cookies, login files, watchlist data, or price cache data.
- Do not commit unless the user explicitly requests a commit.

---

### Task 1: Parse Steam official Chinese names

**Files:**
- Modify: `steam_session.py:100`
- Modify: `steam_session.py:123`
- Modify: `steam_session.py:482`
- Test: `test_steam_session.py:89`

**Interfaces:**
- Consumes: Steam inventory descriptions containing `market_hash_name`, `market_name`, and `name`.
- Produces: inventory asset dictionaries and picker rows containing `name_zh: str` without changing dictionary keys.

- [ ] **Step 1: Write the failing parser test**

Add localized names to the fixture and assert both parser outputs:

```python
{"classid": "c1", "instanceid": "i1", "market_hash_name": "Snakebite Case",
 "market_name": "蛇噬武器箱", "name": "蛇噬武器箱", "marketable": 1}

grouped = ss.parse_inventory(data)
items = ss.parse_inventory_items(data)
assert grouped["Snakebite Case"][0]["name_zh"] == "蛇噬武器箱"
assert items[0]["market_hash_name"] == "Snakebite Case"
assert items[0]["name_zh"] == "蛇噬武器箱"
```

- [ ] **Step 2: Run the parser test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest test_steam_session.py::test_parse_inventory -q`

Expected: FAIL because `name_zh` is absent.

- [ ] **Step 3: Add localized-name parsing**

Use one helper in `steam_session.py`:

```python
def localized_market_name(description, market_hash_name):
    value = (description.get("market_name") or description.get("name") or "").strip()
    return value or market_hash_name
```

Add `name_zh` to each grouped asset and each picker item. Change inventory params from `{"l": "english", ...}` to `{"l": "schinese", ...}`. Do not change the output key from `market_hash_name`.

- [ ] **Step 4: Run the parser test and verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest test_steam_session.py::test_parse_inventory -q`

Expected: `1 passed`.

### Task 2: Persist Chinese display names without changing Steam keys

**Files:**
- Modify: `app.py:598`
- Modify: `app.py:634`
- Modify: `app.py:918`
- Test: `test_multigame.py:27`

**Interfaces:**
- Consumes: optional `name_zh` from inventory-picker POST data and localized inventory copies from Task 1.
- Produces: `name_zh` in saved item dictionaries and `/api/items` responses.

- [ ] **Step 1: Write the failing API test**

Extend the existing item POST test:

```python
r = client.post("/api/items", json={
    "name": "Panda Rug", "name_zh": "熊猫地毯", "appid": 252490, "qty": 2,
    "purchase": 100, "listing_price": 120, "fee": 13})
j = r.get_json()
assert j["name"] == "Panda Rug"
assert j["name_zh"] == "熊猫地毯"
```

- [ ] **Step 2: Run the API test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest test_multigame.py -q`

Expected: FAIL because POST does not persist `name_zh`.

- [ ] **Step 3: Add display-name persistence and sync enrichment**

Normalize the incoming display name independently:

```python
name_zh = (body.get("name_zh") or "").strip()
```

Store it on POST, allow `name_zh` in the PUT field allowlist, and during `/api/steam_sync` copy the first non-empty localized name from the matching inventory copies onto the tracked item. Continue every lookup and `PRICE_CACHE` key with `it["name"]`.

- [ ] **Step 4: Run the API test and verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest test_multigame.py -q`

Expected: all tests in the file pass.

### Task 3: Render Chinese names and group sold rows

**Files:**
- Modify: `index.html:149`
- Modify: `index.html:510`
- Modify: `index.html:752`
- Modify: `index.html:1369`
- Modify: `index.html:1777`
- Create: `test_frontend_ui.py`

**Interfaces:**
- Consumes: item objects with `name`, optional `name_zh`, and `status`.
- Produces: stable row order, Chinese primary labels, English subtitles, and a collapsible sold section.

- [ ] **Step 1: Write the failing frontend structure test**

```python
from pathlib import Path


def test_frontend_contains_grouping_and_bilingual_name_contract():
    html = Path("index.html").read_text(encoding="utf-8")
    assert "function itemDisplayPriority" in html
    assert "soldGroupExpanded" in html
    assert "data-sold-group" in html
    assert "it.name_zh" in html
    assert "item-name-en" in html
```

- [ ] **Step 2: Run the frontend test and verify RED**

Run: `.venv\Scripts\python.exe -m pytest test_frontend_ui.py -q`

Expected: FAIL because the grouping and bilingual display markers do not exist.

- [ ] **Step 3: Implement stable sorting and sold grouping**

Add presentation state and priority without mutating `state`:

```javascript
let soldGroupExpanded = false;
function itemDisplayPriority(it){
  if(it.status==="watching" || it.status==="listed") return 0;
  if(it.status==="sold") return 2;
  return 1;
}
function displayItems(){
  return state.map((it,index)=>({it,index}))
    .sort((a,b)=>itemDisplayPriority(a.it)-itemDisplayPriority(b.it)||a.index-b.index)
    .map(x=>x.it);
}
```

Render one clickable `data-sold-group` row before the first sold record. Give sold item rows a `data-sold-item` marker and hide them while collapsed unless the current search matches them.

- [ ] **Step 4: Implement bilingual labels and search**

Render `name_zh || name` as the primary label and render `name` in `.item-name-en` only when the localized value differs. In the inventory picker, search and display both fields. POST both fields:

```javascript
body:JSON.stringify({name:it.market_hash_name, name_zh:it.name_zh||it.name||"", appid, qty:it.count})
```

Include `it.name_zh` in `applyTableFilter()` search text. A sold match overrides collapse only for matching sold rows; clearing search returns to `soldGroupExpanded`.

- [ ] **Step 5: Run the frontend test and verify GREEN**

Run: `.venv\Scripts\python.exe -m pytest test_frontend_ui.py -q`

Expected: `1 passed`.

### Task 4: Regression and encoding verification

**Files:**
- Verify: `steam_session.py`
- Verify: `app.py`
- Verify: `index.html`
- Verify: `test_steam_session.py`
- Verify: `test_multigame.py`
- Verify: `test_frontend_ui.py`

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: evidence that parser, API, frontend contract, syntax, and UTF-8 checks pass together.

- [ ] **Step 1: Run focused tests**

Run: `.venv\Scripts\python.exe -m pytest test_steam_session.py test_multigame.py test_frontend_ui.py -q`

Expected: all focused tests pass.

- [ ] **Step 2: Compile Python files**

Run: `.venv\Scripts\python.exe -m py_compile app.py steam_session.py test_frontend_ui.py`

Expected: exit code 0.

- [ ] **Step 3: Verify UTF-8 and mojibake markers**

Decode edited files with strict UTF-8 and assert that `index.html` contains none of `锟`, `�`, `鈥`, `浠`, or `鐧`.

Expected: valid UTF-8 and zero detected markers.
