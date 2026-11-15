import json
from fastapi import APIRouter,Depends,HTTPException
from fastapi.responses import Response
from sqlalchemy import select
from src.config.database import get_db
from src.utils.security import current_user,editor,admin
from src.schemas.business import RunInput,ConclusionInput
from src.services import reports
from src.controllers.business import serialize,serialize_many
from src.models.entities import Audit

router=APIRouter()

@router.get('/reports')
def listing(db=Depends(get_db),user=Depends(current_user)):
    return serialize_many(reports.list_reports(db))

@router.post('/reports',status_code=202)
def create(data:RunInput,db=Depends(get_db),user=Depends(editor)):
    return serialize(reports.create_report(db,data.plan_id))

@router.get('/reports/{id}')
def detail(id:int,db=Depends(get_db),user=Depends(current_user)):
    return serialize(reports.get_report(db,id))

@router.patch('/reports/{id}/conclusion')
def conclusion(id:int,data:ConclusionInput,db=Depends(get_db),user=Depends(editor)):
    return serialize(reports.save_conclusion(db,id,data,user))

@router.get('/reports/{id}/export')
def export(id:int,db=Depends(get_db),user=Depends(current_user)):
    item=reports.get_report(db,id)
    if item.status!='completed': raise HTTPException(409,'报告尚未完成')
    result=item.result or {}
    statistics={k:v for k,v in result.items()
                if k not in ('sample_snapshot','excluded_snapshot','plan_snapshot')}
    content={
        'report_id':item.id,
        'plan_id':item.plan_id,
        'status':item.status,
        'created_at':item.created_at,
        'published':item.published,
        'conclusion':item.conclusion,
        'recovery_count':item.recovery_count or 0,
        'attempts':item.attempts or 0,
        # 受理时点固化的方案：后续方案修订不改变本报告口径
        'plan_snapshot':item.plan_snapshot or result.get('plan_snapshot'),
        'statistics':statistics,
        # 纳入与排除样本全量留痕，配合快照校验码可独立复算统计量
        'included_samples':result.get('sample_snapshot',[]),
        'excluded_samples':result.get('excluded_snapshot',[]),
        'sample_snapshot':item.sample_snapshot or [],
        'snapshot_checksum':item.snapshot_checksum or result.get('snapshot_checksum',''),
        'recompute_guide':{
            'rule':'仅使用 included_samples（included=true）的 score，按 group 分为两组；'
                   '排除规则与教学点口径见 plan_snapshot；空成绩与缺考样本见 excluded_samples。',
            'method':result.get('method','Welch 独立样本 t 检验；双侧 α=0.05；干预组减对照组；95% CI'),
        },
    }
    return Response(json.dumps(content,ensure_ascii=False,indent=2),media_type='application/json',
                    headers={'Content-Disposition':f'attachment; filename="tutor-effect-report-{id}.json"'})

@router.get('/audit')
def audit(db=Depends(get_db),user=Depends(admin)):
    return serialize_many(db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100)).all())
