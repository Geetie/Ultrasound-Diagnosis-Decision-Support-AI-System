from datetime import date

import structlog
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.errors import (
    BadRequestException,
    NotFoundException,
    PatientConsentWithdrawnException,
    PatientNotConsentedException,
)
from app.config.database import get_db_session
from app.models.database import DiagnosisResult, DiagnosisSession, Patient
from app.models.schemas import ConsentStatus
from app.repositories.consent_repository import ConsentRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService

logger = structlog.get_logger()
router = APIRouter(tags=["fhir"])


class FHIRCoding(BaseModel):
    system: str
    code: str
    display: str | None = None


class FHIRCodeableConcept(BaseModel):
    coding: list[FHIRCoding]


class FHIRReference(BaseModel):
    reference: str


class FHIRPatient(BaseModel):
    resourceType: str = "Patient"
    id: str
    name: list[dict]
    active: bool = True


class FHIRImagingStudy(BaseModel):
    resourceType: str = "ImagingStudy"
    id: str | None = None
    status: str = Field(pattern=r"^(available|unavailable|unknown)$")
    subject: FHIRReference | None = None
    numberOfSeries: int = Field(ge=0)
    numberOfInstances: int = Field(ge=0)


class FHIRDiagnosticReport(BaseModel):
    resourceType: str = "DiagnosticReport"
    id: str
    status: str = Field(pattern=r"^(registered|partial|preliminary|final|amended|corrected|appended|cancelled|entered-in-error|unknown)$")
    subject: FHIRReference | None = None
    code: FHIRCodeableConcept | None = None


class FHIRObservation(BaseModel):
    resourceType: str = "Observation"
    id: str
    status: str = Field(pattern=r"^(registered|preliminary|final|amended|corrected|cancelled|entered-in-error|unknown)$")
    subject: FHIRReference | None = None
    basedOn: list[FHIRReference] | None = None


class FHIRBundle(BaseModel):
    resourceType: str = "Bundle"
    type: str = "searchset"
    total: int = Field(ge=0)
    entry: list[dict] = []


class ImagingStudyCreateRequest(BaseModel):
    session_id: str


class FHIRPatientCreate(BaseModel):
    """T-GB-API-01：Patient 资源创建（映射到本地 patients 表）。"""

    patient_id: str = Field(min_length=1, max_length=64)
    name: str | None = None
    birth_date: date | None = None
    gender: str | None = Field(default=None, pattern=r"^(male|female|other|unknown)$")


class FHIRPatientUpdate(BaseModel):
    """Patient 资源更新（仅允许可改字段）。"""

    name: str | None = None
    birth_date: date | None = None
    gender: str | None = Field(default=None, pattern=r"^(male|female|other|unknown)$")


async def _load_patient_with_consent(db: AsyncSession, patient_id: str) -> Patient:
    """读取患者并做同意校验（撤回/未签署直接拒绝）。"""
    consent_repo = ConsentRepository(db)
    consent_record = await consent_repo.get_latest_consent(patient_id)
    consent_status = ConsentStatus(consent_record.status) if consent_record else ConsentStatus.DENIED
    if consent_status == ConsentStatus.WITHDRAWN:
        raise PatientConsentWithdrawnException()
    if consent_status != ConsentStatus.GRANTED:
        raise PatientNotConsentedException()

    stmt = select(Patient).where(Patient.patient_id == patient_id)
    result = await db.execute(stmt)
    patient = result.scalar_one_or_none()
    if not patient:
        raise NotFoundException(error_message=f"Patient not found: {patient_id}")
    return patient


def _patient_resource(patient: Patient) -> dict:
    return FHIRPatient(
        id=patient.patient_id,
        name=[{"text": patient.name or patient.patient_id}],
        active=True,
    ).model_dump()


