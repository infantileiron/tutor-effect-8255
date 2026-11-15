from sqlalchemy import String,Float,Boolean,Integer,ForeignKey,JSON,Text,DateTime
from sqlalchemy.orm import Mapped,mapped_column
from datetime import datetime
from src.config.database import Base
class User(Base):
    __tablename__='users'
    id:Mapped[int]=mapped_column(primary_key=True)
    email:Mapped[str]=mapped_column(String(120),unique=True)
    role:Mapped[str]=mapped_column(String(20))
    password_hash:Mapped[str]=mapped_column(String(200))
class Student(Base):
    __tablename__='students'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(50),unique=True)
    name:Mapped[str]=mapped_column(String(80))
    campus:Mapped[str]=mapped_column(String(50))
    group:Mapped[str]=mapped_column(String(20))
    score:Mapped[float|None]=mapped_column(Float,nullable=True)
    absent:Mapped[bool]=mapped_column(Boolean,default=False)
class Plan(Base):
    __tablename__='plans'
    id:Mapped[int]=mapped_column(primary_key=True)
    title:Mapped[str]=mapped_column(String(120),unique=True)
    campus:Mapped[str]=mapped_column(String(50),default='全部')
    exclude_absent:Mapped[bool]=mapped_column(Boolean,default=True)
    description:Mapped[str]=mapped_column(Text,default='')
class Report(Base):
    __tablename__='reports'
    id:Mapped[int]=mapped_column(primary_key=True)
    plan_id:Mapped[int]=mapped_column(ForeignKey('plans.id'))
    status:Mapped[str]=mapped_column(String(20),default='queued')
    result:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    error:Mapped[str]=mapped_column(Text,default='')
    conclusion:Mapped[str]=mapped_column(Text,default='')
    published:Mapped[bool]=mapped_column(Boolean,default=False)
    created_at:Mapped[str]=mapped_column(String(40))
    # 受理时点固化的方案与样本，后续成绩/方案修订不影响按该批样本重算
    plan_snapshot:Mapped[dict|None]=mapped_column('plan_snapshot',JSON,nullable=True)
    sample_snapshot:Mapped[list|None]=mapped_column('sample_snapshot',JSON,nullable=True)
    snapshot_checksum:Mapped[str]=mapped_column(String(64),default='')
    # 多进程领取围栏：run_token 标识当前处理者，心跳用于中断后自动恢复
    run_token:Mapped[str|None]=mapped_column(String(64),nullable=True)
    claimed_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
    claimed_at:Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    heartbeat_at:Mapped[str|None]=mapped_column(DateTime(timezone=True),nullable=True)
    recovery_count:Mapped[int]=mapped_column(Integer,default=0)
    attempts:Mapped[int]=mapped_column(Integer,default=0)
class Audit(Base):
    __tablename__='audit_logs'
    id:Mapped[int]=mapped_column(primary_key=True)
    actor:Mapped[str]=mapped_column(String(120))
    action:Mapped[str]=mapped_column(String(200))
    status:Mapped[int]=mapped_column(Integer)
    created_at:Mapped[str]=mapped_column(String(40))
