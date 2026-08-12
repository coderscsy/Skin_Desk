# Steam Token Read Session Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically renew the Steam Web Cookie from saved account tokens and retry failed authenticated GET requests once.

**Architecture:** Add one authenticated GET boundary to `SteamSession`. Route inventory, listings, pending listings, and price history through it while leaving writes and public price overview unchanged.

**Tech Stack:** Python 3, requests, Flask, existing script-style tests.

## Global Constraints

- Never expose or log token values.
- Retry only GET reads and at most once.
- Do not retry on HTTP 429 or 5xx.
- Do not alter write operations.

---

### Task 1: Authenticated GET retry boundary

**Files:**
- Modify: `steam_session.py`
- Create: `test_authenticated_reads.py`

**Interfaces:**
- Produces: `SteamSession.authenticated_get(url, **kwargs) -> requests.Response`.

- [ ] Add tests for login HTML, 401, normal JSON, and 429 behavior.
- [ ] Run tests and observe failure because the method is absent.
- [ ] Implement one-shot Cookie renewal and retry.
- [ ] Run tests and confirm all cases pass.

### Task 2: Route account reads through the boundary

**Files:**
- Modify: `steam_session.py`
- Modify: `app.py`
- Modify: `test_price_fallback.py`

**Interfaces:**
- Consumes: `SteamSession.authenticated_get` from Task 1.

- [ ] Change inventory, listings, pending listings, and price history GET calls.
- [ ] Keep public price overview and all POST requests unchanged.
- [ ] Run focused regression scripts and compile checks.
