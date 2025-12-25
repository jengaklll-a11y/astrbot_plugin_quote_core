import html
from typing import List, Tuple, Dict, Any
from .model import Quote

class QuoteRenderer:
    """视图层：负责生成 HTML 和渲染配置"""
    
    @staticmethod
    def render_single_card(q: Quote, index: int, total: int) -> Tuple[str, Dict[str, Any]]:
        """渲染单条语录"""
        height = 600      
        width = 1500      
        left_width = 600  # 左侧头像区域 600x600
        
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={q.qq}&s=640"
        
        safe_text = html.escape(q.text)
        safe_name = html.escape(q.name)
        count_text = f"(第 {index} 条 / 共 {total} 条)" if total > 0 else ""

        html_content = f"""
        <html>
        <head>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;700&display=swap');
                * {{ box-sizing: border-box; }}
                body {{ 
                    margin: 0; padding: 0;
                    background: #151515;
                    font-family: 'Noto Sans SC', sans-serif; 
                    width: {width}px; height: {height}px; 
                    display: flex; overflow: hidden;
                    -webkit-font-smoothing: antialiased;
                }}
                
                .left {{ 
                    width: {left_width}px; 
                    height: 100%;
                    position: relative; flex-shrink: 0; background: #000;
                }}
                .left img {{ 
                    width: 100%; height: 100%; 
                    object-fit: cover;
                    object-position: center;
                    filter: contrast(1.05) saturate(1.05) brightness(0.95);
                }}
                .gradient-mask {{ 
                    position: absolute; inset: 0; 
                    background: linear-gradient(to right, rgba(21,21,21, 0) 0%, rgba(21,21,21, 0.4) 85%, #151515 100%);
                    z-index: 1;
                }}

                .right {{ 
                    flex: 1; height: 100%;
                    display: flex; flex-direction: column; 
                    justify-content: center; 
                    align-items: center;     
                    padding: 0 60px; 
                    color: #fff; position: relative; z-index: 2;
                }}

                .quote-container {{
                    position: relative;
                    width: 100%;
                    display: flex; 
                    justify-content: center; 
                    align-items: center;
                }}

                .quote-content-wrapper {{
                    position: relative;
                    display: inline-block;
                    max-width: 90%;
                    text-align: center;
                }}

                .mark {{ 
                    color: #ddd; 
                    font-size: 100px;
                    font-family: "Times New Roman", serif;
                    position: absolute; 
                    top: -50px; 
                    left: -60px; 
                    opacity: 0.3;
                    line-height: 1;
                }}

                .quote-text {{ 
                    font-size: 52px; 
                    line-height: 1.4; 
                    font-weight: bold; 
                    letter-spacing: 2px;
                    text-shadow: 0 4px 15px rgba(0,0,0,0.8);
                    position: relative; 
                    z-index: 2;
                }}

                .footer {{
                    position: absolute; 
                    bottom: 35px; 
                    right: 50px;
                    text-align: right; 
                    display: flex; flex-direction: column; align-items: flex-end;
                }}
                .author {{ 
                    font-size: 34px; 
                    color: #eee; font-weight: 300; margin-bottom: 6px;
                }}
                .count-info {{
                    font-size: 22px; 
                    color: #777; font-weight: 300; letter-spacing: 1px;
                }}
            </style>
        </head>
        <body>
            <div class="left"><img src="{avatar_url}"><div class="gradient-mask"></div></div>
            <div class="right">
                <div class="quote-container">
                    <div class="quote-content-wrapper">
                         <span class="mark">“</span>
                         <div class="quote-text">{safe_text}</div>
                    </div>
                </div>
                <div class="footer">
                    <div class="author">—— {safe_name}</div>
                    <div class="count-info">{count_text}</div>
                </div>
            </div>
        </body>
        </html>
        """
        
        options = {
            "full_page": False, 
            "viewport": {"width": width, "height": height},
            "clip": {"x": 0, "y": 0, "width": width, "height": height}
        }
        return html_content, options

    @staticmethod
    def render_merged_card(quotes: List[Quote], qq: str, name: str) -> Tuple[str, Dict[str, Any]]:
        """渲染合集长图"""
        avatar_url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
        safe_name = html.escape(name)
        
        quotes_html = ""
        for i, q in enumerate(quotes):
            text = html.escape(q.text)
            if not text: continue
            quotes_html += f"""
            <div class="quote-item">
                <div class="quote-index">{i+1}</div>
                <div class="quote-content">{text}</div>
            </div>
            """

        html_content = f"""
        <html>
        <head>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@300;400;700&display=swap');
                * {{ box-sizing: border-box; }}
                body {{
                    margin: 0; padding: 0;
                    background: #151515;
                    font-family: 'Noto Sans SC', sans-serif;
                    width: 800px;
                    color: #fff;
                    -webkit-font-smoothing: antialiased;
                }}
                .container {{
                    display: flex; flex-direction: column; align-items: center;
                    padding-bottom: 60px;
                }}
                .header {{
                    width: 100%;
                    padding: 60px 40px 40px 40px;
                    display: flex; flex-direction: column; align-items: center;
                    background: linear-gradient(to bottom, #1a1a1a, #151515);
                    border-bottom: 1px solid #333;
                }}
                .avatar {{
                    width: 120px; height: 120px;
                    border-radius: 50%;
                    object-fit: cover;
                    border: 4px solid #333;
                    margin-bottom: 20px;
                }}
                .title {{
                    font-size: 36px; font-weight: bold; letter-spacing: 2px;
                    margin-bottom: 5px;
                }}
                .subtitle {{
                    font-size: 18px; color: #666; letter-spacing: 4px; text-transform: uppercase;
                }}
                .quote-list {{
                    width: 100%;
                    padding: 40px;
                    display: flex; flex-direction: column; gap: 30px;
                }}
                .quote-item {{
                    position: relative;
                    padding: 30px 40px;
                    background: #1e1e1e;
                    border-radius: 12px;
                    border-left: 4px solid #444;
                }}
                .quote-index {{
                    position: absolute; top: 15px; left: 15px;
                    font-size: 60px; color: #2a2a2a; font-weight: bold;
                    line-height: 1; z-index: 0;
                }}
                .quote-content {{
                    position: relative; z-index: 1;
                    font-size: 26px; line-height: 1.6; color: #ddd;
                    text-align: center;
                    font-weight: 400;
                }}
                .footer {{
                    margin-top: 20px;
                    font-size: 16px; color: #444;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <img class="avatar" src="{avatar_url}">
                    <div class="title">{safe_name} 的语录精选</div>
                    <div class="subtitle">QUOTES COLLECTION</div>
                </div>
                <div class="quote-list">
                    {quotes_html}
                </div>
                <div class="footer">Created by AstrBot Quotes Plugin</div>
            </div>
        </body>
        </html>
        """
        options = {"full_page": True, "viewport": {"width": 800, "height": 1000}}
        return html_content, options
