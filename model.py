from dataclasses import dataclass
from typing import Optional

@dataclass
class Quote:
    id: str
    qq: str
    name: str
    text: str
    created_by: str
    created_at: float
    group: str         # 群组 ID 用于隔离
    ai_reason: Optional[str] = None # AI 推荐理由 (可选)
