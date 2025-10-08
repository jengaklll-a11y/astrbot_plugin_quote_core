# quotes

一个 AstrBot 插件：
- 先“回复某人的消息”，再发送 `/quote add`（别名：`/语录提交`、`/语录添加`）保存该被回复消息为语录。
- 支持图片：在回复的同时，若被回复消息或当前消息链中含有图片，将自动保存到语录库；也支持在发送指令时直接附带图片上传。
- 发送 `语录`（或 `/quote random`）随机发送一张带头像与昵称的语录图片（当前渲染不展示原图，仅保存于库内）。

数据持久化于 AstrBot 根目录：
- 文本：`data/quotes/quotes.json`
- 图片：`data/quotes/images/*`

Napcat/OneBot 平台默认使用 qlogo 头像；也可在配置中改为 platform。

依赖：
- `httpx`（下载远程图片）
