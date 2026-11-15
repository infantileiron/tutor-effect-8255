import{STATUS}from'../../constants/enums.js';import{esc}from'../../utils/format.js';
export const badge=(status)=>`<span class="badge ${status}">${STATUS[status]||status}</span>`;
export function taskMeta(r){
 if(!r||r.status==='completed')return '';
 const rows=[];
 rows.push(['当前状态',STATUS[r.status]||r.status]);
 if(r.status==='queued')rows.push(['排队说明','等待后台 worker 领取，重复唤醒不会新增报告']);
 if(r.status==='running')rows.push(['当前处理者',esc(r.claimed_by||'—')]);
 rows.push(['已尝试次数',String(r.attempts||0)]);
 rows.push(['中断恢复次数',String(r.recovery_count||0)]);
 if(r.status==='failed'&&r.error)rows.push(['失败原因',esc(r.error)]);
 if(r.status==='running'&&r.claimed_at)rows.push(['领取时间',esc(r.claimed_at.slice(0,19).replace('T',' '))+' UTC']);
 return `<dl class=task-meta>${rows.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;
}
