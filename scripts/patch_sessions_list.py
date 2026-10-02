#!/usr/bin/env python3
# 在服务器上就地给 feature 分支源码补工作列表端点（幂等，不整文件覆盖）。
# 事故复盘：本地 checkout 在 main，服务器部署的是 feature/T-GA-INT-01-e2e-integration；
# 直接 scp 覆盖 → main 的 schemas.py 缺 ClinicalAction → 启动即 ImportError。
import io

R = "/mnt/ee16/home/student03/uai/src"
LOG = []


def insert_before(path, first_line, block, tag):
    src = io.open(path, encoding="utf-8").read()
    if first_line in src:
        LOG.append("SKIP  " + tag)
        return
    src = src.replace(block, "", 0)  # no-op keeps linters quiet
    io.open(path, "a", encoding="utf-8", newline="").write("\n\n" + block)
    LOG.append("OK    " + tag + " (appended)")


def rep_once(path, old, new, tag, marker=None):
    """幂等替换。

    marker 是**已插入内容的唯一标识**（如函数名）。
    早先版本拿 `new.strip() in src` 判幂等 —— 一旦后来改了块内容，
    比对就失效，于是同一个端点被插了两份，注册出两条 GET ""。
    教训：幂等标记必须独立于被替换的正文。
    """
    src = io.open(path, encoding="utf-8").read()
    if marker and marker in src:
        LOG.append("SKIP  " + tag)
        return
    if old not in src:
        LOG.append("FAIL  " + tag + " : anchor not found, refused")
        return
    io.open(path, "w", encoding="utf-8", newline="").write(src.replace(old, new, 1))
    LOG.append("OK    " + tag)


# ---------- 1) schemas.py：追加列表项 schema（模块末尾追加，名字都在作用域内） ----------
SCHEMA_BLOCK = '''class SessionListItem(BaseModel):
    """工作列表单行 —— 对应设计稿 01_工作列表 的一行。

    脱敏纪律：只出**已脱敏**的 patient_name_masked（姓+**），不出完整姓名、
    不出出生日期 —— 列表是共享屏幕，暴露全名即泄露面。
    年龄由服务端从 birth_date 派生，避免把生日发到前端再算。
    """
    session_id: str
    patient_id: str
    patient_name_masked: Optional[str] = None
    gender: Optional[str] = None
    age: Optional[int] = None
    status: SessionStatus
    target_organs: List[str] = []
    created_at: datetime
    updated_at: datetime
    ttl_remaining_seconds: Optional[int] = None


class SessionListResponse(BaseModel):
    """工作列表整体，分组在服务端做完，前端不再二次分类。

    契约 P2「一次决策」：列表页医生此刻只需挑一个病人开始，
    只给两组（进行中 / 今日待处理），不按七种状态切页。
    """
    in_progress: List[SessionListItem] = []
    pending_today: List[SessionListItem] = []
    total_pending: int = 0

'''

# ---------- 2) session_repository.py：列表查询（插在 renew_session 之前，仍在类内） ----------
REPO_METHOD = '''    async def list_sessions(self, limit: int = 20) -> list[dict]:
        """工作列表：按创建时间倒序取最近会话，联表 patients 带出人口学信息。

        设计稿 01_工作列表需要「患者标识 / 部位 / 时间 / 状态 / 剩余分钟」，
        人口学在 patients 表，必须 JOIN。返回 dict 而非 ORM 对象——
        列表行要做脱敏与派生（年龄、剩余分钟），不适合直接序列化模型。
        """
        stmt = (
            select(
                DiagnosisSession.session_id,
                DiagnosisSession.patient_id,
                DiagnosisSession.status,
                DiagnosisSession.target_organs,
                DiagnosisSession.organ_statuses,
                DiagnosisSession.created_at,
                DiagnosisSession.updated_at,
                Patient.name,
                Patient.gender,
                Patient.birth_date,
            )
            .select_from(DiagnosisSession)
            .outerjoin(Patient, Patient.patient_id == DiagnosisSession.patient_id)
            .order_by(DiagnosisSession.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(stmt)
        keys = [
            "session_id", "patient_id", "status", "target_organs",
            "organ_statuses", "created_at", "updated_at",
            "name", "gender", "birth_date",
        ]
        return [dict(zip(keys, row)) for row in result.all()]

    async def renew_session(self, session_id: str) -> DiagnosisSession | None:'''

