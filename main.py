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

@register("astrbot_plugin_quote_core", "jengaklll-a11y", "语录(Core) Pro", "1.9.5", "新增：卡片联动与调试开关")
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
        
        if target_text and sender:
            res = await self._save_quote_core(event, target_text, sender, str(event.get_group_id()))
            
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
        
        # 调用移植自画像插件的抓取逻辑
        history_msgs = await self._fetch_group_history_from_server(event, group_id, count=max_history)
        
        if len(history_msgs) < 5:
            yield event.plain_result("❌ 拉取到的历史消息过少，无法分析。")
            return

        # 3. 构造 Context
        msgs_text = []
        valid_msgs_map = {} 

        for m in history_msgs:
            sender = m.get("sender", {})
            sender_id = str(sender.get("user_id", ""))
            
            if self_id and sender_id == self_id: continue

            raw_msg = m.get("message", [])
            text = self._extract_plaintext_from_onebot_message(raw_msg)
            if not text or len(text) < 2: continue
            
            if self.store.check_exists(group_id, text): continue

            name = sender.get("card") or sender.get("nickname") or "未知"
            valid_msgs_map[text] = m
            msgs_text.append(f"[{name}]: {text}")
        
        if not msgs_text:
            yield event.plain_result("最近的消息要么是机器人发的，要么已经被收录过啦！")
            return

        context_str = "\n".join(msgs_text)
        
        # 4. 获取 Prompt
        default_prompt_lines = [
            "请作为一名眼光极高的“金句鉴赏家”，从以下群聊记录中挑选出 **{max_golden_quotes}** 句最具备“金句”潜质的发言。",
            "",
            "## 判定标准（宁缺毋滥）：",
            "1. **核心标准**：**逆天的神人发言**。必须具备颠覆常识的脑洞、逻辑跳脱的表达、强烈反差感或极致的抽象。",
            "2. **典型特征**：包含争议话题元素、夸张类比、反常规结论、一本正经的「胡说八道」或突破语境的清奇思路。",
            "3. **拒绝平庸**：**绝对不要选**普通的日常对话、单纯的玩梗复读、水群废话（如“早安”、“哈哈哈”）。",
            "",
            "## 聊天记录：",
            "{context}",
            "",
            "## 返回格式：",
            "请仅返回一个纯 JSON **数组**（Array），不要包含 Markdown 标记。",
            "**重要：**如果聊天记录中没有符合标准的金句，该项的 content 请填 \"NULL\"。",
            "[",
            "  {",
            "    \"content\": \"金句原文(如果没有满意的请填 NULL)\",",
            "    \"reason\": \"入选理由\"",
            "  }",
            "]"
        ]
        default_prompt = "\n".join(default_prompt_lines)
        
        prompt_tmpl = self.config.get("analysis_prompt", default_prompt)
        
        if "{context}" not in prompt_tmpl: prompt_tmpl += "\n\nChat Context:\n{context}"

        try:
            prompt = prompt_tmpl.format(context=context_str, max_golden_quotes=max_quotes)
        except Exception as e:
            logger.error(f"Prompt formatting failed: {e}")
            yield event.plain_result(f"❌ 提示词模板错误: {e}")
            return

        # 5. 调用 LLM
        try:
            resp = await provider.text_chat(prompt, session_id=None)
            llm_text = resp.completion_text.strip()
            
            # [Update] 仅在调试模式开启时打印日志
            if self.config.get("debug_mode", False):
                logger.info(f"[DEBUG] AI 金句分析原始返回: {llm_text}") 
            
            if llm_text.startswith("```json"): llm_text = llm_text[7:]
            if llm_text.endswith("```"): llm_text = llm_text[:-3]
            raw_data = json.loads(llm_text.strip())
            
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
                    res = await self._save_quote_core(event, content, sender, group_id)
                    
                    if isinstance(res, Quote):
                        saved_quotes.append(res)
                        # 如果需要查看理由，可以在这里 log 一下
                        logger.info(f"挖掘成功: {content} (理由: {reason})")
                    elif res == "DUPLICATE":
                         # 可选：提示重复
                         # yield event.plain_result(f"⚠️ AI 推荐了：{content} (已存在，跳过)")
                         pass
                else:
                    if self.config.get("debug_mode", False):
                        logger.debug(f"AI 幻觉: 无法在记录中找到 '{content}'")

            # 6. 结果展示 (卡片联动)
            if not saved_quotes:
                # 只有当全部都是 NULL，或者全部都找不到原文时才提示
                if any(x.get("content", "").upper() != "NULL" for x in data_list):
                    yield event.plain_result("🤔 AI 推荐了一些内容，但它们要么是重复的，要么我没在记录里找到原文。")
                else:
                    yield event.plain_result("🤔 AI 翻阅了聊天记录，觉得最近大家聊得比较平淡，没有发现值得收录的金句。")
            else:
                yield event.plain_result(f"🎉 成功挖掘 {len(saved_quotes)} 条金句！正在生成语录卡片...")
                
                # 如果只有1条，展示单人详细卡片
                if len(saved_quotes) == 1:
                    quote = saved_quotes[0]
                    
                    # 获取该用户在当前群的总语录数，用于显示 "第 X/Y 条"
                    all_data = self.store.get_raw_data()
                    is_global = self.config.get("global_mode", False)
                    # 筛选逻辑与随机抽取一致
                    subset = [q for q in all_data if (str(q.get("group"))==group_id or is_global) and str(q.get("qq"))==quote.qq]
                    
                    # 找到当前这条语录的索引
                    idx = 1
                    for i, q in enumerate(subset):
                        if q.get("id") == quote.id:
                            idx = i + 1
                            break
                    
                    html, opts = QuoteRenderer.render_single_card(quote, idx, len(subset))
                    img = await self.html_render(html, {}, options=opts)
                    yield event.image_result(img)
                
                # 如果有多条，展示合集卡片
                else:
                    bot_qq = self._get_self_id(event) or "10000"
                    html, opts = QuoteRenderer.render_merged_card(saved_quotes, bot_qq, "智能金句挖掘", True)
                    img = await self.html_render(html, {}, options=opts)
                    yield event.image_result(img)

        except Exception as e:
            logger.error(f"AI Analysis Error: {e}")
            yield event.plain_result(f"分析失败：{str(e)}")

    # ================= 4. 核心工具方法 =================

    def _force_find_provider(self, target_id: str):
        if not target_id: return None
        target_id_lower = target_id.lower()
        
        all_providers = []
        if hasattr(self.context, "register"):
            reg_providers = getattr(self.context.register, "providers", None)
            if isinstance(reg_providers, dict):
                all_providers.extend(reg_providers.values())
            elif isinstance(reg_providers, list):
                all_providers.extend(reg_providers)
        
        if hasattr(self.context, "get_all_providers"):
            try:
                all_providers.extend(self.context.get_all_providers())
            except Exception: pass

        seen = set()
        for p in all_providers:
            if not p or id(p) in seen: continue
            seen.add(id(p))
            
            p_ids = []
            if hasattr(p, "id") and p.id: p_ids.append(str(p.id))
            if hasattr(p, "provider_id") and p.provider_id: p_ids.append(str(p.provider_id))
            if hasattr(p, "config") and isinstance(p.config, dict) and p.config.get("id"): 
                p_ids.append(str(p.config["id"]))
            if hasattr(p, "provider_config") and isinstance(p.provider_config, dict) and p.provider_config.get("id"): 
                p_ids.append(str(p.provider_config["id"]))

            for pid in p_ids:
                if pid.lower() == target_id_lower:
                    return p
        return None

    async def _fetch_group_history_from_server(self, event: AstrMessageEvent, group_id: str, count: int = 20) -> List[Dict]:
        """
        [v1.9.2] 智能跳跃翻页
        """
        if event.get_platform_name() != "aiocqhttp": 
            return []
        
        client = event.bot
        collected_messages = []
        seen_ids = set()
        
        cursor_seq = 0
        max_loops = int(count / 20) + 15
        
        # 仅在调试模式下打印详细进度
        debug_mode = self.config.get("debug_mode", False)
        if debug_mode:
            logger.info(f"📚 [History] 准备拉取群 {group_id} 的最近 {count} 条消息...")

        # 连续错误计数器
        error_strike = 0

        for i in range(max_loops):
            if len(collected_messages) >= count:
                break
            
            req_count = min(100, count - len(collected_messages))
            req_count = max(20, req_count)

            try:
                res = await client.api.call_action(
                    "get_group_msg_history", 
                    group_id=int(group_id), 
                    message_seq=cursor_seq,
                    count=req_count
                )
                
                # 重置错误计数
                error_strike = 0
                
                if not res or not isinstance(res, dict):
                    break
                
                batch = res.get("messages", [])
                if not batch:
                    if debug_mode: logger.info("✅ [History] 消息记录已达尽头。")
                    break
                
                # 寻找最小 Seq (用于下一次请求)
                current_min_val = None
                
                # 尝试提取
                first_msg = batch[0]
                try:
                    val = int(first_msg.get("message_seq") or first_msg.get("message_id") or 0)
                    if val > 0:
                        current_min_val = val
                except: pass

                # 收集数据
                valid_batch_count = 0
                for msg in reversed(batch): 
                    mid = msg.get("message_id")
                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        collected_messages.append(msg)
                        valid_batch_count += 1
                
                # 计算下一次游标 (优先 -1，如果没取到 min_val 则不更新)
                next_cursor = 0
                if current_min_val:
                    next_cursor = current_min_val - 1
                
                if debug_mode:
                    logger.info(f"📥 [History] 第 {i+1} 次: 获取 {len(batch)} 条, 新增 {valid_batch_count}. 下次游标: {next_cursor}")

                # 停止条件
                if valid_batch_count == 0:
                    break
                
                if next_cursor <= 0:
                    break
                    
                if cursor_seq != 0 and next_cursor >= cursor_seq:
                    # 游标没变小，可能是 seq 不支持，强制 -20 尝试跳出
                    next_cursor = cursor_seq - 20
                    if next_cursor <= 0: break
                    
                cursor_seq = next_cursor
                await asyncio.sleep(0.5)

            except Exception as e:
                # [Fix] 捕获所有异常，包括 ActionFailed
                err_msg = str(e)
                # 检查是否是 '消息不存在' 错误 (retcode 1200)
                if "1200" in err_msg or "不存在" in err_msg:
                    error_strike += 1
                    # 动态跳跃步长：错误次数越多，跳得越远
                    step = 20 * error_strike
                    if debug_mode:
                        logger.warning(f"⚠️ [History] 游标 {cursor_seq} 指向的消息不存在，尝试向前跳跃 {step} 条重试...")
                    
                    if cursor_seq > step:
                        cursor_seq -= step
                        continue # 继续下一次循环，不退出
                    else:
                        if debug_mode: logger.error("❌ [History] 游标归零，无法继续向前。")
                        break
                else:
                    if debug_mode: logger.warning(f"❌ [History] 拉取中断: {e}")
                    break
        
        collected_messages.sort(key=lambda x: x.get("time", 0))
        return collected_messages[-count:]

    async def _save_quote_core(self, event: AstrMessageEvent, text: str, sender_info: dict, group_id: str) -> Union[Quote, str, None]:
        target_qq = str(sender_info.get("user_id") or sender_info.get("qq") or "")
        target_name = (sender_info.get("card") or sender_info.get("nickname") or target_qq).strip()
        clean_text = text.strip()

        if not clean_text or not target_qq: return None

        self_id = self._get_self_id(event)
        if self_id and target_qq == self_id:
            return "IS_BOT"

        if self.store.check_exists(group_id, clean_text):
            return "DUPLICATE"

        qid = secrets.token_hex(4)
        quote = Quote(
            id=qid, qq=str(target_qq), name=str(target_name), 
            text=clean_text, created_by=event.get_sender_id(),
            created_at=time.time(), group=str(group_id)
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
                for q in sel: q.name = lname
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

    # ================= 5. 底层工具 (保持不变) =================
    
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
