from typing import Optional
from pydantic import BaseModel, Field


class CaseRequest(BaseModel):
    a_number: str = Field(..., description="A-number with or without hyphens")
    nationality: str = Field(default="INDIA", description="Nationality to select")


class CaseResponse(BaseModel):
    a_number: Optional[str] = None
    name: Optional[str] = None
    docket_date: Optional[str] = None
    hearing_type: Optional[str] = None
    hearing_mode: Optional[str] = None
    hearing_date: Optional[str] = None
    hearing_time: Optional[str] = None
    hearing_datetime: Optional[str] = None
    hearing_line: Optional[str] = None
    judge: Optional[str] = None
    court_address: Optional[str] = None
    court_decision: Optional[str] = None
    bia_case_info: Optional[str] = None
    phone_number: Optional[str] = None
    court_contact_address: Optional[str] = None
    automated_case_information_text: Optional[str] = None


class HealthResponse(BaseModel):
    status: str
    profile_path: str
    headless: bool