"""
SolarSync - AI Bill Reading & Qualification Pipeline orchestrated with LangGraph and Google Gemini Vision.
Extracts consumer number, tariff, sanctioned load, monthly units, and calculates lead qualification.
"""

import os
import json
from typing import TypedDict, Optional, Dict, Any, List
from google import genai
from google.genai import types
from langgraph.graph import StateGraph, START, END

from app.config import get_settings

settings = get_settings()


class BillProcessingState(TypedDict):
    """State schema passed through the LangGraph pipeline."""
    lead_id: str
    file_path: str
    mime_type: str
    file_bytes: Optional[bytes]
    
    # Customer context for scoring
    ownership: str
    roof_area_sqft: float
    form_monthly_bill: int

    # AI Extraction outputs
    discom_name: str
    consumer_number: Optional[str]
    consumer_name: Optional[str]
    tariff_category: Optional[str]
    sanctioned_load_kw: Optional[float]
    monthly_units: Optional[int]
    bill_amount_rs: Optional[float]
    
    # Quality assessment
    photo_quality: str           # "good", "blurry", "unreadable", "invalid"
    quality_issues: Optional[str]
    needs_reupload: bool
    ai_confidence: float

    # Lead qualification outputs
    lead_score: str              # "high", "medium", "low"
    score_reason: str
    recommended_kw: Optional[float]

    raw_response: Optional[str]
    errors: List[str]


def get_gemini_client() -> genai.Client:
    """Initialize Google GenAI client."""
    return genai.Client(api_key=settings.GEMINI_API_KEY)


# --- LangGraph Nodes ---

def load_document_node(state: BillProcessingState) -> Dict[str, Any]:
    """Node 1: Validate file path and read document bytes."""
    path = state["file_path"]
    errors = list(state.get("errors", []))

    if not os.path.isabs(path):
        # Resolve relative to project root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        full_path = os.path.join(base_dir, path)
    else:
        full_path = path

    if not os.path.exists(full_path):
        return {
            "file_bytes": None,
            "errors": errors + [f"Bill file not found at {full_path}"],
            "photo_quality": "unreadable",
            "needs_reupload": True,
            "quality_issues": "File is missing or could not be loaded.",
        }

    ext = os.path.splitext(full_path)[1].lower()
    mime = "application/pdf" if ext == ".pdf" else "image/jpeg"
    if ext == ".png":
        mime = "image/png"
    elif ext == ".webp":
        mime = "image/webp"

    try:
        with open(full_path, "rb") as f:
            data = f.read()
        return {
            "file_bytes": data,
            "mime_type": mime,
        }
    except Exception as e:
        return {
            "file_bytes": None,
            "errors": errors + [f"Failed to read file: {str(e)}"],
            "photo_quality": "unreadable",
            "needs_reupload": True,
            "quality_issues": f"File read error: {str(e)}",
        }


def gemini_vision_extraction_node(state: BillProcessingState) -> Dict[str, Any]:
    """Node 2: Send bill document to Gemini 2.5 Flash for structured OCR extraction."""
    if not state.get("file_bytes") or state.get("errors"):
        return {}

    client = get_gemini_client()
    prompt = """
You are an expert OCR system specializing in Indian electricity bills, specifically MSEDCL (Maharashtra State Electricity Distribution Co. Ltd / Mahavitaran) and Vidarbha region utilities.

Carefully inspect the provided electricity bill photo/document and extract the key details in strict JSON format.

JSON Schema to return:
{
  "discom_name": "MSEDCL (Mahavitaran) or utility name",
  "consumer_number": "12-digit consumer / account number or null if unreadable",
  "consumer_name": "Applicant name as printed on bill or null",
  "tariff_category": "e.g. LT-I Domestic / Residential, LT-II Commercial, etc.",
  "sanctioned_load_kw": 4.0 (numeric in kW, convert HP to kW if needed: 1 HP = 0.746 kW, or null),
  "monthly_units": 340 (numeric integer: current units billed or average monthly kWh consumed, or null),
  "bill_amount_rs": 3210.0 (numeric: total current bill amount payable in INR, or null),
  "photo_quality": "good" | "blurry" | "unreadable" | "invalid",
  "quality_issues": "Explanation if photo is blurry, cropped, dark, or not an electricity bill, otherwise null",
  "needs_reupload": false (set to true ONLY if image is so blurry, cropped or damaged that key values cannot be determined),
  "ai_confidence": 0.95 (float from 0.0 to 1.0 representing extraction certainty)
}

Important Instructions:
1. If the image is NOT an electricity bill, set photo_quality to 'invalid', needs_reupload to true, and quality_issues to 'Document is not an electricity bill'.
2. If text is blurry or cut off, extract whatever is visible, lower ai_confidence, and explain in quality_issues.
3. Return ONLY valid JSON without Markdown backticks or commentary.
"""

    try:
        part = types.Part.from_bytes(
            data=state["file_bytes"],
            mime_type=state["mime_type"],
        )

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[part, prompt],
        )

        raw_text = response.text.strip()
        # Clean potential markdown fences
        if raw_text.startswith("```json"):
            raw_text = raw_text[7:]
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

        data = json.loads(raw_text)

        return {
            "discom_name": data.get("discom_name", "MSEDCL (Mahavitaran)"),
            "consumer_number": str(data["consumer_number"]) if data.get("consumer_number") else None,
            "consumer_name": data.get("consumer_name"),
            "tariff_category": data.get("tariff_category", "LT-I Residential"),
            "sanctioned_load_kw": float(data["sanctioned_load_kw"]) if data.get("sanctioned_load_kw") is not None else None,
            "monthly_units": int(data["monthly_units"]) if data.get("monthly_units") is not None else None,
            "bill_amount_rs": float(data["bill_amount_rs"]) if data.get("bill_amount_rs") is not None else None,
            "photo_quality": data.get("photo_quality", "good"),
            "quality_issues": data.get("quality_issues"),
            "needs_reupload": bool(data.get("needs_reupload", False)),
            "ai_confidence": float(data.get("ai_confidence", 0.85)),
            "raw_response": raw_text,
        }

    except Exception as e:
        # Fallback heuristic if AI call fails or format parsing error
        errors = list(state.get("errors", []))
        errors.append(f"AI Vision extraction error: {str(e)}")
        return {
            "photo_quality": "unreadable",
            "quality_issues": f"OCR processing failed: {str(e)}",
            "needs_reupload": True,
            "ai_confidence": 0.0,
            "errors": errors,
            "raw_response": str(e),
        }


