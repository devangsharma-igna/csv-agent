from typing import Any, Optional
from pydantic import BaseModel


class TableConfirmRequest(BaseModel):
    table_name: str


class QueryRequest(BaseModel):
    user_query: str
    response_format: str = "NL"  # "NL" | "Figures" | "NL + Figures"


class QueryResponse(BaseModel):
    intent: str = ""
    sql: str = ""
    rows: Optional[list[dict[str, Any]]] = None
    nl_answer: Optional[str] = None
    figures: Optional[list[str]] = None  # list of Plotly JSON strings
    error: Optional[str] = None
    out_of_scope: bool = False
    table_gone: bool = False


class TableDeleteRequest(BaseModel):
    table_name: str


class UploadOptions(BaseModel):
    table_name: str
    if_exists: str = "fail"   # "fail" | "replace" | "append"
    sanitize: bool = False
    primary_key: Optional[str] = None
    remove_dups: bool = False
