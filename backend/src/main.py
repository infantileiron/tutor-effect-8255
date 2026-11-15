import os,logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from sqlalchemy import text,inspect
from src.config.database import Base,engine,SessionLocal
from src.models import entities
from src.routes import auth,students,plans,reports
from src.middlewares.errors import ErrorMiddleware
from src.middlewares.audit import AuditMiddleware
from src.services.queue import queue

# 小项目无完整迁移框架：对已有库幂等补齐 002 新增列，新库由 create_all 直接建全
_REPORT_ADDED_COLUMNS={
    'plan_snapshot':'JSON','sample_snapshot':'JSON','snapshot_checksum':"VARCHAR(64) NOT NULL DEFAULT ''",
    'run_token':'VARCHAR(64)','claimed_by':'VARCHAR(120)','claimed_at':'TIMESTAMPTZ',
    'heartbeat_at':'TIMESTAMPTZ','recovery_count':'INTEGER NOT NULL DEFAULT 0',
    'attempts':'INTEGER NOT NULL DEFAULT 0',
}
def ensure_schema():
    Base.metadata.create_all(engine)
    inspector=inspect(engine)
    existing={c['name'] for c in inspector.get_columns('reports')}
    with engine.begin() as conn:
        for name,ddl in _REPORT_ADDED_COLUMNS.items():
            if name not in existing:
                conn.execute(text(f'ALTER TABLE reports ADD COLUMN {name} {ddl}'))
                logging.warning('added reports.%s via lightweight migration',name)

@asynccontextmanager
async def lifespan(app):
    ensure_schema()
    if os.getenv('SEED_DEMO','true').lower()=='true':
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception: logging.exception('seed failed; server remains available')
    yield
app=FastAPI(title='课程辅导效果检验',lifespan=lifespan)
app.add_middleware(AuditMiddleware)
app.add_middleware(ErrorMiddleware)
for router in [auth.router,students.router,plans.router,reports.router]: app.include_router(router)
@app.get('/health')
def health():
    with SessionLocal() as db: db.execute(text('select 1'))
    queue.ping()
    return {'status':'healthy'}