def qualification_scoring_node(state: BillProcessingState) -> Dict[str, Any]:
    """
    Node 3: Deterministic Lead Qualification & System Sizing.
    Follows explicit rules from Company A Brief:
    - Central residential scheme generation factor: 1,400 units per kW per year.
    - Tenant -> Low Score (replaces survey with short call).
    - Bill < ₹800 or units < 100 -> Low Score (small bill, long payback).
    - Monthly units >= 250 and Owner -> High Score (survey worthy).
    - Moderate bills -> Medium Score (call first).
    """
    ownership = state.get("ownership", "owner").lower()
    roof_area = state.get("roof_area_sqft", 400.0)
    units = state.get("monthly_units")
    bill_rs = state.get("bill_amount_rs") or state.get("form_monthly_bill", 2500)
    sanctioned_load = state.get("sanctioned_load_kw")

    # If units wasn't extracted from bill, estimate from bill amount: ~₹8 per unit
    effective_units = units if units else int(bill_rs / 8.0)

    # System sizing formula:
    # 1 kW generates ~1,400 units/year (~116 units/month).
    # Sizing by consumption: effective_units / 116.
    # Sizing by roof: 100 sq ft per kW.
    # Cap at sanctioned load if known, minimum 1 kW.
    size_by_consumption = round(effective_units / 116.0)
    size_by_roof = int(roof_area / 100.0)

    recommended = max(1, min(size_by_consumption, size_by_roof))
    if sanctioned_load and sanctioned_load > 0:
        recommended = min(recommended, int(sanctioned_load))
    recommended_kw = float(max(1, recommended))

    # Qualification scoring logic
    if ownership == "tenant":
        score = "low"
        score_reason = "Low (tenant) — Tenant cannot install rooftop solar without landlord consent. Replace survey visit with short call."
        recommended_kw = None
    elif bill_rs < 800 or effective_units < 100:
        score = "low"
        score_reason = f"Low (small bill) — {effective_units} units/month (₹{int(bill_rs)} bill) has poor payback economics. Replace survey with short call."
        recommended_kw = 1.0
    elif effective_units >= 250 and ownership == "owner":
        score = "high"
        score_reason = f"High priority — Genuine homeowner with {effective_units} monthly units (₹{int(bill_rs)} bill) and adequate roof ({int(roof_area)} sq ft). Priority survey visit recommended."
    else:
        score = "medium"
        score_reason = f"Medium priority — Moderate consumption ({effective_units} units/mo). Conduct short qualification phone call before booking surveyor visit."

    return {
        "lead_score": score,
        "score_reason": score_reason,
        "recommended_kw": recommended_kw,
    }


# --- Build LangGraph Pipeline ---

def build_bill_pipeline():
    """Construct and compile the LangGraph bill reading workflow."""
    workflow = StateGraph(BillProcessingState)

    workflow.add_node("load_document", load_document_node)
    workflow.add_node("gemini_vision", gemini_vision_extraction_node)
    workflow.add_node("qualification_scoring", qualification_scoring_node)

    workflow.add_edge(START, "load_document")
    workflow.add_edge("load_document", "gemini_vision")
    workflow.add_edge("gemini_vision", "qualification_scoring")
    workflow.add_edge("qualification_scoring", END)

    return workflow.compile()


# Singleton compiled graph instance
bill_reading_graph = build_bill_pipeline()


async def process_bill_reading(
    lead_id: str,
    file_path: str,
    ownership: str,
    roof_area_sqft: float,
    form_monthly_bill: int,
) -> Dict[str, Any]:
    """Execute the full LangGraph bill reading pipeline."""
    initial_state: BillProcessingState = {
        "lead_id": lead_id,
        "file_path": file_path,
        "mime_type": "image/jpeg",
        "file_bytes": None,
        "ownership": ownership,
        "roof_area_sqft": roof_area_sqft,
        "form_monthly_bill": form_monthly_bill,
        "discom_name": "MSEDCL (Mahavitaran)",
        "consumer_number": None,
        "consumer_name": None,
        "tariff_category": None,
        "sanctioned_load_kw": None,
        "monthly_units": None,
        "bill_amount_rs": None,
        "photo_quality": "good",
        "quality_issues": None,
        "needs_reupload": False,
        "ai_confidence": 0.0,
        "lead_score": "pending",
        "score_reason": "",
        "recommended_kw": None,
        "raw_response": None,
        "errors": [],
    }

    result = bill_reading_graph.invoke(initial_state)
    return result
