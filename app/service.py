import json

from openai import AsyncOpenAI

from app.config import Settings
from app.schemas import PhoneIntakeAssessment, PhoneIntakeRequest

SYSTEM_PROMPT = """You are a collision-repair workflow assistant for AI phone intake.
Return a conservative, structured operational output for a professional body shop.
Use only the provided context. Mark uncertain facts as To Validate.
Do not make final safety, repair, insurance, financial, or outbound communication decisions.
Keep human review in the loop for customer-facing, insurer-facing, financial, and safety-sensitive outputs.
"""


class PhoneIntakeService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key, timeout=settings.request_timeout_seconds)
            if settings.openai_api_key and AsyncOpenAI is not None
            else None
        )

    async def assess(self, payload: PhoneIntakeRequest) -> PhoneIntakeAssessment:
        if self.client is None:
            return self._rule_based_fallback(payload)

        response = await self.client.responses.parse(
            model=self.settings.openai_model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(payload.model_dump(), default=str),
            text_format=PhoneIntakeAssessment,
        )
        if response.output_parsed is None:
            raise RuntimeError("Model returned no parsed assessment")
        return response.output_parsed

    @staticmethod
    def _rule_based_fallback(payload: PhoneIntakeRequest) -> PhoneIntakeAssessment:
        fallback = {
            "urgency": "urgent",
            "drivability_caution": "Do not confirm the vehicle is safe to drive until a technician reviews the warning light and liftgate damage.",
            "missing_intake_fields": [
                        "claim number",
                        "loss date",
                        "clear damage photos"
            ],
            "next_steps": [
                        "Capture VIN and odometer photos",
                        "Schedule inspection",
                        "Request claim details from customer"
            ],
            "customer_ready_language": "Thanks for calling. We have enough to start the intake, but we still need photos and claim details before the repair plan can move forward.",
            "confidence": 0.72
}
        notes = payload.workflow_notes.lower()
        if "missing" in notes or "unknown" in notes or "to validate" in notes:
            fallback["confidence"] = min(float(fallback.get("confidence", 0.65)), 0.76)
        return PhoneIntakeAssessment.model_validate(fallback)
