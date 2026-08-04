import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.logging_config import configure_logging
from app.repository import PhoneIntakeRepository
from app.schemas import PhoneIntakeListResponse, PhoneIntakeRequest, PhoneIntakeResponse, StoredPhoneIntake
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
