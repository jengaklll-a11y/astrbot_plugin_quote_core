import json
import random
import asyncio
import dataclasses
from pathlib import Path
from typing import List, Optional, Dict, Any
from .model import Quote

class QuoteStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file = self.data_dir / "quotes.json"
        self._lock = asyncio.Lock()
        self._cache = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data.get("quotes", [])
        except Exception:
            return []

    async def _save(self):
        async with self._lock:
            data = {"quotes": self._cache}
            self.file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_to_quote(self, data: Dict[str, Any]) -> Quote:
        """安全转换为 Quote 对象，自动忽略多余字段"""
        valid_keys = {f.name for f in dataclasses.fields(Quote)}
        clean_data = {k: v for k, v in data.items() if k in valid_keys}
        return Quote(**clean_data)

    async def add_quote(self, quote: Quote):
        q_dict = dataclasses.asdict(quote)
        self._cache.append(q_dict)
        await self._save()

    def get_random(self, group_id: Optional[str], qq: Optional[str]) -> Optional[Quote]:
        """获取单条随机语录"""
        candidates = []
        for q in self._cache:
            if group_id is not None and str(q.get("group")) != str(group_id):
                continue
            if qq is not None and str(q.get("qq")) != str(qq):
                continue
            candidates.append(q)
            
        if not candidates:
            return None
        return self._safe_to_quote(random.choice(candidates))
    
    def get_random_batch(self, group_id: Optional[str], count: int) -> List[Quote]:
        """获取随机语录批次 (用于抽卡)"""
        candidates = []
        for q in self._cache:
            # 同样支持 global_mode (group_id 为 None 时)
            if group_id is not None and str(q.get("group")) != str(group_id):
                continue
            candidates.append(q)
        
        if not candidates:
            return []
            
        # 如果请求数量大于库存，返回全部；否则随机抽取
        sample_size = min(len(candidates), count)
        selected = random.sample(candidates, sample_size)
        return [self._safe_to_quote(x) for x in selected]

    def get_user_quotes(self, group_id: Optional[str], qq: str) -> List[Quote]:
        """获取指定用户的所有语录"""
        res = []
        for q in self._cache:
            if group_id is not None and str(q.get("group")) != str(group_id):
                continue
            if str(q.get("qq")) != str(qq):
                continue
            res.append(self._safe_to_quote(q))
        return res

    async def delete_quote(self, qid: str) -> bool:
        initial_len = len(self._cache)
        self._cache = [q for q in self._cache if q.get("id") != qid]
        if len(self._cache) < initial_len:
            await self._save()
            return True
        return False

    def get_raw_data(self) -> List[Dict[str, Any]]:
        return self._cache
