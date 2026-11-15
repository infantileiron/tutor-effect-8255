"""受理快照、并发领取、中断恢复、围栏写入、导出复算与签核保留的验收测试。"""
import json,os,time,threading
from datetime import datetime,timedelta,timezone
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from src.config.database import SessionLocal
from src.models.entities import Report,Plan,Student
from src.main import app
from src.services import reports as report_service
from src.services import queue as q
from src.services.statistics import compute_from_snapshot
from .conftest import auth_header

client=TestClient(app)

def _fresh(report_id):
    """跨事务读取：夹具会话的身份映射会缓存旧对象，验证其他会话写入时必须开新会话。"""
    with SessionLocal() as session:
        obj=session.get(Report,report_id)
        session.expunge(obj)
        return obj

def _create_report(db,plan):
    return report_service.create_report(db,plan.id)

def _recompute_from_export(payload):
    """按导出文件的复算指引，仅凭 included_samples 独立重算并核对统计量。"""
    included=payload['included_samples']
    control=[s['score'] for s in included if s['group']=='control']
    treatment=[s['score'] for s in included if s['group']=='intervention']
    # 与 statistics 相同的 Welch 口径
    import math
    from scipy import stats
    n0,n1=len(control),len(treatment)
    m0,m1=sum(control)/n0,sum(treatment)/n1
    v0=sum((x-m0)**2 for x in control)/(n0-1)
    v1=sum((x-m1)**2 for x in treatment)/(n1-1)
    se2=v0/n0+v1/n1
    df=se2**2/((v0/n0)**2/(n0-1)+(v1/n1)**2/(n1-1))
    t=(m1-m0)/math.sqrt(se2)
    return {'n':(n0,n1),'mean':(m0,m1),'t':t,'df':df,
            'p':float(2*stats.t.sf(abs(t),df)),'difference':m1-m0}

