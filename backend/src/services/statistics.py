import math,hashlib,json
from datetime import datetime,timezone
from scipy import stats

def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()

def _checksum(payload):
    # 规范化编码：相同样本与方案必然得到相同校验码，供导出后独立复算核对
    canonical=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(canonical.encode()).hexdigest()

def plan_snapshot_of(plan):
    return {'plan_id':plan.id,'title':plan.title,'campus':plan.campus,
            'exclude_absent':plan.exclude_absent,'description':plan.description,
            'captured_at':utcnow_iso()}

def build_snapshot(students,plan):
    """报告受理时点固化方案与全量样本：纳入/排除及原因都留痕，之后修订不再影响本报告。"""
    plan_snap=plan_snapshot_of(plan)
    selected=[s for s in students if plan.campus=='全部' or s.campus==plan.campus]
    included,excluded=[],[]
    for s in selected:
        row={'student_id':s.id,'code':s.code,'name':s.name,'campus':s.campus,
             'group':s.group,'score':s.score,'absent':s.absent}
        if s.score is None:
            excluded.append({**row,'included':False,'exclusion_reason':'成绩为空，永远排除'})
        elif plan.exclude_absent and s.absent:
            excluded.append({**row,'included':False,'exclusion_reason':'缺考，按方案规则排除'})
        else:
            included.append({**row,'included':True,'exclusion_reason':None})
    sample_snap=included+excluded
    checksum=_checksum({'plan':plan_snap,'samples':sample_snap})
    return plan_snap,sample_snap,checksum

def compute_from_snapshot(plan_snap,sample_snap,snapshot_checksum):
    """只依据受理时点固化的快照重算两组差异，不读取可被修订的成绩/方案表。"""
    kept=[r for r in sample_snap if r['included']]
    excluded=[r for r in sample_snap if not r['included']]
    control=[r['score'] for r in kept if r['group']=='control']
    treatment=[r['score'] for r in kept if r['group']=='intervention']
    n0,n1=len(control),len(treatment)
    if min(n0,n1)<2: raise ValueError('每组至少需要 2 个有效成绩；请调整教学点或补录样本')
    m0,m1=sum(control)/n0,sum(treatment)/n1
    v0=sum((x-m0)**2 for x in control)/(n0-1)
    v1=sum((x-m1)**2 for x in treatment)/(n1-1)
    se2=v0/n0+v1/n1
    if se2<=0: raise ValueError('两组方差均为零，无法进行 Welch 检验')
    df=se2**2/((v0/n0)**2/(n0-1)+(v1/n1)**2/(n1-1))
    se=math.sqrt(se2);delta=m1-m0;t=delta/se
    p=float(2*stats.t.sf(abs(t),df))
    margin=float(stats.t.ppf(.975,df))*se
    pooled=math.sqrt(((n0-1)*v0+(n1-1)*v1)/(n0+n1-2))
    return {'control_n':n0,'intervention_n':n1,
            'excluded':len(excluded),'total_selected':len(sample_snap),
            'control_mean':m0,'intervention_mean':m1,'difference':delta,
            't':t,'df':df,'p_value':p,'ci_low':delta-margin,'ci_high':delta+margin,
            'cohen_d':delta/pooled if pooled else None,'significant':p<.05,
            'checksum':_checksum({'plan':plan_snap,'included':kept}),
            'snapshot_checksum':snapshot_checksum,
            'plan_snapshot':plan_snap,
            'sample_snapshot':kept,
            'excluded_snapshot':excluded,
            'method':'Welch 独立样本 t 检验；双侧 α=0.05；干预组减对照组；95% CI；按受理时点样本快照复算'}

def welch(students,plan):
    """兼容 seed 的便捷入口：即时构建快照并复算。"""
    plan_snap,sample_snap,checksum=build_snapshot(students,plan)
    return compute_from_snapshot(plan_snap,sample_snap,checksum)
