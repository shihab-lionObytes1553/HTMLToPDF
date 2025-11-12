from pydantic import BaseModel, Field, field_validator
from typing import List
from helper import MAX_HTML_SIZE, MAX_BATCH_SIZE


def validate_html_size(v: str) -> str:
    """Validate HTML content size."""
    if len(v.encode('utf-8')) > MAX_HTML_SIZE:
        raise ValueError(f"HTML content exceeds maximum size of {MAX_HTML_SIZE} bytes")
    return v


class HTMLRequest(BaseModel):
    html: str = Field(..., min_length=1)
    
    @field_validator('html')
    @classmethod
    def validate_size(cls, v: str) -> str:
        return validate_html_size(v)


class BatchHTMLRequest(BaseModel):
    html_list: List[str] = Field(...)
    
    @field_validator('html_list')
    @classmethod
    def validate_batch_size(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("HTML list cannot be empty")
        if len(v) > MAX_BATCH_SIZE:
            raise ValueError(f"Batch size exceeds maximum of {MAX_BATCH_SIZE} items")
        return v
    
    @field_validator('html_list', mode='after')
    @classmethod
    def validate_html_items(cls, v: List[str]) -> List[str]:
        for i, html in enumerate(v):
            if not html or not html.strip():
                raise ValueError(f"HTML content at index {i} is empty")
        return v

