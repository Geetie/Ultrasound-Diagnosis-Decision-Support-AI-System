from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List
from pydantic import BaseModel, Field
import structlog

from app.config.database import get_db_session
from app.security.auth import get_current_active_user
from app.api.v1.errors import NotFoundException, BadRequestException, PatientNotConsentedException, PatientConsentWithdrawnException
from app.models.database import Patient, DiagnosisSession, DiagnosisResult
from app.repositories.consent_repository import ConsentRepository
from app.models.schemas import ConsentStatus
from sqlalchemy import select

logger = structlog.get_logger()
router = APIRouter(tags=["fhir"])


class FHIRCoding(BaseModel):
    system: str
    code: str
    display: Optional[str] = None


class FHIRCodeableConcept(BaseModel):
    coding: List[FHIRCoding]


class FHIRReference(BaseModel):
    reference: str


class FHIRPatient(BaseModel):
    resourceType: str = "Patient"
    id: str
    name: List[dict]
    active: bool = True


class FHIRImagingStudy(BaseModel):
    resourceType: str = "ImagingStudy"
    id: Optional[str] = None
    status: str = Field(pattern=r"^(available|unavailable|unknown)$")
    subject: Optional[FHIRReference] = None
    numberOfSeries: int = Field(ge=0)
    numberOfInstances: int = Field(ge=0)


class FHIRDiagnosticReport(BaseModel):
    resourceType: str = "DiagnosticReport"
    id: str
    status: str = Field(pattern=r"^(registered|partial|preliminary|final|amended|corrected|appended|cancelled|entered-in-error|unknown)$")
    subject: Optional[FHIRReference] = None
    code: Optional[FHIRCodeableConcept] = None


class FHIRObservation(BaseModel):
    resourceType: str = "Observation"
    id: str
    status: str = Field(pattern=r"^(registered|preliminary|final|amended|corrected|cancelled|entered-in-error|unknown)$")
    subject: Optional[FHIRReference] = None
    basedOn: Optional[List[FHIRReference]] = None


class FHIRBundle(BaseModel):
    resourceType: str = "Bundle"
    type: str = "searchset"
    total: int = Field(ge=0)
    entry: List[dict] = []


class ImagingStudyCreateRequest(BaseModel):
    session_id: str


@router.get("/Patient/{id}")
async def get_patient(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: Optional[dict] = Depends(get_current_active_user),
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


@router.post("/ImagingStudy")
async def create_imaging_study(
    request: ImagingStudyCreateRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: Optional[dict] = Depends(get_current_active_user),
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


@router.get("/DiagnosticReport/{id}")
async def get_diagnostic_report(
    id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: Optional[dict] = Depends(get_current_active_user),
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


@router.get("/Observation")
async def list_observations(
    based_on: Optional[str] = Query(None, alias="based-on"),
    db: AsyncSession = Depends(get_db_session),
    current_user: Optional[dict] = Depends(get_current_active_user),
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
