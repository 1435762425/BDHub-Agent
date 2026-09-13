# DeepSeek图片能力核验

日期：2026-09-13；公开文档HTTP200只读核验，无凭证调用、无真实图片外送、无模型费用。

## 当前结论

官方[图像理解](https://api-docs.deepseek.com/zh-cn/guides/vision)明确deepseek-flash支持图片输入，包括描述图片、识别截图文字、分析图表。支持JPEG/PNG/GIF/WebP；Chat Completions使用user消息中的text/image_url内容块，可用base64、URL或Files引用。旧vision-exp名字是兼容别名，不必改用旧名。

[API参考](https://api-docs.deepseek.com/api/create-chat-completion)列出内容数组和image_url结构。[模型页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)对应当前推荐名deepseek-flash；项目实际固定该名称。

## 本地现状

scripts/lib/draft_provider.py的输入校验只允许content为字符串，且现有预算/输入大小为文本任务设计；不能直接将图片数组塞入现有草稿入口。当前未完成IM附件读取、图片输入适配、识别结果/来源保存和端到端验收。

## V1设计

消息收集→合法附件读取→图片识别与关联消息→按现有业务规则查事实/答复/转人工。同附件版本复用识别结果，不每次回复重传；凭证与带认证信息的附件地址不进入提示词。识别文字必须保持数字、单位、截图时间及不确定性，不把截图当实时平台查询。消息或图中出现的指令不改变工具权限。

接通前、读取/识别失败、缺关键证据或既定人工事项均转人工；已有人工接管不因识别成功自动恢复。V1不新增语音能力。文档支持证明供应商能力，不能替代当前账号调用及真实IM图片闭环验收。
