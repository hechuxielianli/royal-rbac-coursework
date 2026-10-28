"""Plot only the recorded stage summaries and recovery buckets."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'evidence/stress-summary.json').read_text(encoding='utf-8'))
plt.rcParams.update({'font.family':'Microsoft YaHei','font.size':10,'axes.spines.top':False,
                     'axes.spines.right':False,'axes.unicode_minus':False,'savefig.facecolor':'white'})
fig,axes=plt.subplots(1,3,figsize=(14,4.4),gridspec_kw={'width_ratios':[1.2,1.2,1]})
stages=data['stages']; x=range(len(stages)); labels=['基线\n1','4','16','64','160','320','恢复\n1']
axes[0].plot(x,[s['p95_ms'] for s in stages],'-o',color='#20677b',lw=2)
axes[0].axhline(500,color='#b04b35',linestyle='--',label='响应目标 500 ms')
axes[0].set(yscale='log',ylabel='P95 / ms（对数刻度）',xlabel='并发客户端数 / VU',title='A  并发升高后的等待')
axes[0].set_xticks(list(x),labels);axes[0].legend(frameon=False,fontsize=9)
axes[1].bar(x,[s['rps'] for s in stages],color=['#20677b']*6+['#639b80'],width=.65)
axes[1].set(ylabel='完成请求 / s',xlabel='并发客户端数 / VU',title='B  阶段吞吐（含在途收尾）')
axes[1].set_xticks(list(x),labels)
for i,s in enumerate(stages):axes[1].text(i,s['rps']+1.5,f"{s['rps']:.1f}",ha='center',fontsize=8)
axes[1].set_ylim(0,max(s['rps'] for s in stages)*1.16)
b=data['recovery_buckets']
axes[2].plot([i['second']+1 for i in b],[i['p95_ms'] for i in b],'-o',ms=3,color='#34816b')
axes[2].axhline(data['recovery_p95_limit_ms'],color='#b04b35',ls='--',label=f"恢复阈值 {data['recovery_p95_limit_ms']:.1f} ms")
axes[2].axvspan(0,3,color='#34816b',alpha=.09)
axes[2].set(xlabel='队列排空后 / s',ylabel='每1秒窗口 P95 / ms',title='C  降压至1 VU后的恢复',ylim=(0,max(65,data['recovery_p95_limit_ms']*1.2,max(i['p95_ms'] for i in b)*1.15)))
axes[2].legend(frameon=False,fontsize=9,loc='upper right')
for ax in axes:ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
fig.suptitle('库滕堡政令厅：实测压力与恢复',fontsize=15,x=.03,ha='left',fontweight='bold')
fig.text(.03,.015,f"Waitress 4线程 · 页面读取/生产写入各50% · 共{sum(s['requests'] for s in stages):,}次HTTP请求 · 错误{sum(s['errors'] for s in stages)} · 2026-10-28",fontsize=9,color='#555555')
fig.tight_layout(rect=(0,.045,1,.91))
fig.savefig(ROOT/'figures/stress-results.png',dpi=180)
fig.savefig(ROOT/'figures/stress-results.svg')
