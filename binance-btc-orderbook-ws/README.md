# Binance BTC 盘口 WebSocket 采集程序

现有采集程序的可移植源码包，采集 Binance **现货 BTCUSDT**，不是合约盘口。仅获取行情，不下单。Python 3.11；72 小时管理脚本需要 Linux/macOS（使用 `fcntl`）。

## 安装

```bash
cd binance-btc-orderbook-ws
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## 五档盘口（独立 Binance 采集）

```bash
python collect.py --seconds 1200 --out data/depth5-session-001
```

订阅 BTCUSDT 和 BNBUSDT 的 `depth5@100ms`。保留原始五档消息 `events.jsonl`、每秒采样 `samples_1s.jsonl`、连接日志 `connection.jsonl` 和进度 `status.json`。每秒样本标记 `ok/stale/missing`，不以旧盘口填充缺失数据。每次运行使用新的输出目录，以免追加到旧数据中。

## BTC 完整盘口重建及 Variational 对齐采集

```bash
python collect_full_var.py --selftest
python collect_full_var.py --seconds 3600 --out data/full-session-001
```

此脚本同时运行 Binance BTC 现货深度增量、逐笔成交和 Variational BTC 永续标记价格采集。Binance 使用每侧最多 5000 档 REST 初始快照加 `depth@100ms` 增量重建盘口；检测序号缺口后重新同步。不是无限深度，聚合结果带初始快照覆盖边界。

输出目录必须不存在，`--seconds` 范围 1–3600。启动后最多等待 60 秒，需 Binance 和 Variational 标记价格均就绪才开始采样；因此不能把此入口当作纯 Binance 独立程序。实际总运行时间包含预热。

主要输出：

| 文件 | 内容 |
| --- | --- |
| `binance_snapshots.jsonl` | 初始及重连 REST 快照 |
| `binance_raw.jsonl` | 原始深度增量与成交消息 |
| `aligned_1s.jsonl` | 每秒盘口前 20 档、成交价周围每侧 6 个一美元区间、跨平台行情及质量标记 |
| `var_ws_raw.jsonl` | Variational 标记价格消息 |
| `var_quotes.jsonl` | 0.01 BTC RFQ 报价或失败状态 |
| `metadata.jsonl`, `schedule.json` | 采集元信息及计划 |
| `connections.jsonl`, `clock_probes.jsonl`, `status.json` | 连接、时钟和进度记录 |

对齐使用本机单调时钟的因果 as-of 选择，保留交易所时间和本机接收时间，标记过期、缺失和对齐质量。USDT 现货与 USDC 永续是不同合约；标记价格不是可执行报价。

Variational RFQ 如需已有的有效站点 cookie，可设置：

```bash
export VAR_COOKIE_PATH=/absolute/path/to/variational_cf.json
```

文件格式为 `{"ua":"your browser user agent","cookies":{"cookie_name":"cookie_value"}}`。默认路径是 `~/.config/binance-depth-ws/variational_cf.json`；包内不含认证信息。没有文件或报价失败时，RFQ 会记录无效状态，其他行情仍可采集。请将实际认证文件保存在仓库外。

## 72 小时分段采集

```bash
python capture_72h.py --out data/capture-72h-session-001
```

管理脚本调用 `collect_full_var.py`，每段最多采样 3600 秒；首次启动建立 72 小时固定截止时间，复用相同目录会沿用原截止时间。每秒检查磁盘，保留空间为 10 GiB 与磁盘容量 20% 中较大者。段间重连及预热会产生空档，不保证连续无缺口。状态见 `run.json` 和 `supervisor_status.json`。

## 验证与打包

```bash
python collect_full_var.py --selftest
sha256sum -c SHA256SUMS
```

`source.tar.gz` 包含三份源码、依赖、说明和 `.gitignore`。本目录为程序包，历史采集数据见仓库中的 [depth-pnl-research-20261101](../depth-pnl-research-20261101/)。
