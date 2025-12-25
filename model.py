from dataclasses import dataclass

@dataclass
class Quote:
    id: str
    qq: str
    name: str
    text: str
    created_by: str
    created_at: float
    group: str         # 群组 ID 用于隔离