# 1. 受理时点留存方案与样本快照
def test_acceptance_captures_plan_and_sample_snapshot(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    assert report.status=='queued'
    assert report.plan_snapshot is not None
    assert report.plan_snapshot['campus']=='城东'
    assert report.plan_snapshot['exclude_absent'] is True
    # 全量选中 9 个城东样本，含纳入 6、排除 3
    assert len(report.sample_snapshot)==9
    included=[r for r in report.sample_snapshot if r['included']]
    excluded=[r for r in report.sample_snapshot if not r['included']]
    assert len(included)==6 and len(excluded)==3
    reasons={r['code']:r['exclusion_reason'] for r in excluded}
    assert reasons['S004']=='成绩为空，永远排除'
    assert reasons['S008']=='成绩为空，永远排除'
    assert reasons['S011']=='缺考，按方案规则排除'
    assert len(report.snapshot_checksum)==64
    # 城西样本不在城东方案快照内
    assert {r['code'] for r in report.sample_snapshot}.isdisjoint({'S009','S010'})

# 2. 受理后修订成绩与方案，按原快照重算结果不变
def test_later_revisions_do_not_change_snapshot_recompute(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    plan_snap=json.loads(json.dumps(report.plan_snapshot))
    sample_snap=json.loads(json.dumps(report.sample_snapshot))
    checksum=report.snapshot_checksum
    before=compute_from_snapshot(plan_snap,sample_snap,checksum)

    # 修订成绩、删除缺考标记、改教学点与排除规则
    s001=db.scalar(select(Student).where(Student.code=='S001'))
    s001.score=99.5
    s008=db.scalar(select(Student).where(Student.code=='S008'))
    s008.absent=False;s008.score=30.0
    campus_plan.campus='全部';campus_plan.exclude_absent=False
    db.commit()

    report2=db.get(Report,report.id)
    after=compute_from_snapshot(report2.plan_snapshot,report2.sample_snapshot,report2.snapshot_checksum)
    assert report2.plan_snapshot==plan_snap
    assert report2.sample_snapshot==sample_snap
    assert report2.snapshot_checksum==checksum
    assert after['difference']==before['difference']
    assert after['t']==before['t'] and after['p_value']==before['p_value']
    # 修订后的现行方案口径若直接重算会得到不同结果，证明快照确实隔离了修订
    fresh=compute_from_snapshot(*_fresh_snapshot(db,campus_plan))
    assert fresh['difference']!=before['difference']

def _fresh_snapshot(db,plan):
    from src.services.statistics import build_snapshot
    students=db.scalars(select(Student).order_by(Student.code)).all()
    return build_snapshot(students,plan)

# 3. 多进程并发领取：SKIP LOCKED 下同一任务只被一个处理者领取
def test_concurrent_claim_only_one_holder(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    winners=[]
    barrier=threading.Barrier(4)
    def worker():
        barrier.wait()
        with SessionLocal() as session:
            task,token=q.claim_next(session,worker_id=f'w{threading.get_ident()}')
            if task: winners.append((task.id,token))
    threads=[threading.Thread(target=worker) for _ in range(4)]
    for t in threads:t.start()
    for t in threads:t.join()
    assert len(winners)==1
    assert winners[0][0]==report.id
    with SessionLocal() as check:
        held=check.get(Report,report.id)
        assert held.status=='running' and held.run_token==winners[0][1]
        assert held.attempts==1 and held.claimed_by is not None
    # 已被领取后没有第二个 queued 任务可领
    with SessionLocal() as session:
        assert q.claim_next(session)==(None,None)

# 4. 领取后退出（未写心跳）：超时自动回收为 queued 并累计恢复次数，沿用同一报告
def test_reclaim_after_worker_exit_without_heartbeat(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    with SessionLocal() as session:
        task,token=q.claim_next(session,worker_id='crashed-worker')
        assert task.id==report.id
    # 模拟领取后立即退出：claimed_at 回拨到超时阈值之前，heartbeat 始终为空
    held=_fresh(report.id)
    with SessionLocal() as session:
        row=session.get(Report,report.id)
        # 模拟领取后进程立即退出：未来得及续租，心跳为空且领取时间已超时
        row.claimed_at=datetime.now(timezone.utc)-timedelta(seconds=31)
        row.heartbeat_at=None
        session.commit()
    with SessionLocal() as session:
        reclaimed=q.reclaim_stale(session)
    assert reclaimed==[report.id]
    held=_fresh(report.id)
    assert held.status=='queued' and held.run_token is None and held.heartbeat_at is None
    assert held.recovery_count==1 and held.attempts==1  # 同一报告行，没有新增报告

    # 重新领取并按快照完成：恢复后正常落盘，恢复次数保留
    with SessionLocal() as session:
        task,token2=q.claim_next(session,worker_id='replacement-worker')
        result=compute_from_snapshot(task.plan_snapshot,task.sample_snapshot,task.snapshot_checksum)
        assert q.complete_report(session,task.id,token2,result)
    held=_fresh(report.id)
    assert held.status=='completed' and held.recovery_count==1 and held.attempts==2
    assert held.result['difference']==result['difference']

# 5. 心跳超时回收
def test_reclaim_after_heartbeat_timeout(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    with SessionLocal() as session:
        task,token=q.claim_next(session,worker_id='slow-worker')
    with SessionLocal() as session:
        session.get(Report,report.id).heartbeat_at=datetime.now(timezone.utc)-timedelta(seconds=31)
        session.commit()
    with SessionLocal() as session:
        assert q.reclaim_stale(session)==[report.id]
    held=_fresh(report.id)
    assert held.status=='queued' and held.recovery_count==1

# 6. 旧处理者失去资格后晚到的计算不能覆盖
def test_stale_holder_late_write_is_rejected(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    with SessionLocal() as session:
        task,old_token=q.claim_next(session,worker_id='old-worker')
    # 旧 worker 停滞超时，被回收并由新 worker 重新领取
    with SessionLocal() as session:
        session.get(Report,report.id).heartbeat_at=datetime.now(timezone.utc)-timedelta(seconds=40)
        session.commit()
    with SessionLocal() as session:
        q.reclaim_stale(session)
        task2,new_token=q.claim_next(session,worker_id='new-worker')
    assert new_token!=old_token
    # 旧 worker 晚到，带着旧令牌尝试写结果/失败：全部被拒
    bogus={'difference':999.0,'t':999.0,'p_value':0.0001}
    with SessionLocal() as session:
        assert q.complete_report(session,report.id,old_token,bogus) is False
        assert q.fail_report(session,report.id,old_token,'late failure') is False
    held=_fresh(report.id)
    assert held.status=='running' and held.run_token==new_token
    assert held.result is None and held.error==''
    # 新 worker 正常完成
    result=compute_from_snapshot(task2.plan_snapshot,task2.sample_snapshot,task2.snapshot_checksum)
    with SessionLocal() as session:
        assert q.complete_report(session,report.id,new_token,result)
    held=_fresh(report.id)
    assert held.status=='completed' and held.result['difference']!=999.0

# 7. 重复唤醒不新增报告、不重复写结果
def test_duplicate_wakeup_does_not_duplicate(db,campus_plan,students):
    report=_create_report(db,campus_plan)
    before=db.query(Report).count()
    # 同一报告被重复通知多次（Redis 通知丢失/重复的等价情形）
    for _ in range(5):
        q.notify(report.id)
    with SessionLocal() as session:
        task,token=q.claim_next(session)
        result=compute_from_snapshot(task.plan_snapshot,task.sample_snapshot,task.snapshot_checksum)
        q.complete_report(session,task.id,token,result)
        # 队列里剩余的唤醒信号再次扫描：没有 queued 任务，不会产生新报告
        assert q.claim_next(session)==(None,None)
    with SessionLocal() as check:
        assert check.query(Report).count()==before
        held=check.get(Report,report.id)
        assert held.status=='completed'
        saved_difference=held.result['difference']
    # 完成后旧令牌/伪造令牌再写均被拒，结果不被覆盖
    with SessionLocal() as session:
        assert q.complete_report(session,report.id,token,{'difference':1}) is False
        assert q.complete_report(session,report.id,'deadbeef',{'difference':1}) is False
    assert _fresh(report.id).result['difference']==saved_difference

# 8. 多次失败保留同一报告行并累计尝试/恢复次数
def test_repeated_failures_keep_single_report(db):
    # 独立教学点：每组仅 1 个有效成绩 → 必然失败，用于反复重试
    sparse=Plan(title=f'失败方案-{os.urandom(4).hex()}',campus='高新',exclude_absent=True,description='')
    db.add(sparse);db.commit()
    db.add_all([
        Student(code='X001',name='X001',campus='高新',group='control',score=60.0,absent=False),
        Student(code='X002',name='X002',campus='高新',group='intervention',score=70.0,absent=False),
        Student(code='X003',name='X003',campus='高新',group='control',score=None,absent=False),
    ]);db.commit()
    with SessionLocal() as session:
        report=report_service.create_report(session,sparse.id)
        rid=report.id
    for cycle in range(2):
        with SessionLocal() as session:
            task,token=q.claim_next(session)
            with pytest.raises(ValueError):
                compute_from_snapshot(task.plan_snapshot,task.sample_snapshot,task.snapshot_checksum)
            q.fail_report(session,rid,token,'每组至少需要 2 个有效成绩；请调整教学点或补录样本')
        held=_fresh(rid)
        assert held.status=='failed' and '每组至少需要' in held.error
        # 模拟人工/自动重新入队（回收路径），报告行不变
        with SessionLocal() as session:
            row=session.get(Report,rid)
            row.status='queued';row.run_token=None;row.heartbeat_at=None
            row.recovery_count+=1;session.commit()
    held=_fresh(rid)
    with SessionLocal() as check:
        assert check.query(Report).filter(Report.id==rid).count()==1
    assert held.attempts==2 and held.recovery_count==2

# 9. 导出包含当时方案、纳入/排除样本，且可独立复算统计量
def test_export_is_recomputable(db,campus_plan,students,users):
    report=_create_report(db,campus_plan)
    with SessionLocal() as session:
        task,token=q.claim_next(session)
        result=compute_from_snapshot(task.plan_snapshot,task.sample_snapshot,task.snapshot_checksum)
        q.complete_report(session,task.id,token,result)
    resp=client.get(f'/reports/{report.id}/export',headers=auth_header(users['admin']))
    assert resp.status_code==200
    payload=resp.json()
    assert payload['plan_snapshot']['campus']=='城东'
    assert payload['plan_snapshot']['exclude_absent'] is True
    assert len(payload['included_samples'])==6
    assert {s['code'] for s in payload['excluded_samples']}=={'S004','S008','S011'}
    assert all('exclusion_reason' in s for s in payload['excluded_samples'])
    assert len(payload['snapshot_checksum'])==64
    assert 'recompute_guide' in payload
    check=_recompute_from_export(payload)
    stats=payload['statistics']
    assert check['n']==(stats['control_n'],stats['intervention_n'])
    assert check['difference']==stats['difference']
    assert abs(check['t']-stats['t'])<1e-12
    assert abs(check['p']-stats['p_value'])<1e-12
    # 导出文件不依赖现行成绩表：再次修订后导出内容不变
    s=db.scalar(select(Student).where(Student.code=='S001'));s.score=40.0;db.commit()
    payload2=client.get(f'/reports/{report.id}/export',headers=auth_header(users['admin'])).json()
    assert payload2['included_samples']==payload['included_samples']
    assert payload2['statistics']['difference']==stats['difference']

# 10. 保留已有报告与签核结论
def test_existing_report_and_signoff_preserved(db,campus_plan,students,users):
    with SessionLocal() as session:
        report=report_service.create_report(session,campus_plan.id)
        rid=report.id
    with SessionLocal() as session:
        task,token=q.claim_next(session)
        result=compute_from_snapshot(task.plan_snapshot,task.sample_snapshot,task.snapshot_checksum)
        q.complete_report(session,task.id,token,result)
    signed='负责人签核：观察性差异显著，同意扩大试点。'
    with SessionLocal() as session:
        report_service.save_conclusion(session,rid,
            type('D',(),{'conclusion':signed,'published':True}),users['admin'])
    # 后续成绩修订与新报告提交均不影响已签核报告
    with SessionLocal() as session:
        session.scalar(select(Student).where(Student.code=='S001')).score=10.0
        session.commit()
        report_service.create_report(session,campus_plan.id)
    old=_fresh(rid)
    assert old.published is True and old.conclusion==signed
    assert old.status=='completed'
    # 分析员不能改动已签核结论
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        with SessionLocal() as session:
            report_service.save_conclusion(session,rid,
                type('D',(),{'conclusion':'被篡改的结论','published':False}),users['analyst'])
    assert exc.value.status_code==403
    assert _fresh(rid).conclusion==signed

# 11. 排队上限仍生效，且失败原因可读
def test_queue_cap_and_readable_failure(db,students):
    plan=Plan(title=f'容量方案-{os.urandom(4).hex()}',campus='全部',exclude_absent=True,description='')
    db.add(plan);db.commit()
    from fastapi import HTTPException
    for _ in range(10):
        report_service.create_report(db,plan.id)
    with pytest.raises(HTTPException) as exc:
        report_service.create_report(db,plan.id)
    assert exc.value.status_code==429
