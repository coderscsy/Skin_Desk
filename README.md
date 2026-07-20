# Skin Desk · Steam 库存与挂单管理面板

一个本地运行的 Steam 库存、挂单、成本与批量操作面板，主要面向 CS2 等 Steam 市场物品的持仓管理、价格参考、批量上架/下架、Steam 状态同步和盈利统计。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

## 主要功能

- Steam 账号密码登录或 Cookie 登录，本地保存会话。
- 从 Steam 库存选择物品，支持设置默认加载游戏。
- 同步 Steam 库存、正式挂单、隐藏待确认数量。
- 手动填写上架价，自动反算 Steam 到手余额、余额折扣和盈亏。
- 支持游戏掉落物品成本填 `0`，仍正常参与记录和统计。
- 批量上架、批量下架、批量确认、清理待确认记录。
- Steam 同步后自动判断“已挂出 / 待确认 / 回库 / 已售出”。
- 已售出记录自动变为只读，保留历史成交快照。
- 顶部统计本金、预计到手、已实现盈利、总盈利。
- 价格参考支持 Steam CNY、Steam 页面外币折算、BUFF 人民币参考价。
- 支持明亮 / 暗色主题。
- 支持本机 HTTP 代理和可选 HTTPS 证书校验。

## 下载使用

如果只想运行，不需要克隆仓库：

1. 到 GitHub Releases 下载 `SkinDesk-v0.3.0-source.zip`。
2. 解压到任意目录。
3. 安装 Python 3.10 或更高版本，安装时勾选 `Add Python to PATH`。
4. 双击 `启动倒货台.bat`。
5. 浏览器打开启动台显示的地址，默认是：

```text
http://127.0.0.1:8777
```

启动脚本会自动创建 `.venv` 并安装依赖。后续再次启动会复用同一个虚拟环境。

## 源码运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

访问：

```text
http://127.0.0.1:8777
```

## 基本使用流程

1. 打开网页后，先在 Steam 登录区域登录账号，或粘贴 Cookie 登录。
2. 如使用代理工具，在设置里填写本机 HTTP 代理，例如 `http://127.0.0.1:7890`。
3. 点击“从 Steam 库存选择”，选择游戏后加载库存。
4. 勾选物品加入监控表。
5. 填写购入成本和上架价。
6. 勾选需要操作的物品：
   - “批量上架”：按本次操作数量和上架价提交 Steam 市场挂单。
   - “上架并确认”：需要本地 Steam Guard `maFile`。
   - “批量下架”：按 Steam 当前正式挂单数量下架，不盲信本地数量。
   - “同步 Steam”：同步库存、挂单、待确认和售出状态。
7. 如果手机确认失败、确认被吞或数量对不上，先点“同步 Steam”，再按提示重新上架或清理待确认。

## 上架价与余额折扣

表格中的“上架价”就是买家在 Steam 市场看到的价格。

系统会按 Steam 手续费反算实际到手余额：

```text
到手余额 = 上架价 - Steam 手续费
```

余额折扣用于描述现金成本与 Steam 到手余额之间的关系：

```text
余额折扣 = 购入成本 / Steam 实际到手余额 × 100%
```

示例：购入成本 `¥3.45`，Steam 到手余额 `¥4.57`，余额折扣约 `75.49%`，约等于 `7.55 折`。

## 价格来源说明

价格展示优先级：

1. BUFF 人民币参考价
   如果 BUFF 能返回价格，表格会优先显示 BUFF 参考价。

2. Steam CNY 官方价格
   Steam `priceoverview` 成功返回人民币时，会显示 Steam CNY 价格。

3. Steam 页面外币折算
   如果 Steam CNY 接口限流，但 Steam 页面只返回 `SGD / USD / HKD` 等外币，会按内置汇率折算成人民币并标注“约”。

注意：

- BUFF 价格和 Steam 页面外币折算价只作为参考。
- 自动上架仍以你手动填写的“上架价”为准。
- Steam 限流不是本地程序错误，频繁刷新只会延长冷却。

## Steam 同步与已售出判定

