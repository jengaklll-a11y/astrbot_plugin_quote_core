from __future__ import annotations

import time
import secrets
import random
import re
import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, Any, List, Union

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp

# 导入分层模块
from .model import Quote
from .dao import QuoteStore
from .renderer import QuoteRenderer

PLUGIN_NAME = "astrbot_plugin_quote_core"

@register(PLUGIN_NAME, "jengaklll-a11y", "支持多群隔离/混合、HTML卡片渲染和长图生成、Ai一键捕捉上传", "2.0.1")
class QuotesPlugin(Star):
    def __init__(self, context: Context, config: Dict = None):
        super().__init__(context)
        self.config = config or {}
        
        self.data_dir = Path(f"data/plugin_data/{PLUGIN_NAME}")
        self.store = QuoteStore(self.data_dir)
        
        self._last_sent_qid: Dict[str, str] = {}
        self._poke_cooldowns: Dict[str, float] = {}

        # 正则路由
        self.regex_routes = [
            (re.compile(r"^上传$|^添加语录$"), self._logic_add),
            (re.compile(r"^(语录|随机语录|抽卡)([\s\d].*)?$"), self._logic_random),
            (re.compile(r"^删除$|^删除语录$"), self._logic_delete),
            (re.compile(r"^一键金句$|^智能收录$"), self._logic_ai_analysis)
        ]

    # ================= 1. 指令注册 =================
    
    @filter.command("上传", aliases=["添加语录"])
    async def cmd_add(self, event: AstrMessageEvent):
        """回复消息进行收录"""
        async for res in self._logic_add(event): yield res

    @filter.command("语录", aliases=["随机语录", "抽卡"])
    async def cmd_random(self, event: AstrMessageEvent):
        """随机/抽卡/合集"""
        async for res in self._logic_random(event): yield res

    @filter.command("删除", aliases=["删除语录"])
    async def cmd_delete(self, event: AstrMessageEvent):
        """删除上一条"""
        async for res in self._logic_delete(event): yield res

    @filter.command("一键金句", aliases=["智能收录"])
    async def cmd_ai_add(self, event: AstrMessageEvent):
        """[AI] 拉取历史消息并挖掘金句"""
        async for res in self._logic_ai_analysis(event): yield res

    # ================= 2. 辅助监听 =================

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _handle_aux_events(self, event: AstrMessageEvent):
        self_id = self._get_self_id(event)
        if event.get_sender_id() == self_id: return

        is_poke = False
        for seg in event.message_obj.message:
            if isinstance(seg, Comp.Poke):
                is_poke = True; break
        
        if is_poke:
            async for res in self._logic_poke(event): yield res
            return

        if not self.config.get("ignore_prefix", False): return

        raw_text = "".join([s.text for s in event.message_obj.message if isinstance(s, Comp.Plain)]).strip()
        if not raw_text: return

        for pattern, logic_func in self.regex_routes:
            if pattern.match(raw_text) and not raw_text.startswith(("/", "!", "！")):
                async for res in logic_func(event): yield res

    # ================= 3. 核心业务逻辑 =================

    async def _logic_add(self, event: AstrMessageEvent):
        """逻辑：手动上传"""
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
        group_id = str(event.get_group_id())
        self_id = self._get_self_id(event)
        
        # 获取黑名单
        blacklist = self.config.get("user_blacklist", [])
        if not isinstance(blacklist, list): blacklist = []
        
        # 1. 确定使用的 Provider
        provider = None
        cfg_provider_id = self.config.get("llm_provider_id")
        
        if cfg_provider_id:
            provider = self._force_find_provider(cfg_provider_id)
            if not provider:
                logger.warning(f"指定模型 '{cfg_provider_id}' 未能通过深度查找匹配，回退使用默认模型。")

        if not provider:
            provider = self.context.get_using_provider(event.unified_msg_origin)
        
        if not provider:
            yield event.plain_result("❌ 错误：未配置 LLM 服务，无法进行智能分析。")
            return
        
        model_name = getattr(provider, "id", None) or type(provider).__name__

        # 2. 主动拉取历史记录
        max_history = max(50, self.config.get("max_history_count", 200))
        max_quotes = max(1, self.config.get("max_golden_quotes", 1)) 
        
        yield event.plain_result(f"[{model_name}] 正在深挖最近 {max_history} 条消息...")
        
        # [NEW] 使用移植自画像插件的新抓取逻辑
        history_msgs = await self._fetch_history_robust_main(event, group_id, max_history)
        
        if len(history_msgs) < 5:
            yield event.plain_result("❌ 拉取到的历史消息过少，无法分析。")
            return

        # 3. 构造 Context
        msgs_text = []
        valid_msgs_map = {} 

        for m in history_msgs:
            sender = m.get("sender", {})
            sender_id = str(sender.get("user_id", ""))
            
            # 过滤机器人自己
            if self_id and sender_id == self_id: continue
            
            # 过滤黑名单用户
            if sender_id in blacklist: continue

            raw_msg = m.get("message", [])
            text = self._extract_plaintext_from_onebot_message(raw_msg)
            if not text or len(text) < 2: continue
            
            if self.store.check_exists(group_id, text): continue

            name = sender.get("card") or sender.get("nickname") or "未知"
            # 存入 map，key 为文本，value 为完整消息对象（包含 time）
            valid_msgs_map[text] = m
            msgs_text.append(f"[{name}]: {text}")
        
        if not msgs_text:
            yield event.plain_result("最近的消息要么是机器人发的，要么被黑名单拦截，要么已经被收录过啦！")
            return

        context_str = "\n".join(msgs_text)
        
        # 4. 获取 Prompt (已进行安全化处理)
        default_prompt_lines = [
            "请作为一名眼光极高的“金句鉴赏家”，从以下群聊记录中挑选出 **{max_golden_quotes}** 句最具备“金句”潜质的发言。",
            "",
            "## 判定标准（宁缺毋滥）：",
            "1. **核心标准**：**极为精彩的发言**。必须具备颠覆常识的脑洞、逻辑跳脱的表达、强烈反差感或独特的抽象思维。",
            "2. **典型特征**：包含争议话题元素、夸张类比、反常规结论、一本正经的「胡说八道」或突破语境的清奇思路。",
            "3. **收录偏好**：优先选择那些**令人意想不到的神回复**、**强烈的情绪宣泄**（如极度的愤怒或兴奋）、或者**充满哲理的荒谬言论**。",
            "4. **拒绝平庸**：**绝对不要选**普通的日常对话、单纯的玩梗复读、水群废话（如“早安”、“哈哈哈”）。",
            "",
            "## 聊天记录：",
            "{context}",
            "",
            "## 返回格式：",
            "请仅返回一个纯 JSON **数组**（Array），不要包含 Markdown 标记。",
            "**重要：**如果聊天记录中没有符合标准的金句，该项的 content 请填 \"NULL\"。",
            "[",
            "  {{", 
            "    \"content\": \"金句原文(如果没有满意的请填 NULL)\",",
            "    \"reason\": \"入选理由\"",
            "  }}", 
            "]"
        ]
        prompt_tmpl = "\n".join(default_prompt_lines)

        try:
            prompt = prompt_tmpl.format(context=context_str, max_golden_quotes=max_quotes)
        except Exception as e:
            logger.error(f"Prompt formatting failed: {e}")
            yield event.plain_result(f"❌ 提示词构建错误: {e}")
            return

        # 5. 调用 LLM
        try:
            resp = await provider.text_chat(prompt, session_id=None)
            
            # [Fix] 增加对 None 或空对象的防御性检查
            if not resp or not hasattr(resp, "completion_text") or not resp.completion_text:
                yield event.plain_result("⚠️ AI 似乎拒绝了请求（可能是触发了安全过滤器），建议更换模型或重试。")
                return

            llm_text = resp.completion_text.strip()
            
            if llm_text.startswith("```json"): llm_text = llm_text[7:]
            if llm_text.endswith("```"): llm_text = llm_text[:-3]
            
            try:
                raw_data = json.loads(llm_text.strip())
            except json.JSONDecodeError:
                yield event.plain_result("⚠️ AI 返回了无效的 JSON 格式，无法解析。")
                return
            
            data_list = []
            if isinstance(raw_data, list):
                data_list = raw_data
            elif isinstance(raw_data, dict):
                data_list = [raw_data]
            
            if not data_list:
                yield event.plain_result("🤔 AI 似乎没有找到任何值得收录的内容。")
                return

            saved_quotes: List[Quote] = []
            
            for item in data_list:
                content = item.get("content", "").strip()
                reason = item.get("reason", "")
                
                if not content or content.upper() == "NULL" or content == "无":
                    continue

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
                        logger.info(f"挖掘成功: {content} (理由: {reason}, Time: {origin_time})")
                    elif res == "DUPLICATE":
                         pass

            # 6. 结果展示
            if not saved_quotes:
                if any(x.get("content", "").upper() != "NULL" for x in data_list):
                    yield event.plain_result("🤔 AI 推荐了一些内容，但它们要么是重复的，要么我没在记录里找到原文。")
                else:
                    yield event.plain_result("🤔 AI 翻阅了聊天记录，觉得最近大家聊得比较平淡，没有发现值得收录的金句。")
            else:
                yield event.plain_result(f"🎉 成功挖掘 {len(saved_quotes)} 条金句！正在生成语录卡片...")
                
                # --- 修改部分：统一使用 render_merged_card ---
                bot_qq = self._get_self_id(event) or "10000"
                # 即使只有1条，也使用 "智能金句挖掘" 这个标题的合集模板
                html, opts = QuoteRenderer.render_merged_card(saved_quotes, bot_qq, "智能金句挖掘", True)
                img = await self.html_render(html, {}, options=opts)
                yield event.image_result(img)
                # ----------------------------------------

        except Exception as e:
            # 捕获 Provider 抛出的异常
            err_str = str(e)
            if "ChatCompletion" in err_str and "content=None" in err_str:
                 yield event.plain_result("🚫 挖掘失败：AI 拒绝生成内容。这通常是因为聊天记录中包含触发 Gemini 安全过滤器的敏感词。")
            else:
                logger.error(f"AI Analysis Error: {e}")
                yield event.plain_result(f"分析失败：{err_str}")

    # ================= 4. 历史消息抓取 (移植版) =================

    async def _fetch_next_batch_robust(self, client, group_id, cursor_seq, error_strike_ref):
        """[底层] 获取单批次消息 (防1200错误 + 指数跳跃 + 动态Batch + 熔断机制)"""
        batch_size = 100 # 固定单次拉取数量
        MAX_RETRY_STRIKE = 15 
        
        # 熔断检查
        if error_strike_ref[0] > MAX_RETRY_STRIKE:
            logger.error(f"QuoteCore: 连续失败次数过多 ({error_strike_ref[0]}次)，触发熔断停止回溯。")
            return [], 0, False 

        try:
            payload = {
                "group_id": int(group_id),
                "count": batch_size,
                "reverseOrder": True # 关键：倒序拉取
            }
            if cursor_seq > 0:
                payload["message_seq"] = cursor_seq

            res = await client.api.call_action("get_group_msg_history", **payload)
            
            if not res or not isinstance(res, dict): return [], 0, False
            batch = res.get("messages", [])
            if not batch: return [], 0, True 
            
            # 获取当前批次最老的消息ID，作为下次的游标
            oldest_msg = batch[0]
            next_cursor = int(oldest_msg.get("message_seq") or oldest_msg.get("message_id") or 0)
            
            if error_strike_ref[0] > 0:
                error_strike_ref[0] = 0
                
            return batch, next_cursor, True

        except Exception as e:
            err_msg = str(e)
            
            if "1200" in err_msg or "不存在" in err_msg:
                error_strike_ref[0] += 1
                current_strike = error_strike_ref[0]
                
                base_jump = 50 
                # 指数跳跃：50, 100, 200...
                jump_step = base_jump * (2 ** (min(current_strike, 8) - 1))
                new_cursor = cursor_seq - jump_step
                return [], new_cursor, False 
            else:
                return [], 0, False

    async def _fetch_history_robust_main(self, event: AstrMessageEvent, group_id: str, total_count: int) -> List[Dict]:
        """[上层] 鲁棒性历史消息拉取主循环"""
        if event.get_platform_name() != "aiocqhttp": 
            return []
        
        client = event.bot
        collected_messages = []
        cursor_seq = 0
        error_strike = [0] 
        
        # 估算循环次数，防止无限循环
        max_loops = int(total_count / 50) + 20 
        loops = 0
        
        while len(collected_messages) < total_count and loops < max_loops:
            loops += 1
            batch, next_cursor, success = await self._fetch_next_batch_robust(
                client, group_id, cursor_seq, error_strike
            )
            
            if not success:
                # 游标归零或熔断
                if next_cursor <= 0: break
                cursor_seq = next_cursor
                await asyncio.sleep(0.1)
                continue
            
            if not batch: break
            
            for msg in batch:
                collected_messages.append(msg)

            cursor_seq = next_cursor
            await asyncio.sleep(0.2)
        
        # 去重
        unique_msgs = {str(m.get("message_id")): m for m in collected_messages}.values()
        sorted_msgs = sorted(unique_msgs, key=lambda x: x.get("time", 0))
        
        return sorted_msgs[-total_count:]

    # ================= 5. 其他工具方法 =================

    def _force_find_provider(self, target_id: str):
        if not target_id: return None
        target_id_lower = target_id.lower()
        all_providers = []
        if hasattr(self.context, "register"):
            reg_providers = getattr(self.context.register, "providers", None)
            if isinstance(reg_providers, dict): all_providers.extend(reg_providers.values())
            elif isinstance(reg_providers, list): all_providers.extend(reg_providers)
        if hasattr(self.context, "get_all_providers"):
            try: all_providers.extend(self.context.get_all_providers())
            except Exception: pass
        seen = set()
        for p in all_providers:
            if not p or id(p) in seen: continue
            seen.add(id(p))
            p_ids = []
            if hasattr(p, "id") and p.id: p_ids.append(str(p.id))
            if hasattr(p, "provider_id") and p.provider_id: p_ids.append(str(p.provider_id))
            if hasattr(p, "config") and isinstance(p.config, dict) and p.config.get("id"): p_ids.append(str(p.config["id"]))
            if hasattr(p, "provider_config") and isinstance(p.provider_config, dict) and p.provider_config.get("id"): p_ids.append(str(p.provider_config["id"]))
            for pid in p_ids:
                if pid.lower() == target_id_lower: return p
        return None

    async def _save_quote_core(self, event: AstrMessageEvent, text: str, sender_info: dict, group_id: str, origin_time: Optional[int] = None) -> Union[Quote, str, None]:
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
            if isinstance(seg, Comp.At): target_qq = str(seg.qq); break
        if not target_qq and "自己" in event.message_str: target_qq = str(event.get_sender_id())
        raw_text = "".join([s.text for s in event.message_obj.message if isinstance(s, Comp.Plain)])
        nums = re.findall(r"\d+", raw_text)
        if nums and int(nums[0]) > 0: target_count = min(int(nums[0]), max_limit)
        
        if not target_qq and target_count > 1:
            random_quotes = self.store.get_random_batch(search_group_id, target_count)
            if not random_quotes: yield event.plain_result("语录不足。"); return
            refresh_tasks = [self._refresh_quote_name(event, current_group_id, q) for q in random_quotes]
            if refresh_tasks: await asyncio.gather(*refresh_tasks)
            bot_qq = self._get_self_id(event) or "10000"
            html, opts = QuoteRenderer.render_merged_card(random_quotes, bot_qq, "随机语录抽卡", True)
            img = await self.html_render(html, {}, options=opts)
            yield event.image_result(img); return

        if target_qq and target_count > 1:
            user_quotes = self.store.get_user_quotes(search_group_id, target_qq)
            if not user_quotes: yield event.plain_result("该用户暂无语录。"); return
            sel = random.sample(user_quotes, min(len(user_quotes), target_count))
            lname = await self._get_current_name(event, current_group_id, target_qq)
            dname = lname if lname else sel[0].name
            if lname: 
                for q in sel: 
                    q.name = lname
            html, opts = QuoteRenderer.render_merged_card(sel, target_qq, dname, False)
            img = await self.html_render(html, {}, options=opts)
            yield event.image_result(img); return

        quote = self.store.get_random(search_group_id, target_qq)
        if not quote: yield event.plain_result("暂无语录。"); return
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
            yield event.plain_result("仅管理员可删除。"); return
        group_id = str(event.get_group_id())
        qid = self._last_sent_qid.get(group_id)
        if not qid: yield event.plain_result("请先发送一条语录。"); return
        if await self.store.delete_quote(qid):
            yield event.plain_result("删除成功。")
            self._last_sent_qid.pop(group_id, None)
        else: yield event.plain_result("删除失败。")

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
            if isinstance(seg, Comp.Poke): poke_target = str(getattr(seg, "qq", "") or getattr(seg, "target", "") or ""); break
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
        if hasattr(event.message_obj, "self_id") and event.message_obj.self_id: return str(event.message_obj.self_id)
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
            if isinstance(seg, Comp.Reply): return str(getattr(seg, "id", None) or getattr(seg, "msgId", None))
        return None

    async def _fetch_onebot_msg(self, event, mid) -> Dict:
        if event.get_platform_name() != "aiocqhttp": return {}
        try: return await event.bot.api.call_action("get_msg", message_id=int(str(mid))) or {}
        except: return {}

    def _extract_plaintext_from_onebot_message(self, message) -> Optional[str]:
        try:
            if isinstance(message, list):
                return "".join([str(m.get("data",{}).get("text","")) for m in message if m.get("type") in ("text","plain")]).strip() or None
        except: pass
        return None
