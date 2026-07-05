# Skin Desk · Steam 饰品倒货台

一个本地运行的 Steam 库存、挂单与成本管理面板，支持多游戏库存同步、余额折扣计算、批量上架/下架、状态核对与操作流水。

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

## 功能

- Steam 账号密码或 Cookie 登录，并自动维护网页登录状态
- CS2、Rust、TF2、Dota 2 等库存读取与同步
- 库存、正式在架、待确认数量分离
- 购入成本、Steam 到手余额、余额折扣和盈亏计算
- 批量上架、下架、失败重试和后台任务进度
- 隐藏待确认记录检测与清理
- 操作流水和多游戏默认库存设置
- 本机加速器代理兼容

余额折扣计算：

```text
余额折扣 = 购入成本 ÷ Steam 实际到手余额 × 100%
```

例如成本 ¥3.45、到手余额 ¥4.57，余额折扣约为 75.49%，即约 7.55 折。

## Windows 快速开始

1. 安装 Python 3.10 或更高版本，并勾选 `Add Python to PATH`。
2. 双击 `启动倒货台.bat`。
3. 浏览器访问 <http://127.0.0.1:8777>。

也可以手动启动：

```bash
python -m venv .venv
.venv\Scripts\pip install flask requests
.venv\Scripts\python app.py
```

## Docker

```bash
docker compose up -d --build
```

远程访问前务必设置网页访问密码，不要将服务直接暴露到公网。

## 安全说明

以下文件包含账号、令牌或个人交易数据，已被 `.gitignore` 排除，禁止提交或分享：

```text
secret.json
steam_login.json
steamguard.json
*.maFile
watchlist.json
settings.json
operations.json
```

自动确认需要用户自行提供含 `identity_secret` 和 `device_id` 的 maFile。该文件等同于手机令牌核心密钥，请妥善保管。

关闭 HTTPS 校验仅适用于会重签证书且完全可信的本机加速器。

## 测试

```bash
python test_steam_session.py
python test_multigame.py
```

## 项目文件

- `app.py`：Flask 后端、任务、同步和定价逻辑
- `steam_session.py`：Steam 登录、库存、市场与确认接口
- `index.html`：本地 Web 管理界面
- `启动倒货台.bat`：Windows 启动脚本
- `Dockerfile` / `docker-compose.yml`：容器部署
- `test_*.py`：离线回归测试

## 风险提示

Steam 接口和市场页面可能随时调整；批量市场操作也可能触发限流或账号风控。请小批量测试并自行承担使用风险。本项目与 Valve、Steam 无关联。

## License

本项目采用 [MIT License](LICENSE) 开源。
