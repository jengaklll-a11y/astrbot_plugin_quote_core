import json
import asyncio
import secrets
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import asdict
from .model import Quote

class QuoteStore:
    """数据访问层：负责 Quote 数据的持久化和读取"""
    def __init__(self, data_dir: Path):
        self.root = data_dir
        self.file = self.root / "quotes.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._quotes: List[Dict] = self._load()

    def _load(self) -> List[Dict]:
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data.get("quotes", [])
        except Exception:
            return []

    async def save(self):
        async with self._lock:
            self.file.write_text(
                json.dumps({"quotes": self._quotes}, ensure_ascii=False, indent=2), 
                encoding="utf-8"
            )

    async def add_quote(self, quote: Quote):
        self._quotes.append(asdict(quote))
        await self.save()

    async def delete_quote(self, qid: str) -> bool:
        original_len = len(self._quotes)
        self._quotes = [q for q in self._quotes if q["id"] != qid]
        if len(self._quotes) != original_len:
            await self.save()
            return True
        return False

    def _dict_to_quote(self, data: Dict) -> Quote:
        return Quote(
            id=data.get("id"),
            qq=str(data.get("qq")),
            name=data.get("name", "Unknown"),
            text=data.get("text", ""),
            created_by=data.get("created_by", ""),
            created_at=data.get("created_at", 0.0),
            group=str(data.get("group"))
        )

    def get_random(self, group_id: str, target_qq: str = None) -> Optional[Quote]:
        candidates = [q for q in self._quotes if str(q.get("group")) == str(group_id)]
        if target_qq:
            candidates = [q for q in candidates if str(q.get("qq")) == str(target_qq)]
        if not candidates:
            return None
        return self._dict_to_quote(secrets.choice(candidates))
    
    def get_user_quotes(self, group_id: str, target_qq: str) -> List[Quote]:
        """获取指定用户在指定群的所有语录"""
        candidates = [
            self._dict_to_quote(q) for q in self._quotes 
            if str(q.get("group")) == str(group_id) and str(q.get("qq")) == str(target_qq)
        ]
        candidates.sort(key=lambda x: x.created_at)
        return candidates
        
    def get_raw_data(self) -> List[Dict]:
        """获取原始数据列表，用于逻辑层计算排名等"""
        return self._quotes
