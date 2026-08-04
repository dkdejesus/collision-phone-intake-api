import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.schemas import PhoneIntakeAssessment, PhoneIntakeRequest, StoredPhoneIntake, StoredPhoneIntakeSummary


class PhoneIntakeRepository:
    def __init__(self, database_path: str):
        self.database_path = database_path
        self._initialized = False

    def initialize(self) -> None:
        if self._initialized:
            return
        path = Path(self.database_path)
        if path.parent != Path("."):
            path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS phone_intakes (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    assessment_json TEXT NOT NULL
                )
                """
            )
        self._initialized = True

    def save(
        self,
        *,
        request_id: str,
        request: PhoneIntakeRequest,
        assessment: PhoneIntakeAssessment,
        model: str,
    ) -> StoredPhoneIntake:
        self.initialize()
        created_at = datetime.now(UTC)
        record = StoredPhoneIntake(
            request_id=request_id,
            created_at=created_at,
            model=model,
            request=request,
            assessment=assessment,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO phone_intakes (request_id, created_at, model, request_json, assessment_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record.request_id,
                    record.created_at.isoformat(),
                    record.model,
                    record.request.model_dump_json(),
                    record.assessment.model_dump_json(),
                ),
            )
        return record

    def get(self, request_id: str) -> StoredPhoneIntake | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT request_id, created_at, model, request_json, assessment_json
                FROM phone_intakes
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredPhoneIntake(
            request_id=row["request_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            model=row["model"],
            request=PhoneIntakeRequest.model_validate(json.loads(row["request_json"])),
            assessment=PhoneIntakeAssessment.model_validate(json.loads(row["assessment_json"])),
        )

    def list_recent(self, limit: int = 20) -> list[StoredPhoneIntakeSummary]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT request_id, created_at, model, request_json, assessment_json
                FROM phone_intakes
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        summaries = []
        for row in rows:
            request = PhoneIntakeRequest.model_validate(json.loads(row["request_json"]))
            assessment = PhoneIntakeAssessment.model_validate(json.loads(row["assessment_json"]))
            summaries.append(
                StoredPhoneIntakeSummary(
                    request_id=row["request_id"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    model=row["model"],
                    reference_id=request.reference_id,
                    vehicle=request.vehicle,
                    confidence=assessment.confidence,
                )
            )
        return summaries

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection
