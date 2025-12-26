from __future__ import annotations

import time
import secrets
import random
import re
import asyncio
from pathlib import Path
from typing import Dict, Optional, Any, List

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

# 导入分层模块
from .model import Quote
from .dao import QuoteStore
from .renderer import QuoteRenderer

PLUGIN_NAME = "quotes"

@register("astrbot_plugin_quote_core", "jengaklll-a11y", "语录(Core)", "1.0.0", "支持多群隔离、HTML卡片渲染和长图生成的语录插件")
class QuotesPlugin(Star):
    def __init__(self, context: Context, config: Dict = None):
        super().__init__(context)
        self.config = config or {}
        self.data_dir = Path(f"data/plugin_data/{PLUGIN_NAME}")
        self.store = QuoteStore(self.data_dir)
        self._last_sent_qid: Dict[str, str] = {}
        self._poke_cooldowns: Dict[str, float] = {}

    # ================= 1. 显式指令注册 (UI显示用) =================
    
    @filter.command("上传", aliases=["添加语录"])
    async def cmd_add(self, event: AstrMessageEvent):
        """上传语录 (支持回复消息)"""
        async for res in self._logic_add(event):
            yield res

    @filter.command("语录", aliases=["随机语录", "抽卡"])
    async def cmd_random(self, event: AstrMessageEvent):
        """随机语录/抽卡/合集"""
        async for res in self._logic_random(event):
            yield res

    @filter.command("删除", aliases=["删除语录"])
    async def cmd_delete(self, event: AstrMessageEvent):
        """删除上一条语录 (仅管理员)"""
        async for res in self._logic_delete(event):
            yield res

    # ================= 2. 辅助监听 (实现无前缀/无视At位置) =================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _handle_aux_events(self, event: AstrMessageEvent):
        """
        辅助监听器：
        1. 处理“无视前缀”的指令 (如配置开启)
        2. 处理“戳一戳”事件
        """
        # 忽略 Bot 自己的消息
        if event.get_sender_id() == self._get_self_id(event):
            return

        # --- A. 戳一戳检测 ---
        is_poke = False
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Poke):
                is_poke = True
                break
        
        if is_poke:
            async for res in self._logic_poke(event):
                yield res
            return

        # --- B. 无前缀指令检测 ---
        # 如果未开启忽略前缀，直接返回 (标准指令由上面的 @filter.command 处理)
        if not self.config.get("ignore_prefix", False):
            return

        # 提取纯文本 (剔除 At、图片等，实现无视 At 位置)
        raw_text = ""
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Plain):
                raw_text += seg.text
        clean_text = raw_text.strip()
        
        if not clean_text:
            return

        # 防止双重触发：如果消息以 "/" 开头 (假设标准前缀是 /)，则认为已被标准指令捕获
        # 注意：这里假设 Bot 前缀包含 /。如果您的 Bot 设置了其他前缀，这里可能需要调整。
        # 为了更稳妥，我们只处理“完全不带前缀”的关键词。
        
        # 简单的路由映射
        # 正则含义：以关键词开头，后面紧跟 (空格+任意内容) 或 (数字+任意内容) 或 (结束)
        route_map = {
            r"^上传$|^添加语录$": self._logic_add,
            r"^(语录|随机语录|抽卡)([\s\d].*)?$": self._logic_random,
            r"^删除$|^删除语录$": self._logic_delete
        }

        matched_logic = None
        for pattern, logic_func in route_map.items():
            # 如果匹配成功，且字符串开头不是常见指令前缀 (避免和标准指令冲突)
            if re.match(pattern, clean_text) and not clean_text.startswith(("/", "!", "！")):
                matched_logic = logic_func
                break
        
        if matched_logic:
            async for res in matched_logic(event):
                yield res


    # ================= 3. 核心业务逻辑 (复用) =================

    async def _logic_add(self, event: AstrMessageEvent):
        """逻辑：上传"""
        reply_msg_id = self._get_reply_message_id(event)
        if not reply_msg_id:
            yield event.plain_result("请回复某条消息发送 /上传 以收录语录。")
            return
        
        ret = await self._fetch_onebot_msg(event, reply_msg_id)
        if not ret:
            yield event.plain_result("获取原始消息失败，可能是消息太久远或 Bot 无法读取。")
            return

        target_text = self._extract_plaintext_from_onebot_message(ret.get("message"))
        sender = ret.get("sender") or {}
        target_qq = str(sender.get("user_id") or sender.get("qq") or "") or None
        card = (sender.get("card") or "").strip()
        nickname = (sender.get("nickname") or "").strip()
        target_name = card or nickname or target_qq

        # --- 修改点：文案已更新 ---
        if not target_text:
            yield event.plain_result("收录失败：无法提取非文本内容。")
            return
        if not target_qq:
            yield event.plain_result("收录失败：无法获取发送者信息。")
            return

        target_text = target_text.strip()
        qid = secrets.token_hex(4)
        quote = Quote(
            id=qid,
            qq=str(target_qq),
            name=str(target_name), 
            text=target_text,
            created_by=event.get_sender_id(),
            created_at=time.time(),
            group=str(event.get_group_id())
        )
        
        await self.store.add_quote(quote)
        yield event.plain_result(f"已收录 {target_name} 的语录 ({qid})")

    async def _logic_random(self, event: AstrMessageEvent):
        """逻辑：随机/抽卡"""
        current_group_id = str(event.get_group_id())
        is_global = self.config.get("global_mode", False)
        search_group_id = None if is_global else current_group_id
        max_limit = self.config.get("max_batch_count", 10)
        
        target_qq = None
        target_count = 1 
        
        # 解析 At 和 数字 (无论它们在文本的哪里)
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.At):
                target_qq = str(seg.qq)
                break
        
        if not target_qq and "自己" in event.message_str:
             target_qq = str(event.get_sender_id())

        # 提取纯文本中的数字
        raw_text = ""
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Plain):
                raw_text += seg.text
        
        nums = re.findall(r"\d+", raw_text)
        if nums:
            val = int(nums[0])
            if val > 0:
                target_count = min(val, max_limit)
        
        # --- 场景1: 随机抽卡 ---
        if not target_qq and target_count > 1:
            random_quotes = self.store.get_random_batch(search_group_id, target_count)
            if not random_quotes:
                scope_tips = "库中" if is_global else "本群"
                yield event.plain_result(f"{scope_tips}语录太少啦，不够抽卡哦。")
                return
            
            refresh_tasks = [self._refresh_quote_name(event, current_group_id, q) for q in random_quotes]
            if refresh_tasks:
                await asyncio.gather(*refresh_tasks)

            bot_qq = self._get_self_id(event) or "10000"
            title = "随机语录抽卡"
            try:
                html_content, options = QuoteRenderer.render_merged_card(
                    random_quotes, bot_qq, title, show_author=True
                )
                img_url = await self.html_render(html_content, {}, options=options)
                yield event.image_result(img_url)
            except Exception as e:
                logger.error(f"渲染抽卡失败: {e}")
                yield event.plain_result(f"渲染失败：{e}")
            return

        # --- 场景2: 个人合集 ---
        if target_qq and target_count > 1:
            user_quotes = self.store.get_user_quotes(search_group_id, target_qq)
            if not user_quotes:
                scope_tips = "库中" if is_global else "本群"
                yield event.plain_result(f"该用户在{scope_tips}暂时没有语录哦。")
                return
            
            selected_quotes = random.sample(user_quotes, min(len(user_quotes), target_count))
            latest_name = await self._get_current_name(event, current_group_id, target_qq)
            display_name = latest_name if latest_name else selected_quotes[0].name
            if latest_name:
                for q in selected_quotes: q.name = latest_name

            try:
                html_content, options = QuoteRenderer.render_merged_card(
                    selected_quotes, target_qq, display_name, show_author=False
                )
                img_url = await self.html_render(html_content, {}, options=options)
                yield event.image_result(img_url)
            except Exception as e:
                yield event.plain_result(f"渲染失败：{e}")
            return

        # --- 场景3: 单条随机 ---
        quote = self.store.get_random(search_group_id, target_qq)
        if not quote:
            scope_tips = "库中" if is_global else "本群"
            if target_qq and target_qq == str(event.get_sender_id()):
                yield event.plain_result(f"你在{scope_tips}暂时还没有语录哦。")
            else:
                yield event.plain_result(f"{scope_tips}暂时没有相关语录哦。")
            return

        self._last_sent_qid[current_group_id] = quote.id
        
        try:
            all_data = self.store.get_raw_data()
            target_user_id = str(quote.qq)
            if is_global:
                user_quotes_data = [q for q in all_data if str(q.get("qq")) == target_user_id]
            else:
                user_quotes_data = [q for q in all_data if str(q.get("group")) == current_group_id and str(q.get("qq")) == target_user_id]
            user_quotes_data.sort(key=lambda x: x.get("created_at", 0))
            total_count = len(user_quotes_data)
            current_index = next((i + 1 for i, q in enumerate(user_quotes_data) if q.get("id") == quote.id), 0)
        except Exception:
            total_count = 0; current_index = 0

        await self._refresh_quote_name(event, current_group_id, quote)

        try:
            html_content, options = QuoteRenderer.render_single_card(quote, current_index, total_count)
            img_url = await self.html_render(html_content, {}, options=options)
            yield event.image_result(img_url)
        except Exception as e:
            yield event.plain_result(f"「{quote.text}」 —— {quote.name}")

    async def _logic_delete(self, event: AstrMessageEvent):
        """逻辑：删除"""
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

    async def _logic_poke(self, event: AstrMessageEvent):
        """逻辑：戳一戳"""
        mode_str = self.config.get("poke_mode", "仅戳Bot")
        cooldown = self.config.get("poke_cooldown", 10)

        if mode_str == "关闭": return
        mode = 2 if mode_str == "任意戳" else 1

        poke_seg = None
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Poke):
                poke_seg = seg; break
        if not poke_seg: return

        group_id = str(event.get_group_id())
        last_time = self._poke_cooldowns.get(group_id, 0)
        now = time.time()

        should_trigger = False
        if mode == 2:
            should_trigger = True
        elif mode == 1:
            self_id = self._get_self_id(event)
            target_id = self._extract_poke_target(poke_seg)
            if self_id and target_id and str(self_id) == str(target_id):
                should_trigger = True
        
        if should_trigger:
            if now - last_time < cooldown:
                yield event.plain_result("歇一会儿再戳吧~")
                return
            self._poke_cooldowns[group_id] = now
            # 戳一戳触发随机语录 (复用 _logic_random)
            # 注意：这里需要传入 event，_logic_random 会解析文本找数字/At。
            # 戳一戳事件通常没有文本，所以正好符合“无参随机”的逻辑。
            async for res in self._logic_random(event):
                yield res

    # ================= 4. 工具方法 =================
    
    async def _refresh_quote_name(self, event: AstrMessageEvent, group_id: str, quote: Quote):
        try:
            latest_name = await self._get_current_name(event, group_id, quote.qq)
            if latest_name: quote.name = latest_name
        except Exception: pass

    def _get_self_id(self, event: AstrMessageEvent) -> Optional[str]:
        if hasattr(event.message_obj, "self_id") and event.message_obj.self_id:
            return str(event.message_obj.self_id)
        if hasattr(event, "raw_event") and isinstance(event.raw_event, dict):
             return str(event.raw_event.get("self_id", ""))
        return None

    def _extract_poke_target(self, seg: Any) -> Optional[str]:
        for attr in ["qq", "target", "id", "uin", "user_id"]:
            val = getattr(seg, attr, None)
            if val: return str(val)
        return None

    async def _get_current_name(self, event: AstrMessageEvent, group_id: str, user_id: str) -> str:
        if event.get_platform_name() != "aiocqhttp": return ""
        client = event.bot
        try:
            if group_id and group_id != "None":
                ret = await client.api.call_action("get_group_member_info", group_id=int(group_id), user_id=int(user_id), no_cache=True)
                if ret: return (ret.get("card") or ret.get("nickname") or "").strip()
            ret = await client.api.call_action("get_stranger_info", user_id=int(user_id), no_cache=True)
            if ret: return (ret.get("nickname") or "").strip()
        except Exception: pass
        return ""

    def _get_reply_message_id(self, event: AstrMessageEvent) -> Optional[str]:
        try:
            for seg in event.get_messages(): 
                if isinstance(seg, Comp.Reply):
                    mid = (getattr(seg, "message_id", None) or getattr(seg, "id", None) or getattr(seg, "reply", None) or getattr(seg, "msgId", None))
                    if mid: return str(mid)
        except Exception as e:
            logger.warning(f"解析 Reply 段失败: {e}")
        return None

    async def _fetch_onebot_msg(self, event: AstrMessageEvent, message_id: str) -> Dict[str, Any]:
        if event.get_platform_name() != "aiocqhttp": return {}
        try:
            return await event.bot.api.call_action("get_msg", message_id=int(str(message_id))) or {}
        except Exception: return {}

    def _extract_plaintext_from_onebot_message(self, message) -> Optional[str]:
        try:
            if isinstance(message, list):
                parts = []
                for m in message:
                    if m.get("type") in ("text", "plain"):
                        parts.append(str((m.get("data") or {}).get("text") or ""))
                return "".join(parts).strip() or None
        except Exception: pass
        return None
