/**
 * Markdown 工具函数
 *
 * QQ 官方 API 支持在群聊和私聊中渲染 Markdown（2026年4月起全面开放），
 * 但直接发送纯文本中的 markdown 符号不会被渲染——必须使用 markdown 消息类型。
 *
 * 参考: https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html
 */

/**
 * 检测文本是否包含 Markdown 格式
 */
export function containsMarkdown(text: string): boolean {
  return /(\*\*|__|~~|`|\[.*\]\(.*\)|^#{1,6}\s|^[-*+]\s|^\d+\.\s|^>\s|---|\|)/m.test(text);
}

/**
 * 将 Markdown 文本转换为纯文本（移除格式符号）
 * 用于当 markdown 消息发送失败时的降级方案
 */
export function stripMarkdown(md: string): string {
  return md
    // 移除代码块（```...```）
    .replace(/```[\s\S]*?```/g, '(代码块)')
    // 移除行内代码 (`code`)
    .replace(/`([^`]+)`/g, '$1')
    // 移除图片 ![alt](url)
    .replace(/!\[([^\]]*)\]\([^)]+\)/g, '$1')
    // 链接 [text](url) → text
    .replace(/\[([^\]]*)\]\([^)]+\)/g, '$1')
    // 加粗 **text** 或 __text__
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    // 斜体 *text* 或 _text_（小心不要匹配到加粗的 **）
    .replace(/(?<!\*)\*(?!\*)(.*?)(?<!\*)\*(?!\*)/g, '$1')
    .replace(/(?<!_)_(?!_)(.*?)(?<!_)_(?!_)/g, '$1')
    // 删除线 ~~text~~
    .replace(/~~(.*?)~~/g, '$1')
    // 标题 #  → 移除井号
    .replace(/^#{1,6}\s+/gm, '')
    // 无序列表 - 或 *
    .replace(/^[-*+]\s+/gm, '')
    // 有序列表 1.
    .replace(/^\d+\.\s+/gm, '')
    // 块引用 >
    .replace(/^>\s+/gm, '')
    // 分割线 ---
    .replace(/^---+$/gm, '')
    // 表格 | --- | → 移除表格线
    .replace(/\|.*\|/g, (match) => {
      if (/^\|[\s:-]+\|$/.test(match)) return '';
      return match.replace(/\|/g, ' ');
    })
    // 多余空行压缩
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * 生成适用于 QQ 的简单 Markdown 文本。
 * QQ 支持的 Markdown 格式:
 *   - 标题 # h1 ~ # h6
 *   - 加粗 **text**、斜体 *text*、删除线 ~~text~~、下划线
 *   - 链接 [文本](url)
 *   - 有序/无序列表
 *   - 引用 > text
 *   - 分割线 ---
 *
 * 注意: QQ 私聊 Markdown 中不能包含 @ 用户标签，否则报 400 错误。
 */
export function toQQMarkdown(text: string): string {
  // 目前 AI 输出的标准 Markdown 和 QQ 兼容良好，直接返回即可
  // 后续可以根据需要做格式适配
  return text;
}
