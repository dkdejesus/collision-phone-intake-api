import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging
from app.repository import PhoneIntakeRepository
from app.schemas import (
    PhoneIntakeListResponse,
    PhoneIntakeRequest,
    PhoneIntakeResponse,
    StoredPhoneIntake,
    VapiWebhookPayload,
    VapiWebhookResponse,
)
from app.service import PhoneIntakeService

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("collision_phone_intake_api")
service = PhoneIntakeService(settings)
repository = PhoneIntakeRepository(settings.database_path)


@asynccontextmanager
async def lifespan(_: FastAPI):
    repository.initialize()
    logger.info("application_started")
    yield
    logger.info("application_stopped")


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def request_logging(request: Request, call_next):
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled_request_error", extra={"request_id": request_id, "path": request.url.path})
        return JSONResponse(status_code=500, content={"detail": "Internal server error", "request_id": request_id})
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["x-request-id"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "path": request.url.path,
            "duration_ms": duration_ms,
            "status_code": response.status_code,
        },
    )
    return response


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.post("/v1/phone-intakes", response_model=PhoneIntakeResponse, status_code=201)
async def create_record(payload: PhoneIntakeRequest, request: Request) -> PhoneIntakeResponse:
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    try:
        assessment = await service.assess(payload)
    except Exception as exc:
        logger.exception("assessment_failed", extra={"request_id": request_id})
        raise HTTPException(status_code=502, detail="Assessment provider failed") from exc
    model = settings.openai_model if service.client else "rule-based-fallback"
    repository.save(request_id=request_id, request=payload, assessment=assessment, model=model)
    return PhoneIntakeResponse(request_id=request_id, assessment=assessment, model=model)


@app.post("/v1/webhooks/vapi/intake-call", response_model=VapiWebhookResponse)
async def receive_vapi_intake_call(
    payload: VapiWebhookPayload,
    x_vapi_webhook_secret: str | None = Header(default=None),
) -> VapiWebhookResponse:
    if settings.vapi_webhook_secret and x_vapi_webhook_secret != settings.vapi_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid Vapi webhook secret")

    message = payload.message
    event_type = str(message.get("type") or "unknown")
    if event_type != "end-of-call-report":
        return VapiWebhookResponse(
            status="ignored",
            event_type=event_type,
            detail="Event acknowledged. Intake records are created only from end-of-call-report events.",
        )

    transcript = _extract_transcript(message)
    if not transcript:
        return VapiWebhookResponse(
            status="ignored",
            event_type=event_type,
            detail="End-of-call report did not include a transcript.",
        )

    call = _safe_dict(message.get("call"))
    artifact = _safe_dict(message.get("artifact"))
    call_id = str(call.get("id") or call.get("callId") or message.get("callId") or uuid.uuid4())
    request_id = f"vapi-{call_id}"
    intake_request = PhoneIntakeRequest(
        reference_id=request_id,
        customer_name=_extract_customer_name(message),
        vehicle=None,
        workflow_notes=_build_vapi_workflow_notes(message=message, transcript=transcript),
        source_records={
            "source": "vapi",
            "event_type": event_type,
            "call_id": call_id,
            "ended_reason": str(message.get("endedReason") or "To Validate"),
            "recording_url": _extract_recording_url(artifact),
            "caller_phone": _extract_phone_number(message),
        },
        requested_by="Vapi phone intake webhook",
    )
    try:
        assessment = await service.assess(intake_request)
    except Exception as exc:
        logger.exception("vapi_assessment_failed", extra={"request_id": request_id, "call_id": call_id})
        raise HTTPException(status_code=502, detail="Assessment provider failed") from exc

    model = settings.openai_model if service.client else "rule-based-fallback"
    repository.save(request_id=request_id, request=intake_request, assessment=assessment, model=model)
    return VapiWebhookResponse(
        status="created",
        event_type=event_type,
        request_id=request_id,
        detail="Vapi end-of-call report converted into a phone intake record.",
    )


@app.get("/v1/phone-intakes", response_model=PhoneIntakeListResponse)
async def list_records(limit: int = 20) -> PhoneIntakeListResponse:
    safe_limit = max(1, min(limit, 100))
    return PhoneIntakeListResponse(records=repository.list_recent(limit=safe_limit))


@app.get("/v1/phone-intakes/{request_id}", response_model=StoredPhoneIntake)
async def get_record(request_id: str) -> StoredPhoneIntake:
    record = repository.get(request_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Record not found")
    return record


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _extract_transcript(message: dict[str, Any]) -> str:
    artifact = _safe_dict(message.get("artifact"))
    transcript = artifact.get("transcript") or message.get("transcript")
    if isinstance(transcript, str) and transcript.strip():
        return transcript.strip()

    messages = artifact.get("messages")
    if isinstance(messages, list):
        lines = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role") or "speaker"
            content = item.get("message") or item.get("content")
            if isinstance(content, str) and content.strip():
                lines.append(f"{role}: {content.strip()}")
        return "\n".join(lines)
    return ""


def _extract_customer_name(message: dict[str, Any]) -> str | None:
    customer = _safe_dict(message.get("customer"))
    name = customer.get("name")
    return str(name) if name else None


def _extract_phone_number(message: dict[str, Any]) -> str:
    customer = _safe_dict(message.get("customer"))
    phone_number = _safe_dict(message.get("phoneNumber"))
    return str(customer.get("number") or customer.get("phoneNumber") or phone_number.get("number") or "To Validate")


def _extract_recording_url(artifact: dict[str, Any]) -> str:
    recording = _safe_dict(artifact.get("recording"))
    return str(recording.get("url") or recording.get("stereoUrl") or artifact.get("recordingUrl") or "To Validate")


def _build_vapi_workflow_notes(*, message: dict[str, Any], transcript: str) -> str:
    ended_reason = message.get("endedReason") or "To Validate"
    return (
        "Vapi phone call transcript for collision repair intake.\n\n"
        f"Ended reason: {ended_reason}\n\n"
        "Transcript:\n"
        f"{transcript}\n\n"
        "Automation instruction: Extract only facts supported by the transcript. Mark unknown customer, vehicle, "
        "claim, appointment, safety, and insurance details as To Validate. Human review is required before any "
        "customer-facing, insurer-facing, financial, repair, or drivability decision."
    )
