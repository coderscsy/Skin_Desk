# Skin Desk · Steam 库存与挂单管理面板

一个本地运行的 Steam 库存、挂单、成本与批量操作面板，主要用于 CS2 等 Steam 市场物品的持仓管理、价格计算、批量上架/下架和状态同步。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

## 功能

- Steam 账号密码或 Cookie 登录，并保持本地会话
- 从 Steam 库存选择物品，支持设置默认加载游戏
- 同步库存、正式挂单、待确认数量
- 购入成本、上架价、到手余额、余额折扣、盈亏自动计算
- 批量上架、批量下架、批量确认、后台任务进度
- 检测和清理隐藏待确认记录
- 操作流水记录
- 兼容本机加速器代理和可选 HTTPS 证书校验设置

余额折扣计算：

```text
余额折扣 = 购入成本 / Steam 实际到手余额 × 100%
```

例如成本 ¥3.45，到手余额 ¥4.57，余额折扣约为 75.49%，也就是约 7.55 折。成本为 0 的游戏掉落物品也可以记录，盈亏会按零成本单独展示。

## 下载使用

如果你只是想运行，不想折腾 Git：

1. 在 GitHub Release 下载 `SkinDesk-vx.x.x.zip`。
2. 解压到任意英文或中文目录。
3. 安装 Python 3.10 或更高版本，并勾选 `Add Python to PATH`。
4. 双击 `启动倒货台.bat`。
5. 浏览器打开 <http://127.0.0.1:8777>。

首次启动脚本会自动创建 `.venv` 并安装依赖。之后再启动会复用同一个虚拟环境。

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

1. 打开网页后，先进入账号/设置区域登录 Steam。
2. 如果你使用本机 Steam 加速器，在设置里填写代理地址，例如 `http://127.0.0.1:7890`。
3. 如果加速器会重签 HTTPS 证书，可以关闭 HTTPS 校验；仅建议在可信本机代理下使用。
4. 点击“从 Steam 库存选择”，选择游戏后点击“加载库存”。
5. 选中物品加入监控表。
6. 填写购入成本、上架价或到手价，系统会自动反推余额折扣和盈亏。
7. 勾选需要操作的物品：
   - “批量上架”：按本次操作数量和上架价提交 Steam 挂单。
   - “批量下架”：先读取 Steam 当前正式挂单数量，再按实际数量下架。
   - “批量确认”：需要本地 `maFile`，否则仍需手机 Steam Guard 手动确认。
8. 点击“同步 Steam”可以让本地表格与 Steam 库存/挂单状态对齐。

## Steam 加速 / 代理配置

本工具不是 Steam 客户端插件，而是本地 Python 程序直接访问 Steam 网页接口，例如：

```text
https://steamcommunity.com/inventory/...
https://steamcommunity.com/market/...
```

因此，“打开了游戏加速器”不一定等于本工具已经被加速。推荐使用能提供本地 HTTP 代理端口的工具，例如 Clash / Mihomo、Watt Toolkit / Steam++ 等，然后在本工具设置里填写代理地址：

```text
http://127.0.0.1:7890
```

端口以你的代理工具实际显示为准。

UU 加速器通常主要加速游戏或 Steam 客户端流量，不一定会接管 Python 程序访问 `steamcommunity.com` 的请求。如果使用 UU 时出现 `ConnectionResetError(10054)`、连接超时、库存加载失败等问题，可以尝试：

1. 在 UU 中选择 Steam 社区 / Steam 市场 / Steam 商店相关加速项，而不是只加速 CS2。
2. 如果 UU 没有明确提供 `127.0.0.1:端口` 形式的 HTTP 代理，本工具里的代理地址请保持为空。
3. HTTPS 校验默认保持开启；只有在可信本机代理会重签证书并导致证书错误时，才临时关闭。
4. 如果仍然失败，建议改用带本地 HTTP 代理端口的工具。

## 上架价格说明

表格里的“上架价”就是买家看到的 Steam 挂牌价。系统会按 Steam 手续费计算实际到手余额：

```text
到手余额 = 上架价 - Steam 手续费
```

你可以只填“上架价”，不需要再手动输入其他价格。购入成本和上架价都填写后，会自动计算余额折扣、到手余额、盈亏。

## 批量下架与同步说明

批量下架以 Steam 当前正式挂单为准，不直接相信本地数量。比如你本地记录 64 件，但 Steam 实际正式挂单只有 62 件，则确认框会按 62 件下架。

“待确认”记录不算正式挂单。如果 Steam 手机确认被吞或网页状态卡住，可以先同步，再使用清理待确认功能处理隐藏记录。

## 自动确认说明

自动确认需要 Steam Guard `maFile`，并且文件内必须包含 `identity_secret` 和 `device_id`。普通手机 Steam 令牌不会自动在电脑生成 `maFile`。

请注意：`maFile` 等同于手机令牌核心密钥，泄露后账号风险很高。不要上传到 Git，不要发给别人，不要放进 release 包。

## Docker 运行

```bash
docker compose up -d --build
```

访问：

```text
http://127.0.0.1:8777
```

数据会保存到 `./data` 目录。远程访问前务必确认网络环境安全，不建议直接暴露到公网。

## 本地数据与安全

以下文件包含账号、令牌、Cookie 或个人交易数据，已被 `.gitignore` 排除，禁止提交或分享：

```text
secret.json
steam_login.json
steamguard.json
*.maFile
watchlist.json
settings.json
operations.json
data/
```

如果你曾经把账号密码、Cookie、Steam API Key 或 `maFile` 发给别人，建议立即修改 Steam 密码、重新生成 API Key，并重新检查移动令牌安全。

## 项目文件

- `app.py`：Flask 后端、任务、同步和定价逻辑
- `steam_session.py`：Steam 登录、库存、市场、确认接口
- `index.html`：本地 Web 管理界面
- `启动倒货台.bat`：Windows 一键启动脚本
- `requirements.txt`：Python 依赖
- `Dockerfile` / `docker-compose.yml`：容器部署配置
- `test_*.py`：离线回归测试

## 测试

```bash
python test_steam_session.py
python test_multigame.py
```

## 风险提示

Steam 接口和市场页面可能随时调整；批量市场操作可能触发限流或账号风控。请先小批量测试，并自行承担使用风险。本项目与 Valve、Steam 无关联。

## License

本项目采用 [MIT License](LICENSE) 开源。
