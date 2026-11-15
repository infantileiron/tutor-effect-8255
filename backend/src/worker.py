import logging,os,time,traceback
from pathlib import Path
from src.config.database import SessionLocal
from src.services import queue as q
from src.services.statistics import compute_from_snapshot

logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
log=logging.getLogger('worker')

def run_forever():
    print(f'tutor effect worker started id={q.WORKER_ID}',flush=True)
    while True:
        Path('/tmp/worker-heartbeat').touch()
        try:
            q.queue.brpop(q.QUEUE_KEY,timeout=2)
            with SessionLocal() as db:
                q.reclaim_stale(db)            # 回收心跳超时的中断任务，自动恢复为 queued
                task,token=q.claim_next(db)    # 行锁 SKIP LOCKED 领取，多进程只认当前处理者
            if not task:
                continue
            hb=q.Heartbeat(task.id,token)
            hb.start()
            try:
                # 测试钩子：通过 WORKER_COMPUTE_DELAY 模拟慢计算，生产默认不设置即无延迟
                delay=float(os.environ.get('WORKER_COMPUTE_DELAY','0'))
                if delay>0: time.sleep(delay)
                # 仅按受理时点固化的方案与样本快照复算，不回读可被修订的成绩/方案表
                result=compute_from_snapshot(task.plan_snapshot,task.sample_snapshot,task.snapshot_checksum)
                if hb.lost:
                    log.error('report=%s lease lost; discarding computed result to avoid overwrite',task.id)
                else:
                    with SessionLocal() as db:
                        accepted=q.complete_report(db,task.id,token,result)
                    log.info('report=%s completed accepted=%s token=%s',task.id,accepted,token[:8])
            except Exception as e:
                if hb.lost:
                    log.error('report=%s lease lost; discarding failure write: %s',task.id,e)
                else:
                    with SessionLocal() as db:
                        accepted=q.fail_report(db,task.id,token,str(e))
                    log.warning('report=%s failed accepted=%s reason=%s',task.id,accepted,e)
            finally:
                hb.stop()
        except Exception:
            traceback.print_exc()
            time.sleep(2)

if __name__=='__main__':
    run_forever()
