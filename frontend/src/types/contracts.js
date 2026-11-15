/** @typedef {{id:number,code:string,name:string,campus:string,group:'control'|'intervention',score:number|null,absent:boolean}} Student */
/** @typedef {{id:number,title:string,campus:string,exclude_absent:boolean,description:string}} Plan */
/** @typedef {'queued'|'running'|'completed'|'failed'} ReportStatus */
/**
 * @typedef {object} Report
 * @property {number} id
 * @property {number} plan_id
 * @property {ReportStatus} status
 * @property {object|null} result
 * @property {string} error
 * @property {string} conclusion
 * @property {boolean} published
 * @property {object|null} plan_snapshot 受理时点固化的方案
 * @property {Array|null} sample_snapshot 受理时点固化的全量样本（含纳入/排除）
 * @property {string} snapshot_checksum 快照校验码
 * @property {string|null} run_token 当前处理者围栏令牌
 * @property {string|null} claimed_by 当前处理者
 * @property {string|null} claimed_at 领取时间
 * @property {number} recovery_count 中断恢复次数
 * @property {number} attempts 领取尝试次数
 */
export const contractVersion=2;
