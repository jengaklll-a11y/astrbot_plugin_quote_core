import json
import random
import asyncio
import dataclasses
import os
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
from .model import Quote

class QuoteStore:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file = self.data_dir / "quotes.json"
        self._lock = asyncio.Lock()
        
        # 原始数据缓存
        self._cache: List[Dict[str, Any]] = self._load()
        # O(1) 快速查重索引 (格式: "{group_id}_{text}")
        self._index: Set[str] = set()
        self._rebuild_index()

    def _load(self) -> List[Dict[str, Any]]:
        if not self.file.exists():
            return []
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            return data.get("quotes", [])
        except Exception:
            return []

    def _rebuild_index(self):
        """重建查重索引"""
        self._index.clear()
        for q in self._cache:
            gid = str(q.get("group", ""))
            txt = str(q.get("text", "")).strip()
            if gid and txt:
                self._index.add(f"{gid}_{txt}")

    async def _save(self):
        """
        [安全增强] 原子写入：先写入临时文件，再重命名覆盖。
        防止写入过程中断电导致数据文件损坏。
        """
        async with self._lock:
            data = {"quotes": self._cache}
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            
            # 创建临时文件
            fd, tmp_path = tempfile.mkstemp(
                dir=self.data_dir, 
                text=True, 
                prefix="quotes_", 
                suffix=".tmp"
            )
            
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    f.write(json_str)
                
                # 原子替换 (在 POSIX 系统上是原子的，Windows 上也比直接写安全)
                tmp_path_obj = Path(tmp_path)
                tmp_path_obj.replace(self.file)
            except Exception as e:
                # 如果出错，尝试清理临时文件
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise e

    def _safe_to_quote(self, data: Dict[str, Any]) -> Quote:
        """安全转换为 Quote 对象，自动忽略多余字段"""
        valid_keys = {f.name for f in dataclasses.fields(Quote)}
        clean_data = {k: v for k, v in data.items() if k in valid_keys}
        return Quote(**clean_data)

    def check_exists(self, group_id: str, text: str) -> bool:
        """检查指定群是否已存在相同文本 (O(1) 复杂度)"""
        target_text = text.strip()
        key = f"{group_id}_{target_text}"
        return key in self._index

    async def add_quote(self, quote: Quote):
        q_dict = dataclasses.asdict(quote)
        self._cache.append(q_dict)
        
        # 同步更新索引
        key = f"{quote.group}_{quote.text.strip()}"
        self._index.add(key)
        
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
            if group_id is not None and str(q.get("group")) != str(group_id):
                continue
            candidates.append(q)
        
        if not candidates:
            return []
            
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
        # 找到要删除的项以更新索引
        to_delete = next((q for q in self._cache if q.get("id") == qid), None)
        
        if to_delete:
            self._cache = [q for q in self._cache if q.get("id") != qid]
            # 更新索引
            gid = str(to_delete.get("group", ""))
            txt = str(to_delete.get("text", "")).strip()
            key = f"{gid}_{txt}"
            if key in self._index:
                self._index.remove(key)
                
            await self._save()
            return True
            
        return False

    def get_raw_data(self) -> List[Dict[str, Any]]:
        return self._cache
