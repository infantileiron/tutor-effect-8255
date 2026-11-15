"""真实 worker 子进程端到端验证。

覆盖：正常完成、领取后进程崩溃自动恢复、多 worker 并发领取不重复、
重复唤醒不新增报告/不重复写结果。使用独立 Redis 逻辑库 2，避免与其他测试串扰。
"""
import os,subprocess,sys,time,signal
from pathlib import Path
import pytest
from sqlalchemy import select

from src.config.database import SessionLocal
from src.models.entities import Report,Plan,Student
from src.services import reports as report_service
from src.services.statistics import compute_from_snapshot

BACKEND_DIR=Path(__file__).resolve().parents[1]
WORKER_ENV={
    'DATABASE_URL':'postgresql+psycopg://tutor@localhost:17702/tutor_test',
    'REDIS_URL':'redis://localhost:6379/2',
    'JWT_SECRET':'test-secret','SEED_DEMO':'false',
    'WORKER_HEARTBEAT_INTERVAL':'1','WORKER_HEARTBEAT_TIMEOUT':'3',
    'PYTHONPATH':str(BACKEND_DIR),'PATH':os.environ.get('PATH',''),
}

def _spawn_worker(extra_env=None):
    env=dict(WORKER_ENV)
    if extra_env: env.update(extra_env)
    log=open(f'/tmp/worker-test-{os.getpid()}-{time.time_ns()}.log','w')
    proc=subprocess.Popen([sys.executable,'-m','src.worker'],cwd=BACKEND_DIR,
                          env=env,stdout=log,stderr=subprocess.STDOUT,
                          start_new_session=True)
    return proc,log

def _wait_status(report_id,status,timeout=30):
    deadline=time.time()+timeout
    last=None
    while time.time()<deadline:
        with SessionLocal() as session:
            obj=session.get(Report,report_id)
            last=obj.status
            if obj.status==status:
                return obj
        time.sleep(0.3)
    raise AssertionError(f'report {report_id} 未在 {timeout}s 内到达 {status}，最后状态={last}')

@pytest.fixture
def plan_with_students(db):
    plan=Plan(title=f'E2E方案-{os.urandom(4).hex()}',campus='全部',exclude_absent=True,description='端到端')
    db.add(plan);db.commit();db.refresh(plan)
    rows=[]
    for i in range(20):
        rows.append(Student(code=f'E{i:03}',name=f'E{i}',campus='城东',
                            group='control' if i<10 else 'intervention',
                            score=70.0+(i%5) if i<10 else 80.0+(i%5),absent=False))
    db.add_all(rows);db.commit()
    return plan

def _flush_redis():
    import redis
    redis.Redis.from_url(WORKER_ENV['REDIS_URL']).flushdb()

# 1. 真实 worker 正常完成，结果与直接按快照复算一致
def test_real_worker_completes_from_snapshot(db,plan_with_students):
    _flush_redis()
    with SessionLocal() as session:
        report=report_service.create_report(session,plan_with_students.id)
        rid=report.id
        expected=compute_from_snapshot(report.plan_snapshot,report.sample_snapshot,report.snapshot_checksum)
    proc,log=_spawn_worker()
    try:
        done=_wait_status(rid,'completed')
        assert done.result['difference']==expected['difference']
        assert done.result['t']==expected['t']
        assert done.attempts==1 and done.recovery_count==0
        assert done.run_token is not None and done.claimed_by is not None
    finally:
        proc.send_signal(signal.SIGTERM);proc.wait(timeout=10);log.close()

