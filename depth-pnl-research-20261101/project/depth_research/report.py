import html
import json


def chart(curves, field, title):
    all_values = [r[field] for _, rows in curves for r in rows]
    if not all_values:
        return ''
    lo, hi = min(0, min(all_values)), max(0, max(all_values))
    if hi == lo:
        hi = lo + 1
    t0 = min(rows[0]['mono_ns'] for _, rows in curves if rows)
    t1 = max(rows[-1]['mono_ns'] for _, rows in curves if rows)
    out = [f'<h3>{title}</h3><svg viewBox="0 0 1000 300" role="img" aria-label="{title}"><rect x="0" y="0" width="1000" height="300" fill="#f4f7fb"/>']
    for i in range(5):
        v = lo + (hi-lo)*i/4; y = 260-220*i/4
        out.append(f'<path d="M80,{y}H970" stroke="#dce3ed"/><text x="4" y="{y+4}" font-size="12">{v:.4f}</text>')
    colors = ['#176bba', '#bd4930']
    for idx, (label, rows) in enumerate(curves):
        # Bucket extrema preserve narrow drawdown spikes in the static chart.
        stride = max(1, len(rows)//1100); reduced = []
        for j in range(0, len(rows), stride):
            block = rows[j:j+stride]
            reduced.extend(sorted({r['mono_ns']: r for r in [block[0], min(block,key=lambda r:r[field]), max(block,key=lambda r:r[field]), block[-1]]}.values(),key=lambda r:r['mono_ns']))
        points = ' '.join(f'{80+890*(r["mono_ns"]-t0)/max(1,t1-t0):.2f},{260-220*(r[field]-lo)/(hi-lo):.2f}' for r in reduced)
        out.append(f'<polyline points="{points}" fill="none" stroke="{colors[idx]}" stroke-width="1.5"/><text x="{100+idx*330}" y="20" fill="{colors[idx]}">{html.escape(label)}</text>')
    out.append('<text x="80" y="290">测试开始</text><text x="840" y="290">测试结束（时间）</text></svg>')
    return ''.join(out)


def table(rows, keys):
    def fmt(v):
        if v is None:
            return '不适用'
        if isinstance(v,float):
            return f'{v:.6f}'
        return html.escape(str(v))
    return '<table><tr>'+''.join(f'<th>{html.escape(k)}</th>' for k in keys)+'</tr>'+''.join('<tr>'+''.join(f'<td>{fmt(r.get(k))}</td>' for k in keys)+'</tr>' for r in rows)+'</table>'


def render(summary, baseline, benchmark, sensitivity, audit):
    test = baseline['summary']
    content = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>一小时深度策略离线研究</title>
<style>body{{font:16px/1.65 system-ui,sans-serif;max-width:1200px;margin:35px auto;padding:0 24px;color:#203047}}table{{border-collapse:collapse;display:block;overflow:auto;font-size:13px}}td,th{{padding:7px 10px;border:1px solid #dbe1e8;text-align:right;white-space:nowrap}}th{{background:#edf3fa}}svg{{width:100%}}pre{{white-space:pre-wrap;background:#f3f6fa;padding:16px}}.lead{{background:#eaf3ff;padding:20px}}a{{color:#176bba}}</style>
<h1>一小时深度策略离线研究</h1>
<p class="lead">测试集（后20分钟），零手续费、100ms 延迟：净盈亏 <b>{test['net_pnl']:+.6f} USDT</b>，交易 {test['trades']} 笔，按中间价计价最大回撤 {test['mtm_max_drawdown']:.6f} USDT。<b>这只是已被探索过的一小时历史样本，不能证明长期有效，不年化。</b></p>
<h2>数据与窗口</h2><pre>{html.escape(json.dumps(summary['window'],ensure_ascii=False,indent=2))}</pre>
<p>BTCUSDT 现货，只做多，每次 {summary['config']['quantity_btc']} BTC，无杠杆。金额为 USDT 计价，不假定 USDT 等于美元。没有指定本金，收益率为 null；累计收益在本报告中指累计净盈亏金额。</p>
<p>snapshot 在 available 时刻才可用；增量按本地单调接收时刻处理，交易所 E 时间仅审计。raw仅提供接收时间而无逐消息解析完成时间，因此接收时刻是本模型的可用时间代理，未测得的处理开销由延迟假设近似。快照前消息缓冲；U ≤ lastUpdateId+1 ≤ u 才能衔接，u ≤ lastUpdateId 为过期。缺序、会话变更、接收断档超过阈值、交叉盘口或已知范围不足立即停止，只有新的快照和连续增量才能恢复。不会用未来快照补过去。断档期间持仓无法证明退出时，整次结果标记不完整，绝不抹掉持仓。</p>
<pre>{html.escape(json.dumps(audit['reconstruction'],ensure_ascii=False,indent=2))}</pre><p>独立秒级记录交叉核验（只用于审计，不用于成交）：{html.escape(json.dumps(audit['aligned_crosscheck_not_execution_source'],ensure_ascii=False))}。输入文件逐项覆盖范围、JSON错误数、接收排序、时钟偏移与SHA256见 audit.json。</p>
<h2>策略、成交与边界</h2>
<p>参考价为当时最优买卖中间价 P；六个 $1 档定义为买侧 (P−j−1, P−j]、卖侧 [P+j, P+j+1)，j=0…5。精确使用两倍价格整数判定边界；B/A 是这六档的 BTC 数量比，不是订单数或美元名义额。bid1 是最优买档 BTC 量。满足 bid1 下限、B/A 入场阈值持续500ms后发出买入信号；盘口转弱持续300ms或持仓达到30秒后发出卖出信号。30秒限制独立于盘口转弱，实际成交持仓可多出采样间隔和执行延迟。</p>
<p>信号后至少等待指定延迟，使用之后首个可用更新的盘口逐档 taker 成交；即使0ms也不使用产生信号的同一事件。完整数量无法成交则全部拒绝，卖出失败保持持仓并重试。订单等待期间不重复开仓。价格已经包含 spread 和吃单深度 impact，绝不重复扣除。历史盘口不受本策略影响，这是小规模、可见流动性成交假设，无法证明真实订单一定成交，没有 maker 排队模型。没有交易所历史过滤器文件，0.01 BTC 仅为研究订单量，不声称已验证真实账户下单规则。</p>
<p>每段在预定结束前2秒发出清仓信号；最后32秒左右禁止新开仓，以预留持仓期限与成交延迟。任何执行均限定在该段半开区间 [start,end)；若未能清仓则保留未平仓标志，不跨训练/测试边界续仓。本研究不构造未来价格标签，也不在边界外取标签。训练网格32组，只按前40分钟0费/100ms净盈亏选择一次；敏感性分析固定这组参数，不按测试结果重选。测试已经被旧研究看过，属于探索性时序留出，不能称为全新未见样本。</p>
<h2>参数与基准</h2><pre>{html.escape(json.dumps(summary['selected_params'],ensure_ascii=False,indent=2))}</pre>
'''
    comparisons = []
    for name, s in [('训练（用于选择）', summary['train']), ('测试', test), ('全小时分段拼接（含训练）', summary['descriptive_stitched_hour']), ('测试买入持有', benchmark['summary']), ('不交易', summary['no_trade'])]:
        comparisons.append({'name':name, **s})
    content += table(comparisons, ['name','trades','gross_pnl','fees','net_pnl','win_rate','payoff_ratio','profit_factor','mtm_max_drawdown','mean_hold_seconds','break_even_fee_bps_per_side'])
    content += '<p>买入持有采用相同0.01 BTC、相同测试窗口、信号后延迟逐档买入和预定期末逐档卖出；相同的是单次仓位，不是累计交易周转。盈亏比为平均盈利/平均亏损绝对值，profit factor为盈利总和/亏损绝对值；无分母时为不适用。盈亏平衡费率=毛盈亏/双边成交名义额×10000，负值意味着零费已亏损。</p>'
    content += chart([('策略：净市值盈亏',baseline['equity']),('同规模买入持有',benchmark['equity'])], 'equity', '测试集累计净市值盈亏（USDT）')
    content += chart([('策略回撤',baseline['equity']),('买入持有回撤',benchmark['equity'])], 'drawdown','测试集按市值计价回撤（USDT）')
    content += '<p>每个有效盘口时点按中间价标记持仓，扣除已发生费用，平仓后为累计净实现盈亏；曲线从0起算峰值，回撤以正金额报告。不预扣尚未发生的平仓费。完整时点数据见 equity CSV。</p><h2>测试集费用×延迟敏感性</h2><p>每边0/1/2/5/10 bps是研究假设，不是当前账户费率；费用按买入、卖出各自成交名义额独立收取。敏感性表同时列出固定参数下的训练与全小时分段拼接金额；含训练部分的全小时数值只能描述样本，不能当作测试收益。其他成本（融资、税、借币、提币）未建模，无做空、杠杆或跨所转移。</p>'
    content += table(sensitivity,['latency_ms','fee_bps_per_side','trades','gross_pnl','fees','net_pnl','win_rate','payoff_ratio','mtm_max_drawdown','break_even_fee_bps_per_side','buy_hold_net_pnl','train_net_pnl','stitched_hour_net_pnl'])
    content += '<h2>零费测试逐笔</h2>'+table(baseline['trades'],['direction','quantity_btc','entry_utc','exit_utc','entry_price','exit_price','exit_reason','gross_pnl','entry_fee','exit_fee','net_pnl'])
    content += '''<h2>旧实现审查及研究限制</h2><ul>
<li>旧实现的 max_hold 在弱信号分支内，可能超过30秒仍持仓；新实现独立触发。</li>
<li>旧实现预先查询未来卖出价并立即清空仓位，可能在卖出实际发生前重新入场；新实现待成交订单按事件推进。</li>
<li>旧实现期末通过最后事件加延迟寻价通常失败，未平仓收益可能遗漏；新实现提前预定清仓，失败显式标记。</li>
<li>旧实现遇缺序仅跳过，未显式作废盘口；新实现阻断直到重新同步。旧实现以动态最远档判断已知范围、仅留80档；新实现限制在原始快照已证明范围并保留满足本次数量所需全部档位。</li>
<li>旧实现只按已平仓盈亏计算回撤；新实现每个有效事件标记持仓。旧预测标签可取小时结束前最后一价代替超窗未来价；本项目不使用此类标签。</li>
<li>旧报告数值与新结果不可直接归因于单一修复；窗口、执行、清仓和持仓规则均已改变。旧文件只读取和哈希，不执行旧脚本、不写回。</li>
<li>本数据仅一小时、单一市场状态，网格选择有过拟合和多重比较风险。阈值延续旧研究，已存在研究者选择偏差；需要未来独立多日数据验证，不能年化。</li>
<li>Binance现货BTCUSDT与另一交易所BTC USDC永续并非同一资产合约；RFQ有429、时间稀疏，mark不可成交。无跨所同时成交、合约乘数、费用、资金费及转移证据，本项目不做跨所套利。</li></ul>
<h2>文件与复现</h2><p><a href="summary.json">汇总JSON</a> · <a href="audit.json">审计与SHA256</a> · <a href="sensitivity.csv">敏感性CSV</a> · <a href="test_trades.csv">基准逐笔CSV</a> · <a href="test_equity.csv">市值曲线CSV</a> · <a href="training_grid.csv">训练网格</a> · <a href="book_features.csv">盘口分档信号</a></p>'''
    content += '<pre>'+html.escape(summary['reproduce_command'])+'</pre></html>'
    return content
