# TradeFi / RWA 套利调查：当前剩余机会与不可执行的假价差

研究日期：2026-09-08（UTC）。核心 Hyperliquid 快照：07:36:04 UTC；盘口：07:36:37 UTC。本文是一次有时点的调查，不是持续运行的报价服务。报价、费用与门槛分别核查；没有下单，也没有登录券商账户取得可成交的对冲报价。

## 结论

**本次最有证据的候选是 trade.xyz 的油气 perp：Brent、WTI 做多，天然气做空，再用匹配月份的传统期货反向对冲。** 快照中的 mark/oracle 偏离分别约为 -0.466%、-0.351%、+0.394%；过去一周相应方向的 funding 费率累计也为正。它们尚不是已验证净利润的闭环：oracle 不是券商可成交价格，期货展期与融资成本尚未取得实时双边报价。

**没有证据支持“现在仍有稳定、低摩擦的股票现货 4% 套利”。** TSLAx 当前同一 CoinGecko 市场页的 Raydium 报价略低于 Kraken；搜索到的大幅 Ondo token 偏离混有未核验地址，不能当机会。xStocks 赎回规则已有重大变化。

**CBB 的最新公开披露直接改变策略解读：链上空单可能只是套利的一条腿。** 九月初他披露了 HIP-3 / TradeXYZ 与 IBKR 的对冲做市，又开始使用 Variational 的 US100、XAU swaps。跟踪他的单一钱包方向，可能恰好漏掉决定收益的另一条腿。

## 证据标准与计算口径

