# BTCUSDT 一小时深度数据与盈亏研究

本目录包含 2026-11-01 的一小时采集原始数据、完整可运行项目和全部回测产物。严格回测窗口为 UTC 03:12:26.715261–04:12:26.715261，前40分钟训练、后20分钟测试。

## 下载内容

- [raw-data.tar.gz](raw-data.tar.gz)：完整采集目录，含 Binance 快照/增量/成交原始流、秒级对齐数据、Variational 报价和原始流、时钟探测、元数据、采集状态、旧版分析结果及参考脚本。解压到 `data/`。
- [project-complete.tar.gz](project-complete.tar.gz)：完整项目代码、配置、测试、全部 CSV/JSON/HTML 回测结果及运行日志，解压到 `project/`。仅排除 Python 字节码缓存。
- [project/](project/)：可直接浏览的代码与主要结果；完整逐点曲线和所有场景逐笔数据在上述项目压缩包中。
- [MANIFEST.json](MANIFEST.json)：两个压缩包及其中每个文件的字节数和 SHA256。
- [SHA256SUMS](SHA256SUMS)：压缩包校验值。

压缩包保留文件原始字节，未改写原始数据或历史报告。历史报告中本机绝对路径是运行来源记录，异机复现使用下方显式 `--data` 参数。

## 快速复现

克隆仓库并进入本目录后，先校验、解压，再执行：

```bash
sha256sum -c SHA256SUMS
tar -xzf raw-data.tar.gz
tar -xzf project-complete.tar.gz
python3 verify_bundle.py
cd project
python3 -m unittest discover -s tests -v
python3 -m depth_research --data ../data/full_var_20261101T031206Z --config config.json --output reproduced
python3 scripts/verify_results.py --data ../data/full_var_20261101T031206Z --results reproduced
```

项目仅使用 Python 标准库。复现结果写入 `reproduced/`，保留原始 `results/`。历史结果的校验脚本记录了原运行机路径，因此跨机器应验证新生成的 `reproduced/`；已归档历史结果的文件完整性由 `verify_bundle.py` 验证。

## 结果

每笔0.01 BTC、100ms延迟，测试段17笔：零费净盈亏 +0.576596 USDT，胜率52.94%，市值最大回撤0.100200 USDT。每边1 bps费用后净盈亏 −2.067844 USDT；盈亏平衡手续费约每边0.218041 bps。整小时包含训练数据，零费拼接盈利1.688296 USDT。

费用是研究假设，点差与逐档冲击已体现在成交价中。只有这一小时且已被探索过，不能据此证明长期或实盘有效。

原项目验证：21项测试通过，20组手续费×延迟场景，656,033项输出核验通过。HTML报告请下载后在浏览器打开：[report.html](project/results/report.html)。详细方法见 [项目说明](project/README.md)。