REPO = f"{R}/app/repositories/session_repository.py"
rep_once(REPO,
         "from app.models.database import DiagnosisSession",
         "from app.models.database import DiagnosisSession, Patient",
         "repo: import Patient")
rep_once(REPO,
         "    async def renew_session(self, session_id: str) -> DiagnosisSession | None:",
         REPO_METHOD,
         "repo: list_sessions method", marker="async def list_sessions(")

# ---------- 3) session.py：GET "" 端点（必须注册在 /{session_id} 之前） ----------
API_HELPERS = '''def _mask_name(name: str | None) -> str | None:
    """姓名脱敏：姓 + **。

    列表是共享屏幕，暴露全名即泄露面。**脱敏必须在服务端做**——
    前端拿到再遮，等于把完整姓名发到了浏览器。
      张三 -> 张**   欧阳修 -> 欧**
    """
    if not name:
        return None
    return f"{name[0]}**"


def _age_from(birth_date) -> int | None:
    """从出生日期派生年龄。缺失或非法一律 None —— 不猜。"""
    if not birth_date:
        return None
    now = datetime.now(timezone.utc)
    years = now.year - birth_date.year
    if (now.month, now.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years if 0 <= years < 130 else None


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    limit: int = 20,
    session_repo: SessionRepository = Depends(get_session_repo),
    current_user: dict | None = Depends(get_current_active_user),
):
    """工作列表 —— 设计稿 01_工作列表 / 15_工作列表浅色 的数据源。

    为什么必须有：原先只有 POST /sessions（建）与 GET /sessions/{id}（单个），
    **没有列表** —— 前端工作列表无数据源，只能写死三行假数据。
    契约 P2：只分两组，不按七种状态切页。
    """
    rows = await session_repo.list_sessions(limit=limit)

    in_progress: list[SessionListItem] = []
    pending_today: list[SessionListItem] = []
    today = datetime.now(timezone.utc).date()

    for r in rows:
        item = SessionListItem(
            session_id=r["session_id"],
            patient_id=r["patient_id"],
            patient_name_masked=_mask_name(r.get("name")),
            gender=r.get("gender"),
            age=_age_from(r.get("birth_date")),
            status=SessionStatus(r["status"]),
            target_organs=r.get("target_organs") or [],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            ttl_remaining_seconds=_calc_ttl_remaining(r["updated_at"]),
        )
        if item.status in (SessionStatus.ACTIVE, SessionStatus.PARTIAL):
            in_progress.append(item)
        elif item.status in (SessionStatus.PENDING, SessionStatus.NEEDS_REVIEW):
            # 只显示今天的；昨天的待处理不该挤在今天列表里
            if item.created_at.date() == today:
                pending_today.append(item)

    return SessionListResponse(
        in_progress=in_progress,
        pending_today=pending_today,
        # 只数待处理：画布 01 的「今日 3 例待处理」指下面那组，不含进行中那一行。
        # 把进行中也计入会让医生以为还有 4 份要看。
        total_pending=len(pending_today),
    )


@router.get("/{session_id}", response_model=SessionResponse)'''

API = f"{R}/app/api/v1/session.py"
rep_once(API,
         "    SessionCreateRequest,\n    SessionResponse,",
         "    SessionCreateRequest,\n    SessionListItem,\n    SessionListResponse,\n    SessionResponse,",
         "api: import list schemas")
rep_once(API,
         '@router.get("/{session_id}", response_model=SessionResponse)',
         API_HELPERS,
         "api: GET '' endpoint", marker="async def list_sessions(")

# ---------- schemas：模块末尾追加 ----------
insert_before(f"{R}/app/models/schemas.py",
              "class SessionListItem(BaseModel):",
              SCHEMA_BLOCK,
              "schemas: SessionListItem/Response")

print("\n".join(LOG))
