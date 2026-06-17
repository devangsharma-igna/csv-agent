from __future__ import annotations

import json
import logging
from typing import Any

from ..config import settings
from ..logging_utils import trunc
from ..db_client import MCPToolError, mcp
from .base import (
    TableExistenceGate,
    load_prompt,
    single_shot_json,
)

log = logging.getLogger("igna.agent.sql_agent")