点击“同步 Steam”后，本地记录会按 Steam 当前状态更新：

- 正式挂单数量 > 0：状态为“已挂出”。
- 隐藏待确认数量 > 0：状态为“待手机确认”。
- 原来已挂出 / 待确认，现在挂单为 0、待确认为 0、库存也为 0：判定为“已售出”。
- 原来已挂出 / 待确认，现在挂单为 0，但库存回来了：恢复为“盯价中”，可重新编辑或上架。

已售出记录会变为只读，只能查看走势和历史数据，不能再次编辑、上架、送出或参与批量操作。

## 统计口径

顶部统计包含：

- 持仓数：当前表格记录数量。
- 本金：`购入成本 × 数量`。
- 预计到手：未售出记录按当前计算值，已售出记录按售出快照。
- 已实现盈利：只统计已售出记录。
- 总盈利：已实现盈利 + 未售出记录的预计盈利。

游戏掉落成本为 `0` 的物品会正常计入数量，本金为 `0`。

## Steam 加速器 / 代理配置

本工具是本地 Python 程序直接访问 Steam 网页接口，例如：

```text
https://steamcommunity.com/inventory/...
https://steamcommunity.com/market/...
```

因此，“打开游戏加速器”不一定等于本工具已经被加速。

推荐使用能提供本机 HTTP 代理端口的工具，例如 Clash / Mihomo / Watt Toolkit / Steam++，然后在设置里填写：

```text
http://127.0.0.1:7890
```

端口以代理工具实际显示为准。

UU 加速器通常主要加速游戏或 Steam 客户端流量，不一定会接管 Python 程序访问 `steamcommunity.com` 的请求。如果使用 UU 时出现 `ConnectionResetError(10054)`、连接超时、库存加载失败等问题，建议：

1. 在 UU 中选择 Steam 社区 / Steam 市场 / Steam 商店相关加速项，而不是只加速 CS2。
2. 如果 UU 没有明确提供 `127.0.0.1:端口` 形式的 HTTP 代理，本工具里的代理地址保持为空。
3. HTTPS 校验默认开启。只有在可信本机代理重签证书导致证书错误时，才临时关闭。
4. 如果仍然失败，建议改用带本地 HTTP 代理端口的工具。

## 自动确认说明

自动确认需要 Steam Guard `maFile`，且文件内必须包含：

```text
identity_secret
device_id
```

普通手机 Steam 令牌不会自动在电脑生成 `maFile`。

注意：`maFile` 等同于移动令牌核心密钥。泄露后账号风险很高。不要上传到 Git，不要发给别人，不要放进 Release 包。

## Docker 运行

```bash
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8777
```

远程访问前务必设置强密码，不建议直接暴露到公网。

## 本地数据与安全

以下文件包含账号、令牌、Cookie、个人配置或交易数据，已被 `.gitignore` 排除，禁止提交或分享：

```text
secret.json
steam_login.json
steamguard.json
*.maFile
watchlist.json
settings.json
operations.json
price_cache.json
config.json
data/
```

如果曾经把账号密码、Cookie、Steam API Key 或 `maFile` 发给别人，建议立即修改 Steam 密码、重新生成 API Key，并检查移动令牌安全。

## 项目文件

- `app.py`：Flask 后端、任务、同步和定价逻辑。
- `steam_session.py`：Steam 登录、库存、市场、确认接口。
- `index.html`：本地 Web 管理界面。
- `启动倒货台.bat`：Windows 一键启动脚本。
- `requirements.txt`：Python 依赖。
- `Dockerfile` / `docker-compose.yml`：容器部署配置。
- `test_*.py`：离线回归测试。

## 测试

```bash
python test_steam_session.py
python test_multigame.py
python test_price_fallback.py
python test_sold_sync.py
python test_partial_relist.py
```

## 风险提示

Steam 接口和市场页面可能随时调整；批量市场操作可能触发限流或账号风控。请先小批量测试，并自行承担使用风险。

本项目与 Valve、Steam、网易 BUFF 无官方关联。

## License

本项目采用 [MIT License](LICENSE) 开源。
