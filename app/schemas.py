from datetime import datetime

    from pydantic import BaseModel, Field


    class PhoneIntakeRequest(BaseModel):
        reference_id: str | None = Field(default=None, max_length=80)
        customer_name: str | None = Field(default=None, max_length=120)
        vehicle: str | None = Field(default=None, max_length=160)
        workflow_notes: str = Field(min_length=10, max_length=8000)
        source_records: dict[str, str] = Field(default_factory=dict)
        attachments: list[str] = Field(default_factory=list, max_length=25)
        requested_by: str | None = Field(default=None, max_length=120)


    class PhoneIntakeAssessment(BaseModel):
        urgency: str
drivability_caution: str
missing_intake_fields: list[str] = Field(default_factory=list)
next_steps: list[str] = Field(default_factory=list)
customer_ready_language: str
confidence: float = Field(ge=0, le=1)


    class PhoneIntakeResponse(BaseModel):
        request_id: str
        assessment: PhoneIntakeAssessment
        model: str


    class StoredPhoneIntake(BaseModel):
        request_id: str
        created_at: datetime
        model: str
        request: PhoneIntakeRequest
        assessment: PhoneIntakeAssessment


    class StoredPhoneIntakeSummary(BaseModel):
        request_id: str
        created_at: datetime
        model: str
        reference_id: str | None
        vehicle: str | None
        confidence: float


    class PhoneIntakeListResponse(BaseModel):
        records: list[StoredPhoneIntakeSummary]
