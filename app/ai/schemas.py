from pydantic import BaseModel

class PredictionResult(BaseModel):
    """
    Schema chuan cho moi ket qua tra ve tu cac AI Services.
    - label: Nhan du doan duoc (VD: "BEAUTY", "FASHION", "UNKNOWN")
    - confidence: Do tu tin cua mo hinh (0.0 -> 1.0)
    - engine: Ten dong co xu ly (VD: "ML", "LLM", "FALLBACK")
    - needs_review: Co can con nguoi kiem tra lai khong (True/False)
    """
    label: str
    confidence: float
    engine: str
    needs_review: bool
