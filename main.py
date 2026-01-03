from __future__ import annotations

import time
import secrets
import random
import re
import asyncio
import json
import ast
from pathlib import Path
from typing import Dict, Optional, Any, List, Union

# AstrBot Imports
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api.star import StarTools
from astrbot.api import logger
import astrbot.api.message_components as Comp

# Local Imports
from .model import Quote
from .dao import QuoteStore
from .renderer import QuoteRenderer

PLUGIN_NAME = "astrbot_plugin_quote_core"

@register(PLUGIN_NAME, "jengaklll-a11y", "支持多群隔离/混合、HTML卡片渲染和长图生成、Ai一键捕捉上传", "2.0.7")
class QuotesPlugin(Star):
    def __init__(self, context: Context, config: Dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 获取标准数据目录
        self.data_dir = Path(StarTools.get_data_dir(PLUGIN_NAME))
        self.store = QuoteStore(self.data_dir)
        
        self._last_sent_qid: Dict[str, str] = {}
        self._poke_cooldowns: Dict[str, float] = {}

        # [新增] 自动检测本地 logo.png 并注入到渲染器
        curr_dir = Path(__file__).parent
        # 尝试检测插件根目录或 assets 目录下的 logo.png
        possible_paths = [curr_dir / "logo.png", curr_dir / "assets" / "logo.png"]
        for p in possible_paths:
            if p.exists():
                # 使用 as_uri() 自动处理 Windows/Linux 路径差异，生成 file:/// 链接
                QuoteRenderer.DEFAULT_AVATAR_URI = p.as_uri()
                logger.info(f"QuoteCore: 已加载本地默认头像: {p.name}")
                break

        # 正则路由
        self.regex_routes = [
            (re.compile(r"^上传\(|^添加语录\)"), self._logic_add),
            (re.compile(r"^(语录|随机语录|抽卡)([\s\d].*)?$"), self._logic_random),
            (re.compile(r"^删除\(|^删除语录\)"), self._logic_delete),
            (re.compile(r"^一键金句\(|^智能收录\)"), self._logic_ai_analysis)
        ]

    # ================= 1. 指令注册 =================
    
    @filter.command("上传", aliases=["添加语录"])
    async def cmd_add(self, event: AstrMessageEvent):
        """回复消息进行收录"""
        async for res in self._logic_add(event):
            yield res

    @filter.command("语录", aliases=["随机语录", "抽卡"])
    async def cmd_random(self, event: AstrMessageEvent):
        """随机/抽卡/合集"""
        async for res in self._logic_random(event):
            yield res

    @filter.command("删除", aliases=["删除语录"])
    async def cmd_delete(self, event: AstrMessageEvent):
        """删除上一条"""
        async for res in self._logic_delete(event):
            yield res

    @filter.command("一键金句", aliases=["智能收录"])
    async def cmd_ai_add(self, event: AstrMessageEvent):
        """[AI] 拉取历史消息并挖掘金句"""
        async for res in self._logic_ai_analysis(event):
            yield res

    # ================= 2. 辅助监听 =================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _handle_aux_events(self, event: AstrMessageEvent):
        self_id = self._get_self_id(event)
        if event.get_sender_id() == self_id:
            return

        is_poke = False
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Poke):
                is_poke = True
                break
        
        if is_poke:
            async for res in self._logic_poke(event):
                yield res
            return

        if not self.config.get("ignore_prefix", False):
            return

        raw_text = "".join([s.text for s in event.message_obj.message if isinstance(s, Comp.Plain)]).strip()
        if not raw_text:
            return

        for pattern, logic_func in self.regex_routes:
            if pattern.match(raw_text) and not raw_text.startswith(("/", "!", "！")):
                async for res in logic_func(event):
                    yield res

    # ================= 3. 核心业务逻辑 =================

    async def _logic_add(self, event: AstrMessageEvent):
        """逻辑：手动上传"""
        if event.get_platform_name() != "aiocqhttp":
            yield event.plain_result("⚠️ 当前平台不支持获取历史消息原文，无法使用引用收录功能。")
            return

        reply_msg_id = self._get_reply_message_id(event)
        if not reply_msg_id:
            yield event.plain_result("请回复某条消息发送 /上传 以收录语录。")
            return
        
        ret = await self._fetch_onebot_msg(event, reply_msg_id)
        target_text = self._extract_plaintext_from_onebot_message(ret.get("message"))
        sender = ret.get("sender") or {}
        origin_time = ret.get("time") 
        
        if target_text and sender:
            res = await self._save_quote_core(event, target_text, sender, str(event.get_group_id()), origin_time)
            if res == "IS_BOT":
                yield event.plain_result("⚠️ 无法收录：不可以收录机器人发送的消息哦。")
            elif res == "DUPLICATE":
                yield event.plain_result("⚠️ 收录取消：该语录已存在库中。")
            elif res:
                yield event.plain_result(f"已收录 {res.name} 的语录")
            else:
                yield event.plain_result("收录失败：未知错误。")
        else:
            yield event.plain_result("收录失败：无法获取内容或发送者信息。")

    async def _logic_ai_analysis(self, event: AstrMessageEvent):
        """逻辑：AI 分析"""
        if event.get_platform_name() != "aiocqhttp":
            yield event.plain_result("⚠️ 智能挖掘功能依赖 OneBot 协议的历史消息接口，当前平台暂不支持。")
            return

        provider = self._resolve_provider(event)
        if not provider:
            yield event.plain_result("❌ 错误：未配置 LLM 服务，无法进行智能分析。")
            return
        
        model_name = getattr(provider, "id", None) or type(provider).__name__
        
        group_id = str(event.get_group_id())
        max_history = max(50, self.config.get("max_history_count", 200))
        yield event.plain_result(f"[{model_name}] 正在深挖最近 {max_history} 条消息...")
        
        history_msgs = await self._fetch_history_robust_main(event, group_id, max_history)
        if len(history_msgs) < 5:
            yield event.plain_result("❌ 拉取到的历史消息过少，无法分析。")
            return

        context_str, valid_msgs_map = self._prepare_context(event, history_msgs, group_id)
        if not context_str:
            yield event.plain_result("最近的消息要么是机器人发的，要么被黑名单拦截，要么已经被收录过啦！")
            return

        max_quotes = max(1, self.config.get("max_golden_quotes", 1))
        prompt = self._build_prompt(context_str, max_quotes)
        
        try:
            resp = await provider.text_chat(prompt, session_id=None)
            data_list = self._parse_llm_json(resp)
        except Exception as e:
            logger.error(f"AI Call Error: {e}")
            yield event.plain_result(f"⚠️ 分析失败：{str(e)}")
            return

        if not data_list:
            yield event.plain_result("🤔 AI 似乎没有找到任何值得收录的内容。")
            return

        saved_quotes = await self._process_ai_results(event, data_list, valid_msgs_map, group_id)
        
        if not saved_quotes:
            yield event.plain_result("🤔 AI 推荐了一些内容，但它们要么是重复的，要么我没在记录里找到原文。")
        else:
            yield event.plain_result(f"🎉 成功挖掘 {len(saved_quotes)} 条金句！正在生成语录卡片...")
            bot_qq = self._get_self_id(event) or "10000"
            html, opts = QuoteRenderer.render_merged_card(saved_quotes, bot_qq, "智能金句挖掘", True)
            img = await self.html_render(html, {}, options=opts)
            yield event.image_result(img)

    def _resolve_provider(self, event):
        cfg_provider_id = self.config.get("llm_provider_id")
        provider = None
        if cfg_provider_id:
            provider = self._force_find_provider(cfg_provider_id)
        if not provider:
            provider = self.context.get_using_provider(event.unified_msg_origin)
        return provider

    def _prepare_context(self, event, history_msgs, group_id):
        self_id = self._get_self_id(event)
        blacklist = self.config.get("user_blacklist", []) or []
        msgs_text = []
        valid_msgs_map = {}

        for m in history_msgs:
            sender = m.get("sender", {})
            sender_id = str(sender.get("user_id", ""))
            
            if self_id and sender_id == self_id: continue
            if sender_id in blacklist: continue

            raw_msg = m.get("message", [])
            text = self._extract_plaintext_from_onebot_message(raw_msg)
            if not text or len(text) < 2: continue
            
            if self.store.check_exists(group_id, text): continue

            name = sender.get("card") or sender.get("nickname") or "未知"
            valid_msgs_map[text] = m
            msgs_text.append(f"[{name}]: {text}")
            
        return "\n".join(msgs_text), valid_msgs_map

    def _build_prompt(self, context_str, max_quotes):
        return (
            f"请作为一名眼光极高的“金句鉴赏家”，从以下群聊记录中挑选出 **{max_quotes}** 句最具备“金句”潜质的发言。\n\n"
            "## 判定标准（宁缺毋滥）：\n"
            "1. **核心标准**：**极为精彩的发言**。必须具备颠覆常识的脑洞、逻辑跳脱的表达、强烈反差感或独特的抽象思维。\n"
            "2. **拒绝平庸**：**绝对不要选**普通的日常对话、单纯的玩梗复读、水群废话。\n\n"
            "## 聊天记录：\n"
            f"{context_str}\n\n"
            "## 返回格式：\n"
            "请仅返回一个纯 JSON **数组**（Array），不要包含 Markdown 标记。\n"
            "[\n"
            "  {\n"
            "    \"content\": \"金句原文(如果没有满意的请填 NULL)\",\n"
            "    \"reason\": \"入选理由\"\n"
            "  }\n"
            "]"
        )

    def _parse_llm_json(self, resp) -> List[Dict]:
        if not resp or not hasattr(resp, "completion_text") or not resp.completion_text:
            return []
        
        llm_text = resp.completion_text.strip()
        json_match = re.search(r"(\[.*\])", llm_text, re.DOTALL)
        json_str = json_match.group(1) if json_match else llm_text.replace("```json", "").replace("```", "").strip()
        
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            try:
                return ast.literal_eval(json_str)
            except Exception:
                logger.error(f"JSON Parse Failed. Raw: {llm_text}")
                return []

    async def _process_ai_results(self, event, data_list, valid_msgs_map, group_id) -> List[Quote]:
        saved_quotes = []
        if isinstance(data_list, dict): data_list = [data_list]
        
        for item in data_list:
            if not isinstance(item, dict): continue
            
            content = str(item.get("content", "")).strip()
            reason = str(item.get("reason", ""))
            
            if not content or content.upper() in ["NULL", "无"]: continue

            matched_msg = None
            if content in valid_msgs_map:
                matched_msg = valid_msgs_map[content]
            else:
                for k, v in valid_msgs_map.items():
                    if content in k or k in content:
                        matched_msg = v
                        content = k 
                        break
            
            if matched_msg:
                sender = matched_msg.get("sender", {})
                origin_time = matched_msg.get("time")
                res = await self._save_quote_core(event, content, sender, group_id, origin_time)
                
                if isinstance(res, Quote):
                    res.ai_reason = reason
                    saved_quotes.append(res)
                    logger.info(f"挖掘成功: {content} (理由: {reason})")
                    
        return saved_quotes

    async def _fetch_next_batch_robust(self, client, group_id, cursor_seq, error_strike_ref):
        batch_size = 100 
        MAX_RETRY_STRIKE = 15 
        
        if error_strike_ref[0] > MAX_RETRY_STRIKE:
            return [], 0, False 

        try:
            payload = {"group_id": int(group_id), "count": batch_size, "reverseOrder": True}
            if cursor_seq > 0: payload["message_seq"] = cursor_seq

            res = await client.api.call_action("get_group_msg_history", **payload)
            if not res or not isinstance(res, dict): return [], 0, False
            
            batch = res.get("messages", [])
            if not batch: return [], 0, True 
            
            oldest_msg = batch[0]
            next_cursor = int(oldest_msg.get("message_seq") or oldest_msg.get("message_id") or 0)
            
            if error_strike_ref[0] > 0: error_strike_ref[0] = 0
            return batch, next_cursor, True

        except Exception as e:
            if "1200" in str(e) or "不存在" in str(e):
                error_strike_ref[0] += 1
                jump_step = 50 * (2 ** (min(error_strike_ref[0], 8) - 1))
                new_cursor = cursor_seq - jump_step
                return [], new_cursor, False 
            return [], 0, False

    async def _fetch_history_robust_main(self, event, group_id, total_count) -> List[Dict]:
        client = event.bot
        collected_messages = []
        cursor_seq = 0
        error_strike = [0] 
        max_loops = int(total_count / 50) + 20 
        
        for _ in range(max_loops):
            if len(collected_messages) >= total_count: break
            
            batch, next_cursor, success = await self._fetch_next_batch_robust(
                client, group_id, cursor_seq, error_strike
            )
            
            if not success:
                if next_cursor <= 0: break
                cursor_seq = next_cursor
                await asyncio.sleep(0.1)
                continue
            
            if not batch: break
            collected_messages.extend(batch)
            cursor_seq = next_cursor
            await asyncio.sleep(0.2)
        
        unique_msgs = {str(m.get("message_id")): m for m in collected_messages}.values()
        sorted_msgs = sorted(unique_msgs, key=lambda x: x.get("time", 0))
        return sorted_msgs[-total_count:]

    def _force_find_provider(self, target_id: str):
        if not target_id: return None
        target_id_lower = target_id.lower()
        all_providers = []
        if hasattr(self.context, "get_all_providers"):
            all_providers = self.context.get_all_providers()
        
        for p in all_providers:
            ids = []
            if hasattr(p, "id"): ids.append(str(p.id))
            if hasattr(p, "provider_id"): ids.append(str(p.provider_id))
            for pid in ids:
                if pid.lower() == target_id_lower:
                    return p
        return None

    async def _save_quote_core(self, event, text, sender_info, group_id, origin_time=None):
        target_qq = str(sender_info.get("user_id") or sender_info.get("qq") or "")
        target_name = (sender_info.get("card") or sender_info.get("nickname") or target_qq).strip()
        clean_text = text.strip()
        
        if not clean_text or not target_qq: return None
        
        self_id = self._get_self_id(event)
        if self_id and target_qq == self_id: return "IS_BOT"
        if self.store.check_exists(group_id, clean_text): return "DUPLICATE"
        
        created_at_ts = float(origin_time) if origin_time else time.time()
        qid = secrets.token_hex(4)
        quote = Quote(
            id=qid, qq=str(target_qq), name=str(target_name), 
            text=clean_text, created_by=event.get_sender_id(),
            created_at=created_at_ts, group=str(group_id)
        )
        await self.store.add_quote(quote)
        return quote

    async def _logic_random(self, event: AstrMessageEvent):
        current_group_id = str(event.get_group_id())
        is_global = self.config.get("global_mode", False)
        search_group_id = None if is_global else current_group_id
        max_limit = self.config.get("max_batch_count", 10)
        
        target_qq = None
        target_count = 1 
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.At):
                target_qq = str(seg.qq)
                break
        
        if not target_qq and "自己" in event.message_str:
            target_qq = str(event.get_sender_id())
            
        raw_text = "".join([s.text for s in event.message_obj.message if isinstance(s, Comp.Plain)])
        nums = re.findall(r"\d+", raw_text)
        if nums and int(nums[0]) > 0:
            target_count = min(int(nums[0]), max_limit)
        
        if not target_qq and target_count > 1:
            random_quotes = self.store.get_random_batch(search_group_id, target_count)
            if not random_quotes:
                yield event.plain_result("语录不足。")
                return
            
            refresh_tasks = [self._refresh_quote_name(event, current_group_id, q) for q in random_quotes]
            if refresh_tasks: await asyncio.gather(*refresh_tasks)
            
            bot_qq = self._get_self_id(event) or "10000"
            html, opts = QuoteRenderer.render_merged_card(random_quotes, bot_qq, "随机语录抽卡", True)
            img = await self.html_render(html, {}, options=opts)
            yield event.image_result(img)
            return

        if target_qq and target_count > 1:
            user_quotes = self.store.get_user_quotes(search_group_id, target_qq)
            if not user_quotes:
                yield event.plain_result("该用户暂无语录。")
                return
            
            sel = random.sample(user_quotes, min(len(user_quotes), target_count))
            lname = await self._get_current_name(event, current_group_id, target_qq)
            dname = lname if lname else sel[0].name
            if lname: 
                for q in sel: q.name = lname
            html, opts = QuoteRenderer.render_merged_card(sel, target_qq, dname, False)
            img = await self.html_render(html, {}, options=opts)
            yield event.image_result(img)
            return

        quote = self.store.get_random(search_group_id, target_qq)
        if not quote:
            yield event.plain_result("暂无语录。")
            return
        
        self._last_sent_qid[current_group_id] = quote.id
        await self._refresh_quote_name(event, current_group_id, quote)
        
        all_data = self.store.get_raw_data()
        subset = [q for q in all_data if (str(q.get("group"))==current_group_id or is_global) and str(q.get("qq"))==str(quote.qq)]
        idx = next((i+1 for i,q in enumerate(subset) if q.get("id")==quote.id), 0)
        
        html, opts = QuoteRenderer.render_single_card(quote, idx, len(subset))
        img = await self.html_render(html, {}, options=opts)
        yield event.image_result(img)

    async def _logic_delete(self, event: AstrMessageEvent):
        if self.config.get("admin_only", False) and not event.is_admin():
            yield event.plain_result("仅管理员可删除。")
            return
        
        group_id = str(event.get_group_id())
        qid = self._last_sent_qid.get(group_id)
        if not qid:
            yield event.plain_result("请先发送一条语录。")
            return
            
        if await self.store.delete_quote(qid):
            yield event.plain_result("删除成功。")
            self._last_sent_qid.pop(group_id, None)
        else:
            yield event.plain_result("删除失败。")

    async def _logic_poke(self, event: AstrMessageEvent):
        mode_str = self.config.get("poke_mode", "仅戳Bot")
        if mode_str == "关闭": return
            
        cooldown = self.config.get("poke_cooldown", 10)
        group_id = str(event.get_group_id())
        now = time.time()
        
        if now - self._poke_cooldowns.get(group_id, 0) < cooldown: return
            
        is_trigger = False
        poke_target = None
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Poke):
                poke_target = str(getattr(seg, "qq", "") or getattr(seg, "target", "") or "")
                break
        
        if mode_str == "任意戳": is_trigger = True
        elif str(poke_target) == str(self._get_self_id(event)): is_trigger = True
            
        if is_trigger:
            self._poke_cooldowns[group_id] = now
            async for res in self._logic_random(event): yield res
    
    async def _refresh_quote_name(self, event, group_id, quote):
        try:
            n = await self._get_current_name(event, group_id, quote.qq)
            if n: quote.name = n
        except: pass

    def _get_self_id(self, event) -> Optional[str]:
        if hasattr(event.message_obj, "self_id") and event.message_obj.self_id:
            return str(event.message_obj.self_id)
        return str(event.raw_event.get("self_id", "")) if hasattr(event, "raw_event") else None

    async def _get_current_name(self, event, group_id, user_id):
        if event.get_platform_name() != "aiocqhttp": return ""
        try:
            client = event.bot
            if group_id:
                ret = await client.api.call_action("get_group_member_info", group_id=int(group_id), user_id=int(user_id), no_cache=True)
                if ret: return (ret.get("card") or ret.get("nickname") or "").strip()
        except: pass
        return ""

    def _get_reply_message_id(self, event) -> Optional[str]:
        for seg in event.get_messages(): 
            if isinstance(seg, Comp.Reply):
                return str(getattr(seg, "id", None) or getattr(seg, "msgId", None))
        return None

    async def _fetch_onebot_msg(self, event, mid) -> Dict:
        if event.get_platform_name() != "aiocqhttp": return {}
        try:
            return await event.bot.api.call_action("get_msg", message_id=int(str(mid))) or {}
        except: return {}

    def _extract_plaintext_from_onebot_message(self, message) -> Optional[str]:
        try:
            if isinstance(message, list):
                return "".join([str(m.get("data",{}).get("text","")) for m in message if m.get("type") in ("text","plain")]).strip() or None
        except: pass
        return None
