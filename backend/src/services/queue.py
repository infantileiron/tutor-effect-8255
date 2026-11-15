"""后台任务领取与中断恢复。

并发安全要点：
- 领取使用 SELECT ... FOR UPDATE SKIP LOCKED，多进程只有一个处理者拿到同一行；
- 每次领取生成 run_token 围栏令牌，完成/失败写入必须同时匹配 status='running' 与该令牌，
  失去资格的旧处理者晚到的结果匹配 0 行，不能覆盖新处理者或已完成报告；
- 心跳线程持续续租，心跳超时的 running 任务被回收为 queued 并累计 recovery_count，
  重试沿用原报告行，不新增报告、不重复写结果。
"""
import os,logging,socket,secrets,threading
from datetime import datetime,timedelta,timezone
import redis
from sqlalchemy import select,update
from src.config.database import SessionLocal
from src.models.entities import Report

QUEUE_KEY='tutor:reports'
queue=redis.Redis.from_url(os.environ['REDIS_URL'])
WORKER_ID=f'{socket.gethostname()}:{os.getpid()}'
HEARTBEAT_INTERVAL=int(os.environ.get('WORKER_HEARTBEAT_INTERVAL','8'))
HEARTBEAT_TIMEOUT=timedelta(seconds=int(os.environ.get('WORKER_HEARTBEAT_TIMEOUT','30')))
log=logging.getLogger('worker')

def utcnow():
    return datetime.now(timezone.utc)

def notify(report_id):
    # Redis 仅作唤醒信号；丢失也无妨，worker 每轮都会扫描数据库待执行任务
    try:
        queue.lpush(QUEUE_KEY,report_id)
    except Exception:
        logging.exception('redis notify failed for report=%s; db scan will pick it up',report_id)

def reclaim_stale(db):
    """把心跳超时（或领取后未来得及写心跳即崩溃）的 running 任务回收为 queued。"""
    cutoff=utcnow()-HEARTBEAT_TIMEOUT
    stale=db.scalars(select(Report).where(Report.status=='running').with_for_update(skip_locked=True)).all()
    reclaimed=[]
    def _aware(value):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    for task in stale:
        beat=_aware(task.heartbeat_at)
        claimed=_aware(task.claimed_at)
        heartbeat_lost=beat is not None and beat<cutoff
        never_beat=beat is None and claimed is not None and claimed<cutoff
        if heartbeat_lost or never_beat:
            task.status='queued'
            task.run_token=None
            task.claimed_by=None
            task.claimed_at=None
            task.heartbeat_at=None
            task.recovery_count=(task.recovery_count or 0)+1
            reclaimed.append(task.id)
    db.commit()
    for rid in reclaimed:
        notify(rid)
        log.warning('reclaimed stale running report=%s recovery=%s',rid,True)
    return reclaimed

def claim_next(db,worker_id=WORKER_ID):
    """行锁领取一个 queued 任务，返回 (任务, 围栏令牌)，无任务返回 (None, None)。"""
    task=db.scalar(select(Report).where(Report.status=='queued').order_by(Report.id)
                   .with_for_update(skip_locked=True))
    if not task: return None,None
    token=secrets.token_hex(16)
    now=utcnow()
    task.status='running'
    task.run_token=token
    task.claimed_by=worker_id
    task.claimed_at=now
    task.heartbeat_at=now
    task.attempts=(task.attempts or 0)+1
    db.commit()
    db.refresh(task)
    return task,token

class Heartbeat:
    """后台续租线程；发现自己已失去当前处理者资格时置位 lost。"""
    def __init__(self,report_id,token):
        self.report_id=report_id
        self.token=token
        self.lost=False
        self._stop=threading.Event()
        self._thread=threading.Thread(target=self._run,name=f'heartbeat-{report_id}',daemon=True)
    def start(self):
        self._thread.start()
    def stop(self):
        self._stop.set()
    def _run(self):
        while not self._stop.wait(HEARTBEAT_INTERVAL):
            try:
                with SessionLocal() as db:
                    rowcount=db.execute(
                        update(Report).where(Report.id==self.report_id,
                                             Report.status=='running',
                                             Report.run_token==self.token)
                        .values(heartbeat_at=utcnow())).rowcount
                    db.commit()
                if not rowcount:
                    self.lost=True
                    log.error('lost lease for report=%s token=%s; result will be discarded',self.report_id,self.token[:8])
                    return
            except Exception:
                logging.exception('heartbeat update failed for report=%s',self.report_id)

def _guarded_finish(db,report_id,token,**values):
    """条件写入：仅当前处理者（status=running 且 run_token 匹配）可落盘，返回是否被接受。"""
    values.setdefault('heartbeat_at',utcnow())
    rowcount=db.execute(update(Report).where(Report.id==report_id,
                                              Report.status=='running',
                                              Report.run_token==token)
                        .values(**values)).rowcount
    db.commit()
    return rowcount==1

def complete_report(db,report_id,token,result):
    return _guarded_finish(db,report_id,token,status='completed',result=result,error='')

def fail_report(db,report_id,token,message):
    return _guarded_finish(db,report_id,token,status='failed',error=message[:2000])
