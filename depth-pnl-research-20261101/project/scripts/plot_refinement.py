"""Optional descriptive R03 plot; requires matplotlib. Does not select a model."""
import csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

rows=list(csv.DictReader(open('experiments/r03/results/aggregate.csv')))
rows=[r for r in rows if r['split']=='test' and r['latency_ms']=='100' and r['fee_bps']=='0.2'
      and int(r['trades'])>=30 and r['id']!='buy_hold']
colors={'reference':'#64748b','hysteresis':'#2563eb','smoothed_depth':'#a855f7',
        'consensus':'#059669','trend_filter':'#d97706','fixed_horizon':'#dc2626'}
fig,ax=plt.subplots(figsize=(9,5.4),layout='constrained')
for group,color in colors.items():
    r=[x for x in rows if x['group']==group]
    ax.scatter([float(x['liquidation_mdd']) for x in r],[float(x['net_pnl']) for x in r],
               color=color,s=45,alpha=.8,label=group.replace('_',' '))
labels={'obi6_r02':('Original OBI',(1.02,45.1)),
        'hysteresis_0.7_30':('Validation-selected OBI',(1.55,35)),
        'consensus_ofi_90':('OFI + OBI:\nexploratory',(.82,32.7)),
        'consensus_qi_90':('QI + OBI: exploratory',(1.95,47.5))}
for r in rows:
    if r['id'] in labels:
        text,xy=labels[r['id']]
        ax.annotate(text,(float(r['liquidation_mdd']),float(r['net_pnl'])),xytext=xy,
                    fontsize=9,arrowprops=dict(arrowstyle='-',alpha=.5))
ax.set(xlabel='Maximum liquidation drawdown (USDT) — lower is better',
       ylabel='Net P&L (USDT) — higher is better',
       title='R03: return vs drawdown on the last 6 hours\n0.01 BTC | 100 ms | 0.2 bps fee per side | previously seen data')
ax.axhline(0,color='#94a3b8',lw=.8);ax.grid(alpha=.18)
ax.legend(fontsize=8,loc='upper right')
fig.savefig('experiments/r03/risk_return.svg')
fig.savefig('/tmp/r03_risk_return.png',dpi=140)
