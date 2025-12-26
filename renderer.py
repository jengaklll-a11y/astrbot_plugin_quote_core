import html
from datetime import datetime
from typing import List, Tuple, Dict, Any
from .model import Quote

class QuoteRenderer:
    """视图层：负责生成 HTML 和渲染配置"""
    
    @staticmethod
    def render_single_card(q: Quote, index: int, total: int) -> Tuple[str, Dict[str, Any]]:
        """渲染单条语录 - 智能分流入口"""
        # 阈值：超过 60 字或换行过多使用长文本模式
        is_long_text = len(q.text) > 60 or q.text.count('\n') > 4
        
        if is_long_text:
            return QuoteRenderer._render_vertical_card(q, index, total)
        else:
            return QuoteRenderer._render_feed_card(q, index, total)

    @staticmethod
    def _render_feed_card(q: Quote, index: int, total: int) -> Tuple[str, Dict[str, Any]]:
        """布局A：朋友圈/Feed流风格 (整体放大版)"""
        width = 1500
        
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={q.qq}&s=640"
        safe_text = html.escape(q.text)
        safe_name = html.escape(q.name)
        
        try:
            dt = datetime.fromtimestamp(q.created_at)
            months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            month_str = months[dt.month]
            time_text = f"{dt.day:02d} {month_str} {dt.year} {dt.strftime('%H:%M')}"
        except:
            time_text = ""

        count_text = f"#{index} / {total}" if total > 0 else "AstrBot"
        
        html_content = f"""
        <html>
        <head>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;500;700&display=swap');
                * {{ box-sizing: border-box; }}
                html, body {{
                    margin: 0; padding: 0;
                    background: #111111;
                    font-family: 'Noto Sans SC', sans-serif;
                    width: {width}px;
                    max-width: {width}px;
                    height: auto;
                    -webkit-font-smoothing: antialiased;
                }}
                
                body {{
                    /* 加大留白，适应大字体 */
                    padding: 100px;
                    display: flex;
                    flex-direction: column;
                }}
                
                .feed-container {{
                    width: 100%;
                    display: flex; 
                    flex-direction: row; 
                    align-items: flex-start;
                }}
                
                /* 头像区域放大 */
                .avatar-box {{ margin-right: 60px; flex-shrink: 0; }}
                .avatar {{ 
                    width: 180px; height: 180px; /* 增大头像 */
                    border-radius: 24px; 
                    object-fit: cover; 
                    background: #333; 
                }}
                
                .content-box {{ flex: 1; display: flex; flex-direction: column; padding-top: 8px; }}
                
                /* 字体整体放大 */
                .nickname {{ 
                    font-size: 64px; /* 48 -> 64 */
                    font-weight: 600; 
                    color: #7CA0C8; 
                    margin-bottom: 30px; 
                    line-height: 1.2; 
                }}
                
                .text-body {{ 
                    font-size: 70px; /* 52 -> 70 */
                    color: #FFFFFF; 
                    line-height: 1.5; 
                    margin-bottom: 50px; 
                    word-wrap: break-word; 
                    white-space: pre-wrap; 
                }}
                
                .footer-info {{ 
                    font-size: 40px; /* 30 -> 40 */
                    color: #777777; 
                    display: flex; align-items: center; justify-content: space-between; 
                    width: 100%; margin-top: 15px; 
                }}
                
                .count-tag {{
                    display: inline-block; background: #222; color: #888;
                    padding: 8px 24px; /* 加大标签内边距 */
                    border-radius: 12px; border: 1px solid #333;
                    box-shadow: 8px 8px 0px rgba(255, 255, 255, 0.1); 
                    font-family: 'Consolas', 'Monaco', monospace; 
                    font-size: 36px; /* 28 -> 36 */
                    font-weight: bold; 
                    letter-spacing: 2px;
                }}
            </style>
        </head>
        <body>
            <div class="feed-container">
                <div class="avatar-box"><img class="avatar" src="{avatar_url}"></div>
                <div class="content-box">
                    <div class="nickname">{safe_name}</div>
                    <div class="text-body">{safe_text}</div>
                    <div class="footer-info">
                        <span>{time_text}</span>
                        <span class="count-tag">{count_text}</span>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        # 视口高度设为 1，依赖 full_page=True 自动扩展
        options = {"full_page": True, "viewport": {"width": width, "height": 1}}
        return html_content, options

    @staticmethod
    def _render_vertical_card(q: Quote, index: int, total: int) -> Tuple[str, Dict[str, Any]]:
        """布局B：垂直宽幅卡片 (保持不变)"""
        width = 1500  
        min_height = 800
        
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={q.qq}&s=640"
        safe_text = html.escape(q.text)
        safe_name = html.escape(q.name)
        
        try:
            dt = datetime.fromtimestamp(q.created_at)
            months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", 
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            month_str = months[dt.month]
            time_text = f"{dt.day:02d} {month_str} {dt.year} {dt.strftime('%H:%M')}"
        except:
            time_text = ""

        count_text = f"#{index} / {total}" if total > 0 else "AstrBot"
        
        html_content = f"""
        <html>
        <head>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;700&display=swap');
                * {{ box-sizing: border-box; }}
                body {{ 
                    margin: 0; padding: 0;
                    background: #121212;
                    font-family: 'Noto Sans SC', sans-serif; 
                    width: 100%; min-height: {min_height}px; height: auto;
                    display: flex; flex-direction: column; align-items: center; justify-content: center;
                    padding: 80px; -webkit-font-smoothing: antialiased;
                }}
                .card {{
                    width: 100%; background: #1E1E1E; border-radius: 24px;
                    box-shadow: 0 10px 30px rgba(0,0,0,0.3); border: 1px solid #2A2A2A;
                    overflow: hidden; position: relative;
                }}
                .card-top-bar {{ height: 12px; width: 100%; background: linear-gradient(90deg, #5E81AC, #88C0D0); }}
                .header {{ padding: 40px 60px 20px 60px; display: flex; align-items: center; border-bottom: 1px solid #2A2A2A; }}
                .avatar {{ width: 100px; height: 100px; border-radius: 20px; object-fit: cover; border: 3px solid #333; margin-right: 30px; }}
                .user-info {{ display: flex; flex-direction: column; flex: 1; }}
                .username {{ font-size: 38px; font-weight: 600; color: #7CA0C8; }}
                .info-bar {{ display: flex; align-items: center; justify-content: space-between; width: 100%; margin-top: 10px; }}
                .time-text {{ font-size: 26px; color: #666; }}
                .content-area {{ padding: 60px 80px 80px 80px; min-height: 400px; display: flex; flex-direction: column; justify-content: center; }}
                .quote-text {{ font-size: 42px; line-height: 1.6; color: #E0E0E0; text-align: justify; word-wrap: break-word; white-space: pre-wrap; }}
                .footer-deco {{ position: absolute; bottom: 30px; right: 40px; font-family: "Times New Roman", serif; font-size: 140px; color: #252525; opacity: 0.5; pointer-events: none; line-height: 1; }}
                .count-tag {{
                    display: inline-block; background: #222; color: #888;
                    padding: 4px 14px; border-radius: 8px; border: 1px solid #333;
                    box-shadow: 5px 5px 0px rgba(255, 255, 255, 0.1); 
                    font-family: 'Consolas', 'Monaco', monospace; font-size: 26px; font-weight: bold; letter-spacing: 1px;
                }}
            </style>
        </head>
        <body>
            <div class="card">
                <div class="card-top-bar"></div>
                <div class="header">
                    <img class="avatar" src="{avatar_url}">
                    <div class="user-info">
                        <div class="username">{safe_name}</div>
                        <div class="info-bar">
                            <span class="time-text">{time_text}</span>
                            <span class="count-tag">{count_text}</span>
                        </div>
                    </div>
                </div>
                <div class="content-area"><div class="quote-text">{safe_text}</div></div>
                <div class="footer-deco">”</div>
            </div>
        </body>
        </html>
        """
        options = {"full_page": True, "viewport": {"width": width, "height": min_height}}
        return html_content, options

    @staticmethod
    def render_merged_card(quotes: List[Quote], qq: str, name: str, show_author: bool = False) -> Tuple[str, Dict[str, Any]]:
        """渲染合集长图"""
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
        safe_name = html.escape(name)
        view_width = 1000
        
        quotes_list_html = ""
        for i, q in enumerate(quotes):
            text = html.escape(q.text)
            if not text: continue
            
            item_font_size = 46 if len(q.text) < 50 else 38
            
            author_html = ""
            if show_author:
                sub_avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={q.qq}&s=640"
                author_html = f"""
                <div class="card-footer">
                    <img class="sub-avatar" src="{sub_avatar_url}">
                    <div class="card-author">—— {html.escape(q.name)}</div>
                </div>
                """
                
            quotes_list_html += f"""
            <div class="card">
                <div class="card-header"><span class="index-tag">#{i+1}</span></div>
                <div class="card-content" style="font-size: {item_font_size}px;">{text}</div>
                {author_html}
            </div>
            """

        html_content = f"""
        <html>
        <head>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;700&display=swap');
                * {{ box-sizing: border-box; }}
                body {{
                    margin: 0; padding: 0; background-color: #121212;
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif;
                    width: 100%; min-height: 100vh; color: #fff;
                    display: flex; flex-direction: column; align-items: center; 
                }}
                .main-wrapper {{
                    width: 100%; display: flex; flex-direction: column; align-items: center;
                    background-color: #121212; flex: 1; padding-bottom: 60px;
                }}
                .header {{
                    width: 100%; padding: 80px 50px 50px 50px;
                    display: flex; align-items: center; background-color: #1E1E1E; border-bottom: 1px solid #2C2C2C;
                }}
                .avatar {{
                    width: 130px; height: 130px; border-radius: 20px; object-fit: cover; background: #333; margin-right: 40px; flex-shrink: 0;
                }}
                .header-info {{
                    display: flex; flex-direction: column; justify-content: center; max-width: calc(100% - 170px); 
                }}
                .title {{
                    font-size: 52px; font-weight: 600; color: #fff; margin-bottom: 15px; line-height: 1.2; word-break: break-word;
                }}
                .subtitle {{ font-size: 32px; color: #888; }}
                .list-container {{
                    width: 100%; padding: 50px 50px 0 50px; display: flex; flex-direction: column; gap: 36px;
                }}
                .card {{
                    background-color: #1E1E1E; border-radius: 24px; padding: 40px;
                    box-shadow: 0 6px 16px rgba(0,0,0,0.25); border: 1px solid #2A2A2A; width: 100%;
                }}
                .card-header {{ display: flex; justify-content: flex-start; align-items: center; margin-bottom: 25px; }}
                .index-tag {{
                    font-size: 28px; font-weight: bold; color: #5E81AC; background: rgba(94, 129, 172, 0.15); padding: 6px 16px; border-radius: 8px;
                }}
                .card-content {{
                    line-height: 1.5; color: #E0E0E0; font-weight: 400; text-align: left;
                    word-wrap: break-word; word-break: break-word; white-space: pre-wrap;
                }}
                .card-footer {{
                    display: flex; flex-direction: column; align-items: flex-end; margin-top: 35px;
                }}
                .sub-avatar {{
                    width: 90px; height: 90px; border-radius: 12px; object-fit: cover; 
                    border: 2px solid #333; margin-bottom: 15px;
                    box-shadow: 0 4px 10px rgba(0,0,0,0.3);
                }}
                .card-author {{
                    color: #7CA0C8; font-size: 30px; font-weight: 400;
                }}
            </style>
        </head>
        <body>
            <div class="main-wrapper">
                <div class="header">
                    <img class="avatar" src="{avatar_url}">
                    <div class="header-info">
                        <div class="title">{safe_name}</div>
                        <div class="subtitle">本次抽取了 {len(quotes)} 条语录</div>
                    </div>
                </div>
                <div class="list-container">{quotes_list_html}</div>
            </div>
        </body>
        </html>
        """
        options = {"full_page": True, "viewport": {"width": view_width, "height": 1000}}
        return html_content, options
