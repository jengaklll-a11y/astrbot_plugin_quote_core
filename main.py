from __future__ import annotations

import time
import secrets
import random
from pathlib import Path
from typing import Dict

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

# 导入分层模块
from .model import Quote
from .dao import QuoteStore
from .renderer import QuoteRenderer

PLUGIN_NAME = "quotes"

@register("astrbot_plugin_quote_core", "jengaklll-a11y", "语录(Core)", "1.0.0")
class QuotesPlugin(Star):
    def __init__(self, context: Context, config: Dict = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = Path(self.config.get("storage") or f"data/plugin_data/{PLUGIN_NAME}")
        
        # 初始化 DAO
        self.store = QuoteStore(self.data_dir)
        self._last_sent_qid: Dict[str, str] = {}

    # ================= 指令区域 =================

    @filter.command("上传")
    async def cmd_add(self, event: AstrMessageEvent):
        """上传语录：仅支持文字"""
        text_content = event.message_str.replace("上传", "").strip()
        
        target_qq = event.get_sender_id() 
        target_name = event.get_sender_name()
        
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.At):
                target_qq = seg.qq
                target_name = f"用户{target_qq}" 

        if not text_content:
            yield event.plain_result("请提供语录文字内容。")
            return

        qid = secrets.token_hex(4)
        quote = Quote(
            id=qid,
            qq=str(target_qq),
            name=target_name,
            text=text_content,
            created_by=event.get_sender_id(),
            created_at=time.time(),
            group=str(event.get_group_id())
        )
        
        await self.store.add_quote(quote)
        yield event.plain_result(f"已收录 {target_name} 的语录 ({qid})")

    @filter.command("语录")
    async def cmd_random(self, event: AstrMessageEvent):
        """随机语录"""
        group_id = str(event.get_group_id())
        target_qq = None
        target_count = 1 
        
        # 解析参数
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.At):
                target_qq = str(seg.qq)
                break
        
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Plain):
                txt = seg.text.strip()
                if txt.isdigit():
                    target_count = int(txt)
                    break
                import re
                nums = re.findall(r"\d+", txt)
                if nums:
                    target_count = int(nums[0])
                    break
        
        target_count = min(target_count, 50)

        # 模式一：合并多条
        if target_qq and target_count > 1:
            user_quotes = self.store.get_user_quotes(group_id, target_qq)
            if not user_quotes:
                yield event.plain_result("该用户暂时没有语录哦。")
                return
            
            # 随机抽样
            if len(user_quotes) > target_count:
                selected_quotes = random.sample(user_quotes, target_count)
            else:
                selected_quotes = user_quotes
            
            try:
                # 调用 Renderer 获取 HTML 和 Options
                html_content, options = QuoteRenderer.render_merged_card(
                    selected_quotes, target_qq, selected_quotes[0].name
                )
                # 调用 AstrBot 渲染引擎
                img_url = await self.html_render(html_content, {}, options=options)
                yield event.image_result(img_url)
            except Exception as e:
                logger.error(f"渲染语录合集失败: {e}")
                yield event.plain_result(f"渲染合集失败：{e}")
            return

        # 模式二：单条随机
        quote = self.store.get_random(group_id, target_qq)
        if not quote:
            yield event.plain_result("暂时没有相关语录哦。")
            return

        self._last_sent_qid[group_id] = quote.id

        # 计算序号逻辑
        try:
            # 从 DAO 获取原始数据进行计算
            all_data = self.store.get_raw_data()
            target_user_id = str(quote.qq)
            
            user_quotes_data = [
                q for q in all_data 
                if str(q.get("group")) == group_id and str(q.get("qq")) == target_user_id
            ]
            user_quotes_data.sort(key=lambda x: x.get("created_at", 0))
            
            total_count = len(user_quotes_data)
            current_index = 0
            for idx, q_data in enumerate(user_quotes_data):
                if q_data.get("id") == quote.id:
                    current_index = idx + 1
                    break
        except Exception as e:
            logger.error(f"计算序号出错: {e}")
            total_count = 0
            current_index = 0

        # 渲染单条
        try:
            # 调用 Renderer
            html_content, options = QuoteRenderer.render_single_card(quote, current_index, total_count)
            # 调用 AstrBot
            img_url = await self.html_render(html_content, {}, options=options)
            yield event.image_result(img_url)
        except Exception as e:
            logger.error(f"渲染语录失败: {e}")
            yield event.plain_result(f"「{quote.text}」 —— {quote.name}")

    @filter.command("删除")
    async def cmd_delete(self, event: AstrMessageEvent):
        """删除刚刚发送的语录"""
        if self.config.get("admin_only", False) and not event.is_admin():
            yield event.plain_result("仅管理员可删除语录。")
            return

        group_id = str(event.get_group_id())
        qid = self._last_sent_qid.get(group_id)
        if not qid:
            yield event.plain_result("请先发送一条语录后再执行删除。")
            return

        if await self.store.delete_quote(qid):
            yield event.plain_result("删除成功。")
            self._last_sent_qid.pop(group_id, None)
        else:
            yield event.plain_result("删除失败或已被删除。")

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_poke(self, event: AstrMessageEvent):
        """戳一戳触发"""
        if not self.config.get("poke_enabled", True):
            return
        is_poke = False
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Poke):
                is_poke = True
                break
        if is_poke:
            async for res in self.cmd_random(event):
                yield res
