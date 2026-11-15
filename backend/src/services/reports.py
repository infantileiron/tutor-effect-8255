from fastapi import HTTPException
from sqlalchemy import select
from src.models.entities import Report,Plan,Student
from src.services.queue import notify
from src.services.statistics import build_snapshot,utcnow_iso

def list_reports(db):
    return db.scalars(select(Report).order_by(Report.id.desc())).all()

def create_report(db,plan_id):
    plan=db.get(Plan,plan_id)
    if not plan: raise HTTPException(404,'辅导方案不存在')
    running=db.scalars(select(Report).where(Report.status.in_(['queued','running']))).all()
    if len(running)>=10: raise HTTPException(429,'待执行任务已达10个，请稍后再试')
    # 受理即在同一事务、同一时点留存方案与全量样本快照；后续修订不影响本报告的复算依据
    students=db.scalars(select(Student).order_by(Student.code)).all()
    plan_snap,sample_snap,checksum=build_snapshot(students,plan)
    item=Report(plan_id=plan_id,status='queued',created_at=utcnow_iso(),
                plan_snapshot=plan_snap,sample_snapshot=sample_snap,
                snapshot_checksum=checksum,recovery_count=0,attempts=0)
    db.add(item);db.commit();db.refresh(item)
    notify(item.id)
    return item

def get_report(db,id):
    item=db.get(Report,id)
    if not item: raise HTTPException(404,'报告不存在')
    return item

def save_conclusion(db,id,data,user):
    item=get_report(db,id)
    if item.status!='completed': raise HTTPException(409,'计算完成后才能起草结论')
    if item.published and user.role!='admin': raise HTTPException(403,'已签核报告仅管理员可更改')
    if data.published and user.role!='admin': raise HTTPException(403,'仅管理员可签核发布')
    item.conclusion=data.conclusion;item.published=data.published
    db.commit();db.refresh(item)
    return item
