<div align="center">

# 📈 A股智能量化工作台

**自托管、零运维的 A 股「选股 + 监控 + 回测」量化工作台**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-≥3.11-blue.svg)](https://www.python.org/)
[![React](https://img.shields.io/badge/React-18-61dafb.svg)](https://react.dev/)
[![Data: TickFlow](https://img.shields.io/badge/Data-TickFlow-00b386.svg)](https://tickflow.org/auth/register?ref=V3KDKGXPEA)
[![Deploy: Docker](https://img.shields.io/badge/Deploy-Docker-2496ed.svg)](./Dockerfile)

基于 [TickFlow](https://tickflow.org/auth/register?ref=V3KDKGXPEA) 数据 · 🚀 **开箱即用**(单容器 / Free 模式)
能力驱动,适配 Free → Expert 全档位订阅 · 🔌 **自由接入第三方扩展数据**(例如 Tushare、自有量化项目数据)

**[核心功能](#-核心功能)** · **[快速开始](#-快速开始)** · **[配置](#️-配置)** · **[路线图](#-路线图)**

</div>

> **⚠️说明**:目前项目基于[TickFlow](https://tickflow.org/auth/register?ref=V3KDKGXPEA)数据源。无需付费即可使用策略+回测等功能.自有个性化数据源可接入扩展数据使用。

 如果觉得有用,请Star支持一下,蟹蟹🌹


---

## 🎯 项目定位

让任何**个人散户 / 量化爱好者**,**零运维**地拥有一套**与自己订阅档位严格匹配**的 A 股分析、选股、监控工作台。  
基于 [TickFlow](https://tickflow.org/auth/register?ref=V3KDKGXPEA) Key **低成本**获取数据。**填写邀请码 `V3KDKGXPEA` 免费领取概念行业等扩展数据**。<br>
**任意接入第三方数据**(Tushare 等),页面可视化自定义配置扩展数据表。

**项目所需配置**:

| 配置项 | 说明 | 是否必填 |
| :--- | :--- | :--- |
| **TickFlow API Key** | 数据源凭证,留空启用 None 模式 | 可选 |
| **AI 大模型 API Key** | 用于 AI 生成策略、个股分析(开发中)、行情分析(开发中)等,任意 OpenAI 兼容接口,留空关闭 | 可选 |

<table>
  <tr>
    <td width="50%" align="center"><b>看板 Dashboard</b></td>
    <td width="50%" align="center"><b>策略 Screener</b></td>
  </tr>
  <tr>
    <td width="50%"><img src="./docs/screenshots/dashboard.png" alt="看板页面" title="看板页面"></td>
    <td width="50%"><img src="./docs/screenshots/screener.png" alt="策略页" title="策略页"></td>
  </tr>
  <tr>
    <td width="50%" align="center"><b>回测 Backtest</b></td>
    <td width="50%" align="center"><b>监控中心 Monitor</b></td>
  </tr>
  <tr>
    <td width="50%"><img src="./docs/screenshots/backtest.png" alt="回测页" title="回测页"></td>
    <td width="50%"><img src="./docs/screenshots/monitor.png" alt="监控中心" title="监控中心"></td>
    
  </tr>
  <tr>
    <td width="50%" align="center"><b>连板梯队 Limit Ladder</b></td>
    <td width="50%" align="center"><b>概念分析 Concept</b></td>    
  </tr>
  <tr>
    <td width="50%"><img src="./docs/screenshots/limit-ladder.png" alt="连板梯队页" title="连板梯队页"></td>
    <td width="50%"><img src="./docs/screenshots/concept-analysis.png" alt="概念分析" title="概念分析"></td>
  </tr>
</table>

> ### ⚠️ 🚧 项目持续优化,功能陆续开放,敬请期待。

> **明确不做**:不对标同花顺/通达信的全功能股票软件;不内置任何「AI 荐股 / 涨停预测」。

---

## ✨ 核心功能

### 🔍 选股引擎(Screener)

**20 个内置策略** —— 每个策略是一个独立 Python 文件(`backend/app/strategy/builtin/`),基于 Polars 表达式实现:

| 类型 | 代表策略 |
| :--- | :--- |
| 趋势 | 趋势突破 · 均线多头 · 缩量回踩 |
| 形态 | MA 金叉 · MACD 金叉放量 · 布林突破 |
| 量价 | 量价齐升 · 高换手强势 · 强势高开 |
| 涨停 | 连板股 · 断板反包 · 逼近涨停 · 涨停动量 |
| 反转 | 超跌反弹 · 超卖反转 · 新低反转 |
| 波动 | 低波动龙头 · 回踩 MA20 反弹 |

- **自定义信号系统** —— 在 UI 上用 `字段 + 操作符 + 阈值` 组合(entry / exit / both),编译成 Polars 表达式热加载,**无需写代码**即可定义自己的买卖信号。
- **策略商店** —— 内置策略 + 用户自定义策略统一管理,支持参数覆盖(`params` 暴露阈值)。

#### ➕ 添加自己的策略

除 20 个内置策略外,你可以用两种方式扩展:

| 方式 | 说明 | 前提 |
| :--- | :--- | :--- |
| **🤖 AI 生成** | 用自然语言描述策略思路,LLM 读取 [strategy-guide.md](./docs/strategy-guide.md) 自动生成完整 Polars 策略文件(经 `ast` 安全校验,限定 `import polars as pl`)。生成后落入 `data/strategies/ai/`,即刻可用 | 需先在 [配置](#️-配置) 中填入 AI Key |
| **📝 代码自定义 / 策略迁移** | 参照 [策略开发指南](./docs/strategy-guide.md) 的文件结构模板,把你**已有的自有策略**改写为 Polars 文件放入 `data/strategies/custom/`(文件名/ID 建议 `custom_时间戳`),引擎自动发现加载——**轻松迁移你现成的量化项目策略**,无需从头重写 | 无 |
| **🎛️ 自定义信号配置** | 不写代码,在 UI 上用 `字段 + 操作符 + 阈值` 组合(entry / exit / both),编译成 Polars 表达式热加载,即可定义自己的买卖信号 | 无 |

> 引擎按 `source` 标记来源:`builtin`(内置)/ `custom`(手写或迁移)/ `ai`(生成),三者统一进入策略商店管理。

### 📊 指标流水线(Indicators)

原生 Polars 向量化计算,全 A 股一次扫表落盘为 enriched Parquet:

| 分类 | 指标 |
| :--- | :--- |
| 均线系 | MA(5/10/20/30/60)· EMA(5/10/12/20/26/30/60) |
| 趋势系 | MACD(DIF/DEA/HIST)· 动量(5/10/20/30/60d)· 布林带(上/下轨) |
| 震荡系 | RSI(可配周期)· KDJ(K/D/J) |
| 波动系 | ATR(14)· 年化波动率(20d)· 振幅 |
| 量能系 | 量比(5d/10d)· 量均线 |
| 涨跌停 | 涨停信号 · 连板数 · 涨跌幅 · 涨跌额 |
| 原子信号 | MA 金叉/死叉 · MA20 突破/跌破 · MACD 金叉/死叉 · N 日新高/新低 · 布林突破 |
| 复权 | 基于除权因子自动计算前复权(`ex_factor` / `cum_factor`),回测与指标一致 |

### 🧪 回测引擎(Backtest)

基于 vectorbt:

- **三种回测模式**:个股 · 策略组合 · 自由信号组合
- **真实约束**:T+1 · 手续费 · 滑点(基点) · 止损 · 最大持仓天数
- **组合管理**:最大持仓数 · 最大敞口 · 等权 / 自定义仓位
- **SSE 流式进度**:长任务实时推送进度,支持刷新 / 切页后**重连恢复**(相同参数任务只启动一次)
- **统计输出**:净值曲线 · 夏普 · 最大回撤 · 胜率 · 每笔交易明细

### 📡 监控中心(Monitor)

**统一监控规则引擎** —— 一个页面管理所有类型的监控,实时推送 + 持久化触发记录:

- **四类监控**:策略监控 · 个股信号监控(选信号即加) · 个股价格/涨跌监控 · 全市场异动监控
- **灵活条件**:多条件 AND/OR 组合 + 冷却期去重(防刷屏) + 严重级别(info/warn/critical)
- **多入口配置**:监控中心页面新建规则 · 个股详情页「加监控」· 策略卡片一键开启
- **实时 SSE 推送**:命中规则后右下角弹窗通知(可配声效) + 持久化到 `alerts.jsonl`
- **触发记录**:时间倒序展示,支持按来源过滤 · 单条删除 · 清空 · 点击查看个股日K
- **菜单未读徽标**:离开监控中心后有新触发,菜单显示未读数;进入页面后清零

### 🤖 AI 策略生成(可选)

- **自然语言 → 策略代码**:用一句话描述策略思路,LLM 读取 `docs/strategy-guide.md` 生成完整 Polars 策略文件
- **沙箱约束**:生成代码经 `ast` 校验、限定 `import polars as pl`,避免逐行循环,优先向量化表达
- **可插拔**:留空 AI 配置即跳过整个模块,不影响核心功能

### 🧰 数据与扩展

- **多源数据**:TickFlow 日 K / 分钟 K / 指数 / 财务(利润 / 资产负债 / 现金流)/ 自选行情
- **🔌 第三方数据接入(重点)** —— TickFlow 之外的数据也能用:
  - 支持 **Tushare** 等第三方数据源,通过 **HTTP 定时拉取**自动入库
  - 支持 **CSV / Excel 上传** · **JSON 写入**,自动 schema 发现与符号归一
  - **页面可视化配置**扩展数据表,无需改代码
  - 可接入**你自己的量化项目数据**,统一并入 DuckDB 查询面,与内置数据同台分析
- **盘后定时管道**:APScheduler 15:30 CST 自动拉日 K + 重算 enriched 表 + 跑监控
- **令牌桶限流**:适配各档位 rpm / batch 上限,批量合并 + 增量拉取,同一份数据多面板复用

---

## 🚀 快速开始

### 前置依赖

| 工具 | 版本 | 安装 |
| :--- | :--- | :--- |
| Python | ≥ 3.11 | [python.org](https://www.python.org/) |
| Node | ≥ 20 | [nodejs.org](https://nodejs.org/) |
| [`uv`](https://docs.astral.sh/uv/) | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `pnpm` | 9 | `npm i -g pnpm` 或 `corepack enable && corepack prepare pnpm@9 --activate` |



### 方式 A:Dev 模式(二次开发,最推荐)

```bash
cp .env.example .env       # 填 TICKFLOW_API_KEY,留空则启用 Free 试用
```

**一键启动**(推荐,自动检查\下载依赖 / 释放端口 / 同时起前后端,Ctrl-C 一并关闭):

| 平台 | 命令 |
| :--- | :--- |
| **macOS / Linux** | `./dev.sh` |
| **Windows (PowerShell)** | `.\dev.ps1` |



### 方式 B:Docker(最省心,可部署)

```bash
cp .env.example .env       # 按需填写 Key(留空即 Free 模式,可直接体验)
docker compose up --build
# 打开 http://localhost:3018
```


首次运行会自动安装前后端依赖(约 1-2 分钟),之后直接启动:

- 后端 → <http://localhost:3018>
- 前端 → <http://localhost:3011>

自定义端口:`BACKEND_PORT=8000 FRONTEND_PORT=5173 ./dev.sh`(Windows:`.\dev.ps1 -BackendPort 8000 -FrontendPort 5173`)

<details>
<summary>手动分别启动(备选)</summary>

```bash
# 终端 1:后端
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 3018

# 终端 2:前端
cd frontend
pnpm install
pnpm dev                   # http://localhost:3011
```

</details>

> **启用回测**:`cd backend && uv sync --extra backtest`
> vectorbt → numba 体积较大,故作为可选 extras。macOS / Intel 无预构建 wheel 时需 `brew install cmake` 现场编译。

---

## 🧭 第一次使用

1. 打开面板 → **设置 → 凭据与能力** → 点 **重新检测**,确认 Tier Label
2. 点 **立即跑盘后管道** —— 拉日 K + 计算 enriched 表
   - **Free 用户**:只同步内置 DEMO_SYMBOLS(浦发 / 招商 / 茅台等 10 只)
   - **Starter+**:同步全 A 或可获取的 instruments 列表
3. **自选**页:添加跟踪标的;点代码进 **K 线**页看蜡烛图 + 买卖点
4. **选股**页:点任一内置策略卡片即时扫描;或用自定义信号组合条件
5. **回测**页:选策略 / 信号 + 时间区间 → 跑回测 → 看净值 / 夏普 / 交易明细(SSE 实时进度)
6. **监控中心**页:配置监控规则(策略/个股信号/价格/市场异动),盘中 SSE 实时弹窗通知 + 持久化触发记录;或在个股详情页点「加监控」快速添加

---

## ⚙️ 配置

所有配置通过项目根目录的 `.env` 文件读取(复制 `.env.example` 开始)。配置也可在面板 **设置** 页面内修改。

### 数据源:TickFlow

TickFlow 提供订阅制 A 股数据。**留空 `TICKFLOW_API_KEY` 即启用 Free 模式,无需注册即可体验**。

```ini
TICKFLOW_API_KEY=              # 留空 = Free 模式;填入 Key = 按订阅档位解锁
```

> 完整能力矩阵见 [tickflow.org/pricing](https://tickflow.org/pricing/)。系统启动时会自动探测你的真实能力集,UI 显示「≈ Pro」等友好标签。

### AI(可选):策略生成

AI 模块用于「自然语言生成策略代码」。**所有配置留空即跳过 AI 功能,不影响核心使用**。支持任何 **OpenAI 兼容接口**:

```ini
AI_PROVIDER=openai_compat              # openai_compat | ollama
AI_BASE_URL=https://api.deepseek.com/v1
AI_API_KEY=                            # 留空 = 关闭 AI
AI_MODEL=deepseek-chat
AI_DAILY_TOKEN_BUDGET=500000           # 每日 token 预算上限
```

> 切换 `AI_PROVIDER=ollama` 时无需 `AI_API_KEY`,适合本地部署大模型。

### 服务与数据

```ini
HOST=0.0.0.0          # 监听地址
PORT=3018             # 服务端口
LOG_LEVEL=INFO        # DEBUG | INFO | WARNING | ERROR
DATA_DIR=./data       # Parquet / DuckDB 数据存储目录
```

---

## 🏗️ 技术栈

| 层 | 选型 |
| :--- | :--- |
| **后端** | FastAPI · Pydantic v2 · APScheduler · sse-starlette |
| **数据** | Polars(计算)· DuckDB(查询)· Parquet(存储)· PyArrow |
| **回测** | vectorbt(全项目唯一 pandas 边界) |
| **数据源** | [TickFlow](https://tickflow.org/auth/register?ref=V3KDKGXPEA) 官方 SDK(`tickflow[all]`) |
| **AI**(可选) | OpenAI 兼容接口(DeepSeek / 通义 / Ollama 等) |
| **前端** | React 18 · Vite · TypeScript · Tailwind CSS · Framer Motion · Tanstack Query · Lightweight Charts · ECharts · dnd-kit |
| **部署** | Docker 两阶段构建,前端 dist 拷进后端镜像,**单容器** |

---

## 🗺️ 路线图

| Phase | 内容 | 状态 |
| :--- | :--- | :--- |
| **0** | 仓库骨架 / FastAPI 壳 / Vite + React SPA / Docker 一键起 | ✅ |
| **1** | 能力探测 + Kline 同步 + K 线分析页 | ✅ |
| **2** | Polars enriched 流水线 + Screener + 信号扫描 | ✅ |
| **3** | vectorbt 回测 + T+1 + 手续费 + 止损 + max-hold | ✅ |
| **4** | 监控引擎 + 告警规则 + Webhook + APScheduler 盘后定时 | ✅ |
| **5** | 统一监控中心 + 四类监控规则 + 实时推送 + 持久化触发记录 + 声效通知 | ✅ |
| **v2** | Webhook 推送(QMT/掘金下单) · 板块异动 · 早晚报 · 更多扩展 | 🚧 |

---

## 📚 文档

- [docs/strategy-guide.md](./docs/strategy-guide.md) —— 策略开发指南(AI 生成器与手写策略的规范)
- [docs/](./docs) —— 策略构建步骤、示例

---

## 🤝 贡献

欢迎 Issue 和 PR。本地开发:

```bash
cd backend && uv sync --extra backtest   # 含回测依赖
cd ../frontend && pnpm install && pnpm dev
```

新增内置策略:在 `backend/app/strategy/builtin/` 参照现有策略文件,实现 `StrategyDef` 即可被引擎自动发现。

---

## ⚠️ 免责声明

本项目仅供**学习与量化研究**,**不构成任何投资建议**。回测结果不代表未来收益。A 股有风险,入市需谨慎。数据准确性以数据源 TickFlow 官方为准。

---

## 📄 License

[MIT](./LICENSE) © tickflow-stock-panel contributors

本项目依赖 [TickFlow](https://tickflow.org/auth/register?ref=V3KDKGXPEA) 提供数据服务,使用前请遵守其服务条款。

## 社区

本开源项目已链接并认可 [LINUX DO 社区](https://linux.do)。
