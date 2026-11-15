import os
os.environ.setdefault('DATABASE_URL','postgresql+psycopg://tutor@localhost:17702/tutor_test')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/1')
os.environ.setdefault('JWT_SECRET','test-secret')
os.environ.setdefault('SEED_DEMO','false')
os.environ.setdefault('WORKER_HEARTBEAT_INTERVAL','1')
os.environ.setdefault('WORKER_HEARTBEAT_TIMEOUT','3')

import pytest
from sqlalchemy import select,func
from src.config.database import Base,engine,SessionLocal
from src.models.entities import User,Student,Plan,Report
from src.utils.security import hash_password,token_for

@pytest.fixture(scope='session',autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield

@pytest.fixture
def db():
    with SessionLocal() as session:
        session.query(Report).delete()
        session.query(Plan).delete()
        session.query(Student).delete()
        session.query(User).delete()
        session.commit()
        yield session

@pytest.fixture
def users(db):
    made={}
    for role in ('admin','analyst','viewer'):
        u=User(email=f'{role}@t.local',role=role,password_hash=hash_password('pw123456'))
        db.add(u);made[role]=u
    db.commit()
    return made

def auth_header(user):
    return {'Authorization':f'Bearer {token_for(user)}'}

@pytest.fixture
def campus_plan(db):
    plan=Plan(title=f'方案-{os.urandom(4).hex()}',campus='城东',exclude_absent=True,description='检验城东辅导效果')
    db.add(plan);db.commit();db.refresh(plan)
    return plan

@pytest.fixture
def students(db):
    # 城东 4 对照 + 5 干预，含 1 缺考清空成绩、1 缺考带历史成绩、1 空成绩；城西 2 人验证教学点口径
    rows=[
        ('S001','城东','control',70.0,False),
        ('S002','城东','control',72.0,False),
        ('S003','城东','control',74.0,False),
        ('S004','城东','control',None,False),       # 空成绩，永远排除
        ('S005','城东','intervention',80.0,False),
        ('S006','城东','intervention',82.0,False),
        ('S007','城东','intervention',84.0,False),
        ('S008','城东','intervention',None,True),    # 缺考且清空成绩：先命中空成绩规则
        ('S011','城东','intervention',55.0,True),    # 缺考带历史成绩：命中缺考排除规则
        ('S009','城西','control',50.0,False),
        ('S010','城西','intervention',99.0,False),
    ]
    out=[]
    for code,campus,group,score,absent in rows:
        s=Student(code=code,name=code,campus=campus,group=group,score=score,absent=absent)
        db.add(s);out.append(s)
    db.commit()
    return out