- **Fact-A**：官方 API 或官方产品文档；API 原始响应保存在 `./evidence/`。
- **Fact-B**：聚合数据、新闻或 X 镜像所展示的信息；标注时间与来源，不视作独立审计。
- **Calc**：仅用所列真实数值计算的派生量，不是额外观测事实。
- **Inference**：执行判断、机会排序与成因解释。
- mark 基差 = `(markPx / oraclePx - 1) × 100%`。这不同于 API 的 impact-price premium，更不同于买卖两腿的可成交利润。
- 小时 funding = API 小数值乘百分比换算。XYZ **每小时结算**，正数多付空、负数空付多；不能照搬聚合网站的每八小时标签。[XYZ 官方 funding](https://docs.trade.xyz/perp-mechanics/funding)
- 历史累计是逐小时费率之和，以每期 oracle 名义本金标准化，未复利、未扣费用；固定合约数量的实际美元收入须逐期乘 oracle 价格。历史平均不代表未来，也不代表保证金收益率。

## 一、当前可观察机会清单

### 实时基差与资金费

**Fact-A + Calc**：以下全部来自同一 `metaAndAssetCtxs` 响应；价格为美元计价的合约单位。24h 成交额不是盘口容量。来源：[Hyperliquid API](https://api.hyperliquid.xyz/info)，请求 `{"type":"metaAndAssetCtxs","dex":"xyz"}`；本地 [`hl_xyz.json`](evidence/hl_xyz.json)。

| 品种 | mark / oracle | mark 基差（Calc） | 当前 funding / 小时 | 当时收取方 | 24h 成交额（美元） |
|---|---:|---:|---:|---|---:|
| BRENTOIL | 98.32 / 98.78 | -0.4657% | -0.027587% | 多头收取 | 148,849,391 |
| NATGAS | 2.9827 / 2.971 | +0.3938% | +0.022581% | 空头收取 | 6,377,501 |
| CL | 93.979 / 94.31 | -0.3510% | -0.020411% | 多头收取 | 151,157,992 |
| GOLD | 4397.8 / 4398.2 | -0.0091% | +0.000625% | 空头收取 | 43,499,794 |
| MU | 1035.6 / 1035.0 | +0.0580% | +0.001460% | 空头收取 | 101,228,737 |
| SNDK | 1766.1 / 1766.9 | -0.0453% | -0.000888% | 多头收取 | 78,382,101 |
| BIRD | 2.4377 / 2.4494 | -0.4777% | -0.051139% | 多头收取 | 32,965 |

### 是否只是一瞬间：实际结算历史

**Fact-A + Calc**：历史窗口为 2026-09-01 08:00 至 2026-09-08 07:00 UTC，每个市场均取得 168 条小时记录；最近 24 条不是预测。来源为同一官方 API 的 `fundingHistory`，各文件含请求参数，例如 [`funding_BRENTOIL.json`](evidence/funding_BRENTOIL.json)。

| 品种 | 观察策略方向 | 一周 funding 收入率（Calc） | 最近 24 小时收入率（Calc） | 收取 funding 的小时数 |
|---|---|---:|---:|---:|
| BRENTOIL | 多 | +0.684156% | +0.304826% | 124/168 |
| NATGAS | 空 | +0.421430% | +0.192385% | 147/168 |
| CL | 多 | +0.607524% | +0.219728% | 115/168 |
| GOLD | 空 | +0.127454% | +0.013879% | 160/168 |
| MU | 空 | +0.121266% | -0.008276% | 147/168 |
| SNDK | 空 | +0.116113% | -0.002658% | 134/168 |

**Inference**：油气的持续性证据强于单张高 APR 截图。MU、SNDK 虽然一周空头收取为正，最近一天却净付费；不能因为 CBB 曾做空就机械长期收息。基差收敛收益和资金费可以分别记账，但不能把当前偏离与未来不断重复出现的偏离重复计算。

### 逐项执行判断

| 候选 / 等级 | 套利机制（Inference，须核实对冲腿） | 摩擦成本与时间 | 为什么存在 / 谁被挡在门外（Inference） |
|---|---|---|---|
| **Brent：优先研究** | 买入 `xyz:BRENTOIL`，在 IBKR 等券商卖出与 oracle 匹配的 Brent 期货或期货篮子；目标为负基差回归和负 funding 的多头收入 | XYZ growth mode 已启用；基础 taker 单边 0.009%、maker 0.003%。再加券商佣金、期货买卖差、展期成本、两处保证金；无 token 赎回费。须有券商 KYC 与期货权限 | 链上空头需求与传统期货资金不能自动互通；能在两个市场同步执行、调整合约月份的人较少 |
| **WTI CL：优先研究** | 买 `xyz:CL`、卖匹配 WTI 期货；不是拿 Brent 空头当作完全对冲 | 同上；growth mode 已核验。匹配期货处于开放时段才可能形成双腿；非原子成交 | 跨系统执行、交易时段、保证金与展期限制。与 Brent 比，应最终按双边净价差和容量选择 |
| **NATGAS：优先研究但容量较弱** | 卖 `xyz:NATGAS`、买匹配天然气期货，收正 funding，并观察溢价回归 | 同样的 growth 费率；API 显示 `noCross`，保证金安排须按该市场处理；买入期货的远期曲线与展期成本可能抵消 funding | 天然气的期限结构与波动使低成本长期对冲困难，小盘口也限制容量 |
| **GOLD：低优先级常规 carry** | 卖 `xyz:GOLD`，买匹配黄金现货/融资工具；如用黄金期货，另计现货—期货基差 | GOLD 未启用 growth，官方说明黄金不符合 growth 条件；基础 taker 单边 0.090%、maker 0.030%。还需黄金融资、保管/发行方或期货基差成本 | 当前费率更接近常规 carry，薄利易被交易费吞掉；不是见到正 funding 就有超额收益 |
| **MU / SNDK：事件型监控** | 只有在经校正的现货与 perp 双边成交价、资金费同时合适时，才建立对冲；依据当时费用方向选腿 | 券商股票空头借券、股息、token 份额变化、美国时段、两腿费用。没有拿到实时借券报价 | 财报/单边交易流可制造短时偏离；缺乏借券与高速行情者难持续回收。当前证据不足以推荐固定方向 |
| **BIRD：排除出优先执行** | 尽管负 funding 更大，不能按费率大小排序后直接买入 | 一档容量极小，借券与正确底层合约未核实；成交额亦明显低于油气 | 小市场的高显示费率可能只是微量订单造成，不等于可部署容量 |

费率来源：[XYZ 费用表](https://docs.trade.xyz/perp-mechanics/fees)、[Hyperliquid 费用规则](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/fees)。表中是无额外折扣的基础费率；账户等级、抵押币优惠、前端 builder fee、maker 成交概率需另外核实，不能把 maker 费当成必然可获得。

### 盘口容量检查

**Fact-A + Calc**：官方 `l2Book` 于 07:36:37 UTC 返回以下盘口。末列是上述策略开仓方向的一档价格乘数量，不是推荐下单额；盘口与 07:36:04 的 oracle **不同步**，因此不把二者相减当成净利润。文件例如 [`book_CL.json`](evidence/book_CL.json)。

| 品种 | 买一 | 卖一 | 策略开仓侧一档美元容量（Calc） |
|---|---:|---:|---:|
| BRENTOIL | 98.3 | 98.313 | 25,317.56 |
| NATGAS | 2.9827 | 2.9831 | 1,000.10 |
| CL | 93.966 | 93.973 | 7,389.66 |
| GOLD | 4398.1 | 4398.2 | 16,031.07 |
| BIRD | 2.4362 | 2.4444 | 88.98 |

**Inference**：NATGAS 即使日成交量可观，一档可用深度仍很有限；Brent、CL 也必须逐档算 VWAP。池子 TVL、24h 成交量和一档深度是不同概念。

### 油气最大技术陷阱：你的对冲合约必须与 oracle 相同

**Fact-A**：XYZ 贵金属引用流动性现货市场；能源和工业金属引用指定期货，并在每月第 5 至第 10 个工作日逐步换月。官方表列九月初 WTI 与 NATGAS 使用 V 月份代码、Brent 使用 X，之后按 roll schedule 转换。外部覆盖有日内维护窗口；外部行情不可用时会转内部价格发现。[商品定义](https://docs.trade.xyz/asset-directory/commodities)、[Oracle 机制](https://docs.trade.xyz/perp-mechanics/oracle-price)

**Inference**：执行前读取当月 roll schedule 与当前权重，不能仅凭“近月油”对冲。买 UNG 对冲 NATGAS、买 USO 对冲 CL、拿 PAXG 对冲任意黄金合约，都可能引入额外 tracking error。周末 oracle 内部移动更不是可向外部市场兑现的 NAV。

## 二、跨平台新线索：Variational swaps 与纯链上黄金双 perp

### Variational：CBB 最近主动尝试的对冲通道

**Fact-B**：CBB 的 X 镜像显示其开始建立 Variational swaps 仓位，自述 US100 单边成本 **0.3 bps**、XAU **0.7 bps**，并回复“one way”。这是本人交易体验，不是所有账户/所有规模的保证费率，也不是已发现的基差。[Sotwe 镜像](https://www.sotwe.com/Cbb0fe)、[Followin 日期索引](https://followin.io/en/kol/4076023946)

**Fact-A**：官方 swaps 文档确有 US100、XAU，以及 USOIL、UKOIL 等市场；swaps 基于传统机构流动性，融资按底层 carry 定价，多空费率分别报价且一般不对称。它们目前并非全天候交易；周末补计 funding、节假日和分红调整都影响实际现金流。[Variational Swaps](https://docs.variational.io/omni/trading/swaps)

**Inference / 待报价**：可把 `xyz:XYZ100 ↔ US100 swap`、`xyz:GOLD ↔ XAU swap`、`xyz:CL ↔ USOIL swap`、`xyz:BRENTOIL ↔ UKOIL swap` 加入监控。平台名字相似不保证指数、现金调整与换月完全一致。没有取得同一时点 RFQ 和 swaps 多空融资数值，故**不填写净基差、不宣称已成立**。进入门槛由券商权限部分转化为平台地域资格、RFQ 容量、对手方、隔离保证金与关市风险。

### 纯链上 GOLD / PAXG funding 差：当前数字看起来诱人，历史很薄

**Fact-A + Calc**：同轮官方 API 返回 PAXG perp funding 为每小时 **0.00125%**，XYZ GOLD 为 **0.000625%**。买 GOLD、卖 PAXG 的瞬时差为 **0.000625%/小时**，按不变费率简单年化为 **5.475%**，不是收益预测。来源：[`hl_main.json`](evidence/hl_main.json)、[`hl_xyz.json`](evidence/hl_xyz.json)。

但同一周 PAXG 累计费率 **0.132867%**，GOLD **0.127454%**，双 perp 净收入只有 **0.005413%**（Calc，按等名义本金近似，未计基差与再平衡）。来源：[`funding_PAXG.json`](evidence/funding_PAXG.json)、[`funding_GOLD.json`](evidence/funding_GOLD.json)。

**Inference**：这种组合散户技术上更容易触达，然而普通基础 taker 往返总费用约 **0.27%**（GOLD 两次 0.09% 加 PAXG 两次 0.045%，Calc），远大于本周 funding 差。PAXG 还含代币相对黄金的溢价/发行方风险。**本次排除为立即收息机会**，仅保留低费用账户的异常时段监控。

## 三、股票现货折溢价：更新与过滤结果

### “Kraken $341、Raydium $360”的长期溢价没有复现

**Fact-B + Calc**：本次打开的同一 [CoinGecko TSLAx 市场页](https://www.coingecko.com/en/coins/tesla-xstock) 显示 Kraken TSLAX/USD **$355.07**、Raydium CLMM TSLAX/USDC **$354.84**，DEX 相对 CEX 为 **-0.0648%**。该页将两者标为 Recently，但没有精确同秒时间，因此只能叫聚合报价差；同时页面给 Raydium spread 为 **0.60%**，Kraken 为 **0.01%**。聚合器 spread 也不是已获取的交易 RFQ。

**Inference**：该样本不足以证明所有时段都无套利，却足以否定继续把旧截图当当前稳定溢价。方向上如果 DEX 确实便宜，应买 DEX、卖 CEX 库存；不能反向执行旧策略。必须另计 CEX 交易/提现费、链上 swap 费、稳定币差和预置库存资金成本。

### 同一 TSLAx token 的池间价差：有显示偏离，尚无可成交证明

**Fact-B**：DexScreener API 在本轮显示 TSLAx 主 Raydium 池 **$355.19**、另一较小池 **$359.029**，价格差 **+1.0808%（Calc）**；两池显示流动性分别 **$2,062,862.34**、**$22,973.29**。两者 base mint 都是 `XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB`，并与 CoinGecko 链接的 TSLAx Solana 资产一致。来源：[主池](https://dexscreener.com/solana/8adabqktrs6hvmjyc6ezebgdiaxhlygridwkwwp1npff)、[小池](https://dexscreener.com/solana/abzmjavzxhksspzbcw8dry2jrjw2qqueztm6ntjvr4qp)、[`dex_TSLAx.json`](evidence/dex_TSLAx.json)。

**Inference / 可探测候选**：同链买低卖高，若路由支持可把闭环纳入同笔交易；不需要发行方赎回，也不需要借股票。阻碍是小池有效 tick 深度、quote token 风险、swap 费、竞争机器人和 MEV。本次只有聚合显示价，没有路由模拟或可成交 min-out，故此 **1.0808% 不列为已锁定收益**，也不能用池总 TVL 推定能成交的量。

### 跨发行方同股票：本次没有合格的“最大 NAV 折溢价”排名

**Fact-B**：已搜索 TSLAon、NVDAon、MUon、CRCLon 及 xStocks；DexScreener 同 ticker 返回多个互不相同的合约，部分宣称巨量流动性。没有完成发行方资产目录、份额倍率和有效报价的联合验证，因此这些未核验地址及其夸张价差全部剔除。搜索原始结果留在 evidence 中，**不代表认可其真实性**。

**Fact-A**：Ondo stocks 是包含税后分红再投资的 total-return tracker；xStocks 则通过 rebasing / Solana Scaled UI 处理公司行动。直接比较一个 Ondo token 与一股股票，或比较 xStocks 原始最小单位和 UI 单位，可能产生虚假溢价。[Ondo 产品说明](https://ondo.finance/ondo-stocks)、[xStocks FAQ](https://docs.xstocks.fi/docs/frequently-asked-questions)

**Inference**：比较公式应是 `token 可成交价格 / (底层同时可成交价格 × 每 token 股票经济份额)`，再计稳定币兑换；各发行方也不能相互直接赎回。先建立相同经济敞口再谈多空均值回归，否则只是两个不同债权的相对价值交易。用户给出的 TSLAx、NVDAON、CRCLON、COINX、MUON 旧折溢价没有附采样时点，本文不把它们收入当前清单。COIN、MU 也不能仅因偏离大就称为“小盘股”；池深与底层公司规模不是一回事。

### 赎回摩擦已变：不能沿用旧规则

| 平台 | Fact：本次查到的规则 | 对套利的含义（Inference） |
|---|---|---|
| xStocks | 最新 FAQ 明确散户可直接赎回，须 KYC、最低 **$5,000**。Market Flow 当前费用设为 **0**；不超过 **$200,000** 平均结算 **30 秒**，超过则 **2 分钟**，可用 **24/5** | 旧“三工作日、最低 $100、0.5%、仅专业投资者”不能泛化至新产品流程。平均时长不是 SLA，直接准入、白名单、底层成交成本与营业时段仍在 |
| Ondo Stocks / GM | 当前直接 mint/redeem 支持 **24/5**，其中 NVDAon、SPYon、CRCLon、TSLAon、QQQon、GOOGLon 公布 **24/7** 通道，仍可因市场/公司行动暂停。报价可含发行方保留的差额与费用，另付 gas；地域和投资者资格限制继续适用 | “仅机构能赎回”过于绝对，但也不能推出所有散户都能直连。以账户资格与 live RFQ 为准；不是零摩擦按屏幕股价兑付 |
| Dinari | 官方 Partner Fees 给合作方 OTC 最低 **$25,000**，报价通常至 **10 bps**、薄流动性可能更宽；USDT 转换加 **3 bps** | 不能一概说费太高，也不能把合作方 OTC 成本当散户成本。无匹配的当前 dShare 二级报价，本次不认定有套利 |

来源：[xStocks FAQ](https://docs.xstocks.fi/docs/frequently-asked-questions)、[Market Flow](https://docs.xstocks.fi/docs/issuance-and-redemption/market-flow)、[Ondo Stocks](https://ondo.finance/ondo-stocks)、[Ondo 资格](https://docs.ondo.finance/ondo-stocks/eligibility)、[Ondo 费用](https://docs.ondo.finance/ondo-stocks/fees-and-taxes)、[Dinari Partner Fees](https://docs.dinari.com/docs/fees)。Ondo 资格页对香港、新加坡等地明确设有限制，不能按语言或钱包所在地推断用户合资格。

## 四、Flash Trade、Ostium：不要把不同费用机制混为 funding

**Fact-A**：Ostium 最新费用页给开仓 **3–5 bps**，oracle 费 **$0.10 USDC**，没有 closing fee；持仓采用基于真实 carry 的双向 rollover：`long = underlyingCarry + brokerPremium`、`short = -underlyingCarry + brokerPremium`。文档给 broker premium 通常年化 **1–2%**；某些方向可以收取，而不是双方永远只能付费。[Ostium Fees](https://docs.ostium.com/traders/reference/fees)

**Inference**：Ostium 可作为替代对冲腿，但 rollover 收入不能直接等同于 crypto perp 的多空 funding 转移。未取得当前品种 Net Rate L/S 和 bid/ask，因此没有可报告的当期净基差；旧“统一 4bps、200x”不宜作为成本模型。

**Fact-A**：Flash 官方术语说明有 entry/exit fee 和周期性 borrow fee，交易对手为池子。[Flash Glossary](https://docs.flash.trade/flash-trade/flash-trade-protocol/build-on-flash/glossary)

**Fact-B**：2026 年可检索的开发者实测指出 rTSLA/rNVDA 等 perp 跟的是底层股票 oracle，不是 DEX 上股票 token 报价；这是第三方实测，未在本次逐市场读取 oracle 配置。[开发者原始记录](https://colosseum.com/agent-hackathon/forum/1854)

**Inference**：如果 token 有溢价，在 Flash 做空股票 oracle 并不能自动“做空该溢价”；只有持有相应便宜 token、对冲底层并等到真正退出/赎回，才可能收敛。Flash 本次没有取得最新具体市场借款费与买卖报价，明确列为**证据不足**，不编造 funding 数值。

## 五、国债 / 债券 token：有收益差，没有证明可锁定套利

**Fact-B**：本次直接抓取 [DeFiLlama Yields API](https://yields.llama.fi/pools)；原始响应 [`llama_yields.txt`](evidence/llama_yields.txt)。这些是聚合器 APY 字段，不是保证到期收益率，API 未在每行提供独立的精确更新时间。

| 产品 / 链或份额 | APY | 可复查 pool ID |
|---|---:|---|
| USDY / Ethereum | 3.56% | ac61ee82-2fe4-4f9b-a9cd-7fb33f598859 |
| OUSG / Ethereum | 3.25% | 7436db9b-2872-46c8-81a2-da6baff902b7 |
| BUIDL / Ethereum Institutional | 3.59429% | b663ca59-c7e6-4435-ae4a-28d339ce6a15 |
| BUIDL / Ethereum 另一条目 | 3.25307% | b2b1d98f-cac1-4e7b-8cd8-d67b576fd259 |
| USYC / Ethereum | 2.97695% | 448a64ff-06fd-4e56-b63c-03662ac39010 |
| Flux Finance USDC 供应 | 3.73098% | fa4d7ee4-0001-4133-9e8d-cf7d5d194a91 |
| Flux Finance USDT 供应 | 4.46754% | 6600934f-6323-447d-8a7d-67fbede8529d |

**Calc**：USDY 对 OUSG 的显示差为 **31 bps/年**；BUIDL Institutional 对 OUSG 为 **34.429 bps/年**；Flux USDC 供应相对 OUSG 为 **48.098 bps/年**。这些差值均由表中 APY 相减计算。

**Inference**：USDY 与 OUSG 是不同资格、法律结构与流动性条款；BUIDL 不同条目可能对应不同份额、费率或数据口径，不能用桥接自动赚差。Flux 的供应利率不是可借入利率，把它当融资腿会直接做错模型。本次没有取得匹配期限、可借规模和可锁定的低成本负债，因此结论是**现金管理/信用与流动性风险补偿，不是无风险收益差套利**。短期持有的小幅年利差还会被一次兑换或赎回成本吞掉。

**Fact-A：门槛与退出**：OUSG 只允许通过 Qualified-Access onboarding 的人投资、收取、转让和赎回；即时赎回最低 **$5,000**，非即时 **$50,000**。官方举例非即时赎回通常到下一个工作日结束，且请求日收益可能不归赎回者。[资格](https://docs.ondo.finance/qualified-access-products/ousg/eligibility-and-onboarding)、[赎回](https://docs.ondo.finance/qualified-access-products/ousg/redeeming)

BUIDL 官方发行及后续跨链公告披露初始最低 **$5 million**、合格投资者渠道；这属于已发布条款，并非本次取得的最新版认购合同。[Securitize 公告](https://investors.securitize.io/news/news-details/2025/Securitize-Announces-the-Live-Deployment-of-Wormhole-Enabling-Tokenized-Funds-with-Multiple-Share-Classes-01-28-2025/default.aspx)

USDY 仍有地域/投资者限制；本次未取得可执行的赎回费与兑付时长，所以不将 DEX 价格相对某个未核实 NAV 的差值列为机会。[USDY 资格](https://docs.ondo.finance/general-access-products/usdy/eligibility)

OUSG 费用页仍写管理费 **0.15%**“豁免至 2026-07-01”；研究日已经在这之后，不能继续按豁免费计算，也不能在不知道 APY 是否净费时再机械扣一次。[费用页](https://docs.ondo.finance/qualified-access-products/ousg/fees-and-taxes)

## 六、CBB 最新方向与聪明钱解读

### 最新可定位动态

**Fact-B / 本人自述经新闻转引**：2026-09-02 的 [CBB 原帖](https://x.com/Cbb0fe/status/2095250548744405297) 被 2026-09-03 新闻引用。报道说其双人团队过去 **10 个月**在 HIP-3 / TradeXYZ 与 IBKR 做 delta-neutral 套利，交易量约 **$32 billion**、利润超过 **$10 million**，投入资本年化约 **35–45%**。这些是本人陈述，不是审计收益；也不能拿来代表现在新进入者收益。[AiCoin / PANews 转引](https://www.aicoin.com/en/news-flash/3061624)

该披露描述：在链上围绕券商价格挂单，链上成交后才到 IBKR 反向对冲，并对各品种设置最小对冲量、滑点与异常价格停机条件。报道同时提到 IBKR 行情延迟曾导致异常黄金空头与亏损，以及机构进入后利润空间收窄。[JinaCoin 对原帖的报道](https://jinacoin.ne.jp/hyperliquid-cbb-20260903/)

**Fact-B / X 镜像**：同一时期开始交易 Variational US100、XAU swaps。Followin 索引标为 **09-02**，Sotwe/TwStalker 则显示相对天数，且镜像缓存不一致；不据此编造精确发帖分钟。[Sotwe](https://www.sotwe.com/Cbb0fe)、[Followin](https://followin.io/en/kol/4076023946)

**访问限制**：X 原帖页面直接打开失败；从 JinaCoin 的嵌入链接确认 status URL，用镜像与新闻交叉读取。TwStalker 搜索摘要与打开页面还出现缓存错位，所以以上是“本次最新可核实的交易相关披露”，不是宣称完整读遍其最新时间线。未取得完整可信地址绑定，**没有把 `0xEFd3...` 补全为任何钱包，也没有声称核验其当前全部仓位**。

### 老的半导体空单新闻应如何放置

**Fact-B / 历史**：2026-02-04 的消息确实记载其在 Hyperliquid 做空白银及 INTC、SNDK、MU。[KuCoin 历史快讯](https://www.kucoin.com/news/flash/crypto-kol-cbb-holds-over-40m-in-commodity-short-positions-focuses-on-silver-and-semiconductor-storage-sectors)

**Inference**：这不是九月仍持有相同空单的证据；结合九月的对冲披露，更不能把所有可见空仓归类为方向性看空。用户提供的单日利润数字未取得可核实的一手账单，本报告不沿用。

### 类似聪明钱

**Fact-B / 历史背景**：三月报道还记录 Rune 做多原油并以 ETH/Nasdaq 空单对冲，Loracle 做空 CL，并有 NVDA/PAXG 空头；同一报道记录 CBB 参与油、气、黄金和股票。[ChainCatcher](https://www.chaincatcher.com/en/article/2250652)

**Fact-B / 最近叙述**：九月初 CBB 披露的新闻提到 Wintermute、Ethena 等参与者进入后压缩该策略利润；这是新闻对竞争格局的叙述，不能推断它们现在某个品种的具体头寸。[AiCoin](https://www.aicoin.com/en/news-flash/3061624)

**Inference**：有价值的“聪明钱信号”应是跨腿成交、库存周转、资金费现金流和报价位置，而非截取最大空单。此次没有拿到 Rune/Loracle 九月的完整两腿账本，故不包装成最新跟单信号。

## 七、可行性排序：散户与专业团队分开看

| 使用者 | 优先顺序（Inference） | 当前证据与决定性条件 |
|---|---|---|
| 有机器人能力的纯链上散户 | TSLAx 同 token 池间闭环探测 → Variational/XYZ RFQ 比较 → 黄金双 perp 监控 | 第一项有显示价差但缺模拟；第二项缺实时双边 RFQ；第三项历史差不足抵费。**本次没有确认随手可做的正净收益交易** |
| 有券商期货权限、可维护自动化的个人/小团队 | Brent 与 CL → NATGAS → 股指与黄金事件窗口 | 油气有一周实际 funding 与当前盘口；先获取匹配合约外部 bid/ask、展期现金流和真实账户佣金，才能升为可执行 |
| 已通过发行方 KYC 的用户 | xStocks 折价买入—直接赎回 | 门槛已降低，散户并非全部被排除；但本次缺当前 verified NAV/RFQ 折价，因此属于可实施通道而非现成盈利 |
| 做市商 / 专业机构 | HIP-3 与券商高速对冲、发行方 mint/redeem 库存管理 | 优势来自融资、双边库存、交易费和基础设施；竞争已压缩回报 |
| 合格投资者 / 大额机构 | BUIDL、OUSG 资金管理及抵押融资 | 基金资格与额度限制明显；没有锁定廉价融资腿，就只有 carry，不能称套利 |

## 八、把候选升级为订单所缺的证据

对于油气，只需围绕具体候选补齐以下执行链，不必继续泛搜“RWA 机会”：

1. 读取当前 oracle 合约月份/权重，在券商订阅实时行情，取得同一时点的双边可成交价与佣金；延迟报价不能用于触发。
2. 按计划交易数量计算 HL 与外部市场双边 VWAP；用开仓、退出、展期、融资和资金费现金流计算净边际。
3. 对冲数量按经济单位和期货合约乘数换算，明确无法整除的小额残余 delta；分配两个账户的独立保证金。
4. 对 funding 反转、行情时间戳失效、外部休市、换月及对冲失败设暂停条件；delta-neutral 并不防止单腿先被清算。
5. 同 token DEX 路径则先验证 mint、quote token、余额单位，再做完整 round-trip 模拟；只能用成交输出确认利润，不能用 DexScreener 显示价确认。

净收入核算应为：**实际两腿基差变动 + 实收 funding / rollover + 股息或现金调整 − 交易与滑点成本 − 借券/融资 − 换月及再平衡 − 资金跨系统成本**。账本逐项只记一次。

## 九、数据排除与复核说明

- `flx:TSLA`、`flx:NVDA` 等虽然 API 仍返回旧价格，但 metadata 标记 `isDelisted: true`；已剔除，绝不拿它们与 xyz 的实时价格拼套利。见 [`hl_flx.json`](evidence/hl_flx.json)。
- CoinGecko API 请求未取得可解析 JSON；Kraken 标准 ticker 请求返回 unknown asset pair，AssetPairs 本次未列出目标股票对。这是本次接口覆盖限制，不表示 Kraken 未上市 xStocks；CEX 比较降级为 CoinGecko 网页数据。
- DexScreener symbol 搜索仅用于发现池子；同名假币、未核实合约、陈旧交易价格、极端显示 TVL 均不能自动通过筛选。
- 未获得真实股票与全部代币的同步可成交价及份额倍率，因此没有虚构全市场“最大 NAV 偏离排行榜”。已展示的股票候选仅为同 token 的池间显示价差。
- 除上述官方 API 原始响应，目录中保存了部分网页/文档抓取；抓取文件可能含错误页，不能用“存在文件”代替验证。主要数字的可复查来源已在对应段落给出。

**最终判断（Inference）**：仍存在值得验证的跨传统期货—链上油气基差与 funding 通道；最接近 CBB 当前风格的是对冲做市、成本控制与行情工程。散户能访问交易界面，不等于拥有同样的套利闭环。本次找到了具体可观察候选，也明确剔除了不能兑现的“大数字”。
