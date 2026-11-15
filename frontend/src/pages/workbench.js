import{store}from'../stores/business.js';import{esc}from'../utils/format.js';import{resultPanel}from'../components/common/report.js';import{badge}from'../components/common/status.js';import{notice}from'../components/common/notice.js';
function historyMeta(r){
 const parts=[];
 if(r.status==='running')parts.push(`处理中 · ${esc(r.claimed_by||'worker')}`);
 if(r.status==='queued')parts.push('排队等待领取');
 if((r.recovery_count||0)>0)parts.push(`中断恢复 ${r.recovery_count} 次`);
 parts.push(`尝试 ${r.attempts||0} 次`);
 if(r.status==='failed'&&r.error)parts.push(`失败原因：${esc(r.error)}`);
 return parts.join('　·　');
}
export function workbenchPage(){return `<header class=page-head><div><span class=eyebrow>03 / STATISTICAL WORKBENCH</span><h1>检验工作台</h1><p>Welch 检验允许两组方差不同。受理即固化方案与样本快照，中断后自动恢复，不新增报告。</p></div></header><div class=run-bar><form id=run-form><label>研究方案<select name=plan_id>${store.plans.map(p=>`<option value=${p.id}>${esc(p.title)} · ${esc(p.campus)}</option>`).join('')}</select></label><button class=primary ${store.user.role==='viewer'?'disabled':''}>提交后台检验 →</button></form><div><span class=eyebrow>分析约定</span><p>双侧检验 · 95% 置信区间<br>有效样本每组至少 2 条</p></div></div><section id=latest-result class=result-sheet>${resultPanel(store.reports[0])}</section><div class=section-title><h2>计算记录</h2><span>结果按受理快照留存，成绩/方案修订不改变旧报告</span></div><div class=run-history>${store.reports.map(r=>`<button class=history-entry data-report=${r.id}>№ ${r.id}　${esc(store.plans.find(p=>p.id===r.plan_id)?.title||'方案')} ${badge(r.status)}<small>${esc(r.created_at.slice(0,19).replace('T',' '))} UTC</small><div class=history-meta>${historyMeta(r)}</div></button>`).join('')}</div>${notice('失败或中断任务保留同一报告行并自动回收重试；页面可见排队、处理中、恢复次数和失败原因。补录有效成绩或调整方案后，请重新提交一次检验。')}`;}
