# Steam Token 读取会话设计

## 目标

让需要登录的 Steam 读取接口统一使用本地账号 token 派生的 Web Cookie；当 Cookie 过期时自动续期并重试一次。

## 边界

- 仅处理 GET 读取请求：库存、价格历史、正式挂单、待确认记录。
- 不自动重试上架、撤单、确认等写请求，避免重复提交。
- `access_token` 和 `refresh_token` 只留在 Python 后端，不返回前端、不写日志。
- token 不用于规避 HTTP 429；限流仍按现有缓存和冷却逻辑处理。
- 公共 `priceoverview` 保持匿名请求。

## 流程

1. 读取请求先使用当前 `steamLoginSecure` 和 `sessionid`。
2. 响应为 401、403，或返回 Steam 登录页 HTML 时，判定 Web 会话失效。
3. 清理失效的 `steamLoginSecure`，优先使用现有 access token 重建 Cookie；失败时由现有逻辑回退 refresh token。
4. 保存更新后的 Cookie，并重试原 GET 请求一次。
5. 第二次响应原样交给调用方，不继续循环重试。

## 验收

- 登录页 HTML 和 401/403 会触发一次续期与一次重试。
- 429、5xx 和正常 JSON 不触发会话续期。
- 四类账号读取接口使用统一读取方法。
- 写接口行为不变。