@router.get("/Patient/{id}")
async def get_patient(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    consent_repo = ConsentRepository(db)
    consent_record = await consent_repo.get_latest_consent(id)
    consent_status = ConsentStatus(consent_record.status) if consent_record else ConsentStatus.DENIED
    if consent_status == ConsentStatus.WITHDRAWN:
        raise PatientConsentWithdrawnException()
    if consent_status != ConsentStatus.GRANTED:
        raise PatientNotConsentedException()

    stmt = select(Patient).where(Patient.patient_id == id)
    result = await db.execute(stmt)
    patient = result.scalar_one_or_none()

    if not patient:
        raise NotFoundException(error_message=f"Patient not found: {id}")

    resource = FHIRPatient(
        id=patient.patient_id,
        name=[{"text": patient.patient_id}],
    )
    return resource.model_dump()


@router.post("/Patient")
async def create_patient(
    request: FHIRPatientCreate,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """创建 Patient 资源（patient_id 已存在 → 409 语义的 400）。"""
    stmt = select(Patient).where(Patient.patient_id == request.patient_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing:
        raise BadRequestException(
            error_message=f"Patient already exists: {request.patient_id}"
        )

    patient = Patient(
        patient_id=request.patient_id,
        name=request.name,
        birth_date=request.birth_date,
        gender=request.gender,
    )
    db.add(patient)
    await db.flush()

    actor = (current_user or {}).get("user_id", "system")
    await AuditService(db).log_action(
        action="fhir_patient_create",
        actor=actor,
        session_id=None,
        target_type="patient",
        target_id=request.patient_id,
        detail={"resource": "Patient"},
    )
    logger.info("fhir_patient_created", patient_id=request.patient_id)
    return _patient_resource(patient)


@router.put("/Patient/{id}")
async def update_patient(
    id: str,
    request: FHIRPatientUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """更新 Patient 资源（仅 name / birth_date / gender）。"""
    patient = await _load_patient_with_consent(db, id)

    if request.name is not None:
        patient.name = request.name
    if request.birth_date is not None:
        patient.birth_date = request.birth_date
    if request.gender is not None:
        patient.gender = request.gender
    await db.flush()

    logger.info("fhir_patient_updated", patient_id=id)
    return _patient_resource(patient)


@router.delete("/Patient/{id}")
async def delete_patient(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """删除 Patient 资源（同意校验通过后硬删除，并写审计）。"""
    patient = await _load_patient_with_consent(db, id)

    actor = (current_user or {}).get("user_id", "system")
    await db.delete(patient)
    await db.flush()

    await AuditService(db).log_action(
        action="fhir_patient_delete",
        actor=actor,
        session_id=None,
        target_type="patient",
        target_id=id,
        detail={"resource": "Patient"},
    )
    logger.info("fhir_patient_deleted", patient_id=id)
    return {"resourceType": "OperationOutcome", "status": "deleted", "id": id}


@router.post("/ImagingStudy")
async def create_imaging_study(
    request: ImagingStudyCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    session = await db.get(DiagnosisSession, request.session_id)
    if not session:
        raise NotFoundException(
            error_message=f"Session not found: {request.session_id}",
        )

    resource = FHIRImagingStudy(
        id=request.session_id,
        status="available",
        subject=FHIRReference(reference=f"Patient/{session.patient_id}"),
        numberOfSeries=1,
        numberOfInstances=0,
    )
    return resource.model_dump()


@router.get("/ImagingStudy/{id}")
async def get_imaging_study(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """读取 ImagingStudy（由会话派生：id = session_id）。"""
    session = await db.get(DiagnosisSession, id)
    if not session:
        raise NotFoundException(error_message=f"ImagingStudy not found: {id}")
    return FHIRImagingStudy(
        id=session.session_id,
        status="available",
        subject=FHIRReference(reference=f"Patient/{session.patient_id}"),
        numberOfSeries=1,
        numberOfInstances=0,
    ).model_dump()


@router.get("/ImagingStudy")
async def search_imaging_studies(
    subject: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """按 subject（``Patient/{id}``）检索 ImagingStudy。"""
    if not subject:
        return FHIRBundle(total=0, entry=[]).model_dump()
    patient_id = subject.split("/")[-1]
    stmt = (
        select(DiagnosisSession)
        .where(DiagnosisSession.patient_id == patient_id)
        .order_by(DiagnosisSession.created_at.desc())
        .limit(50)
    )
    sessions = list((await db.execute(stmt)).scalars().all())
    entries = [
        {
            "resource": FHIRImagingStudy(
                id=s.session_id,
                status="available",
                subject=FHIRReference(reference=f"Patient/{s.patient_id}"),
                numberOfSeries=1,
                numberOfInstances=0,
            ).model_dump()
        }
        for s in sessions
    ]
    return FHIRBundle(total=len(entries), entry=entries).model_dump()


@router.get("/DiagnosticReport/{id}")
async def get_diagnostic_report(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    stmt = select(DiagnosisResult).where(DiagnosisResult.request_id == id)
    result = await db.execute(stmt)
    diagnosis = result.scalar_one_or_none()

    if not diagnosis:
        raise NotFoundException(error_message=f"DiagnosticReport not found: {id}")

    session = await db.get(DiagnosisSession, diagnosis.session_id)
    patient_id = session.patient_id if session else "unknown"

    resource = FHIRDiagnosticReport(
        id=id,
        status="final" if diagnosis.status == "completed" else "registered",
        subject=FHIRReference(reference=f"Patient/{patient_id}"),
        code=FHIRCodeableConcept(
            coding=[FHIRCoding(
                system="http://loinc.org",
                code="18782-3",
                display="Radiology Study observation",
            )]
        ),
    )
    return resource.model_dump()


@router.get("/DiagnosticReport")
async def search_diagnostic_reports(
    subject: str | None = Query(None),
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """按 subject（``Patient/{id}``）检索 DiagnosticReport（由诊断结果派生）。"""
    if not subject:
        return FHIRBundle(total=0, entry=[]).model_dump()
    patient_id = subject.split("/")[-1]
    stmt = (
        select(DiagnosisSession)
        .where(DiagnosisSession.patient_id == patient_id)
        .order_by(DiagnosisSession.created_at.desc())
        .limit(50)
    )
    sessions = list((await db.execute(stmt)).scalars().all())
    if not sessions:
        return FHIRBundle(total=0, entry=[]).model_dump()

    session_ids = [s.session_id for s in sessions]
    result_stmt = (
        select(DiagnosisResult)
        .where(DiagnosisResult.session_id.in_(session_ids))
        .order_by(DiagnosisResult.created_at.desc())
        .limit(100)
    )
    results = list((await db.execute(result_stmt)).scalars().all())
    entries = [
        {
            "resource": FHIRDiagnosticReport(
                id=r.request_id,
                status="final" if r.status == "completed" else "registered",
                subject=FHIRReference(reference=f"Patient/{patient_id}"),
                code=FHIRCodeableConcept(
                    coding=[
                        FHIRCoding(
                            system="http://loinc.org",
                            code="18782-3",
                            display="Radiology Study observation",
                        )
                    ]
                ),
            ).model_dump()
        }
        for r in results
    ]
    return FHIRBundle(total=len(entries), entry=entries).model_dump()


@router.get("/Observation/{id}")
async def get_observation(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    """读取 Observation（由诊断结果派生：id = ``obs-{request_id}``）。"""
    request_id = id[4:] if id.startswith("obs-") else id
    stmt = select(DiagnosisResult).where(DiagnosisResult.request_id == request_id)
    diagnosis = (await db.execute(stmt)).scalar_one_or_none()
    if not diagnosis:
        raise NotFoundException(error_message=f"Observation not found: {id}")

    session = await db.get(DiagnosisSession, diagnosis.session_id)
    patient_id = session.patient_id if session else "unknown"
    return FHIRObservation(
        id=f"obs-{request_id}",
        status="final" if diagnosis.status == "completed" else "registered",
        subject=FHIRReference(reference=f"Patient/{patient_id}"),
        basedOn=[FHIRReference(reference=f"DiagnosticReport/{request_id}")],
    ).model_dump()


@router.get("/Observation")
async def list_observations(
    based_on: str | None = Query(None, alias="based-on"),
    db: AsyncSession = Depends(get_db_session),
    current_user: dict | None = Depends(get_current_active_user),
):
    if not based_on:
        return FHIRBundle(total=0, entry=[]).model_dump()

    stmt = select(DiagnosisResult).where(DiagnosisResult.request_id == based_on)
    result = await db.execute(stmt)
    diagnosis = result.scalar_one_or_none()

    if not diagnosis:
        return FHIRBundle(total=0, entry=[]).model_dump()

    session = await db.get(DiagnosisSession, diagnosis.session_id)
    patient_id = session.patient_id if session else "unknown"

    obs = FHIRObservation(
        id=f"obs-{based_on}",
        status="final" if diagnosis.status == "completed" else "registered",
        subject=FHIRReference(reference=f"Patient/{patient_id}"),
        basedOn=[FHIRReference(reference=f"DiagnosticReport/{based_on}")],
    )
    return FHIRBundle(
        total=1,
        entry=[{"resource": obs.model_dump()}],
    ).model_dump()
