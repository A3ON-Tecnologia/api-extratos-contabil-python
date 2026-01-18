"""Schemas Pydantic para validação de dados."""

from .llm_response import LLMExtractionResult
from .api import ProcessingResult, UploadResponse, ProcessingStatus
from .client import ClientInfo, MatchResult, MatchMethod

__all__ = [
    "LLMExtractionResult",
    "ProcessingResult",
    "UploadResponse",
    "ProcessingStatus",
    "ClientInfo",
    "MatchResult",
    "MatchMethod",
]