# 2. 领取后 worker 崩溃（SIGKILL）：任务自动恢复并由新 worker 完成，同一报告、恢复次数+1
def test_worker_crash_after_claim_auto_recovers(db,plan_with_students):
    _flush_redis()
    with SessionLocal() as session:
        report=report_service.create_report(session,plan_with_students.id)
        rid=report.id
    first,log1=_spawn_worker({'WORKER_COMPUTE_DELAY':'8'})
    try:
        # 慢计算保证任务停留在 running，随后 SIGKILL 模拟崩溃（无清理、无最终心跳）
        deadline=time.time()+15
        while time.time()<deadline:
            with SessionLocal() as session:
                if session.get(Report,rid).status=='running':break
            time.sleep(0.2)
        else:
            raise AssertionError('worker 未在预期时间内领取任务')
        os.killpg(os.getpgid(first.pid),signal.SIGKILL)
        first.wait(timeout=10)
    finally:
        if first.poll() is None:
            os.killpg(os.getpgid(first.pid),signal.SIGKILL)
        log1.close()
    # 崩溃后心跳停更；新 worker 启动，超时回收后重新领取完成
    second,log2=_spawn_worker()
    try:
        done=_wait_status(rid,'completed',timeout=40)
        assert done.recovery_count>=1,'崩溃任务应被自动回收并累计恢复次数'
        assert done.attempts>=2,'同一报告应被重新领取而非新建'
        with SessionLocal() as session:
            assert session.query(Report).filter(Report.id==rid).count()==1
    finally:
        second.send_signal(signal.SIGTERM);second.wait(timeout=10);log2.close()

# 3. 多个 worker 并发：同一报告只被一个处理者完成，无重复结果行
def test_multiple_workers_single_claim(db,plan_with_students):
    _flush_redis()
    with SessionLocal() as session:
        report=report_service.create_report(session,plan_with_students.id)
        rid=report.id
    procs=[]
    try:
        for _ in range(4):
            proc,log=_spawn_worker();procs.append((proc,log))
        done=_wait_status(rid,'completed',timeout=30)
        assert done.status=='completed'
        with SessionLocal() as session:
            assert session.query(Report).count()==1
            assert session.get(Report,rid).attempts==1  # 只被领取一次
    finally:
        for proc,log in procs:
            proc.send_signal(signal.SIGTERM)
        for proc,log in procs:
            proc.wait(timeout=10);log.close()

# 4. 重复唤醒：同一报告多次通知，只有一次领取与一次结果写入
def test_duplicate_wakeup_real_worker(db,plan_with_students):
    _flush_redis()
    from src.services import queue as q
    with SessionLocal() as session:
        report=report_service.create_report(session,plan_with_students.id)
        rid=report.id
    for _ in range(6):
        q.notify(rid)
    proc,log=_spawn_worker()
    try:
        done=_wait_status(rid,'completed',timeout=30)
        assert done.attempts==1
        with SessionLocal() as session:
            assert session.query(Report).count()==1
    finally:
        proc.send_signal(signal.SIGTERM);proc.wait(timeout=10);log.close()
    # 队列残余信号不应在之后触发任何新报告
    time.sleep(3)
    with SessionLocal() as session:
        assert session.query(Report).count()==1
        assert session.get(Report,rid).status=='completed'

# 5. 失败任务：真实 worker 落可读原因，报告行保留
def test_real_worker_failure_keeps_report(db):
    _flush_redis()
    plan=Plan(title=f'E2E失败-{os.urandom(4).hex()}',campus='滨江',exclude_absent=True,description='')
    db.add(plan);db.commit()
    db.add_all([
        Student(code='F01',name='F01',campus='滨江',group='control',score=60.0,absent=False),
        Student(code='F02',name='F02',campus='滨江',group='intervention',score=70.0,absent=False),
    ]);db.commit()
    with SessionLocal() as session:
        report=report_service.create_report(session,plan.id)
        rid=report.id
    proc,log=_spawn_worker()
    try:
        failed=_wait_status(rid,'failed',timeout=20)
        assert '每组至少需要' in failed.error
        with SessionLocal() as session:
            assert session.query(Report).filter(Report.id==rid).count()==1
    finally:
        proc.send_signal(signal.SIGTERM);proc.wait(timeout=10);log.close()
