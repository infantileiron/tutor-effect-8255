import{num,esc}from'../../utils/format.js';import{badge,taskMeta}from'./status.js';
function snapshotBlock(report){
 const r=report.result;const plan=r.plan_snapshot||report.plan_snapshot||{};
 const excluded=r.excluded_snapshot||[];
 return `<div class=snapshot-block>
 <span class=eyebrow>受理时点快照 · 后续修订不影响本报告</span>
 <p class=snapshot-plan>方案「${esc(plan.title||'')}」 · 教学点 ${esc(plan.campus||'')} · ${plan.exclude_absent?'排除缺考':'缺考有效成绩纳入'} · 捕获于 ${esc((plan.captured_at||'').slice(0,19).replace('T',' '))} UTC</p>
 <p class=snapshot-count>纳入样本 ${r.control_n+r.intervention_n} 条（对照 ${r.control_n} / 干预 ${r.intervention_n}），排除 ${r.excluded} 条${excluded.length?`：${excluded.map(s=>esc(s.code)+'（'+esc(s.exclusion_reason)+'）').join('，')}`:''}</p>
 <p class=checksum>快照校验码<br>${esc(report.snapshot_checksum||r.snapshot_checksum||'')}</p>
 </div>`;
}
export function resultPanel(report){
 if(!report)return '<div class=empty>先在检验工作台执行辅导方案，结果会在这里留存。</div>';
 if(report.status!=='completed')return `<div class=empty>${badge(report.status)} ${esc(report.error||'后台任务执行中，请稍候…')}${taskMeta(report)}</div>`;
 const r=report.result;
 return `<div class=result-heading><div><span class=eyebrow>WELCH · 双侧检验 · 按受理快照复算</span><h2>两组均分相差 <em>${num(r.difference)} 分</em></h2><p>${r.significant?'差异达到预设显著性水平':'尚无足够证据拒绝均值相等假设'}。观察性分组需结合教学点背景解读。</p></div><span class=report-no>研究报告<br>№ ${String(report.id).padStart(3,'0')}</span></div>
 <div class=effect><div><span>常规自习组</span><b>${num(r.control_mean)}</b><small>n = ${r.control_n}</small></div><div class=effect-line><span style="width:${Math.min(100,Math.max(0,r.control_mean))}%"></span><span class=treatment style="width:${Math.min(100,Math.max(0,r.intervention_mean))}%"></span></div><div><span>小组辅导组</span><b>${num(r.intervention_mean)}</b><small>n = ${r.intervention_n}</small></div></div>
 <div class=stats-strip><div><label>p 值</label><strong>${r.p_value<.0001?'< 0.0001':num(r.p_value,4)}</strong></div><div><label>95% 差值区间</label><strong>[${num(r.ci_low)}, ${num(r.ci_high)}]</strong></div><div><label>Cohen d</label><strong>${num(r.cohen_d)}</strong></div><div><label>排除样本</label><strong>${r.excluded} / ${r.total_selected}</strong></div></div>
 <p class=method>t = ${num(r.t,3)} · 自由度 ${num(r.df,2)} · α = 0.05 · 方向：干预 − 对照 · 中断恢复 ${report.recovery_count||0} 次 · 尝试 ${report.attempts||1} 次</p>
 ${snapshotBlock(report)}`;
}
