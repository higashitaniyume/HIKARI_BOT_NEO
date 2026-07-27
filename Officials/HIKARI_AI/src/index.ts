import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bot, ReceiverMode, segment } from 'qq-official-bot';
import type { Intent } from 'qq-official-bot';
import type { AppConfig } from './types.js';
import { AIChat } from './ai.js';
import { stripMarkdown } from './utils.js';

// ── 加载配置 ────────────────────────────────────────────────
const __dirname = dirname(fileURLToPath(import.meta.url));
const configPath = resolve(__dirname, '..', 'config.json');
let appConfig: AppConfig;

try {
  const raw = readFileSync(configPath, 'utf-8');
  appConfig = JSON.parse(raw) as AppConfig;
  validateConfig(appConfig);
} catch (err) {
  console.error('❌ 无法加载配置文件 config.json:', err);
  process.exit(1);
}

function validateConfig(config: AppConfig): void {
  const { qqbot, ai } = config;
  if (!qqbot.appid || qqbot.appid === 'YOUR_APPID') {
    console.error('❌ 请先配置 config.json 中的 qqbot.appid');
    process.exit(1);
  }
  if (!qqbot.secret || qqbot.secret === 'YOUR_APPSECRET') {
    console.error('❌ 请先配置 config.json 中的 qqbot.secret');
    process.exit(1);
  }
  if (!ai.apiKey || ai.apiKey.startsWith('YOUR_')) {
    console.error('❌ 请先配置 config.json 中的 ai.apiKey（DeepSeek API Key）');
    process.exit(1);
  }
}

// ── Markdown 回复辅助 ─────────────────────────────────────
//
// QQ 官方 API 支持在群聊和私聊中渲染 Markdown（2026年4月起全面开放），
// 但需要以 markdown 消息类型发送，纯文本消息中的 markdown 符号不会被渲染。
//
// 策略：先尝试以 markdown 消息发送 → 失败时自动降级为纯文本

const testImagePath = 'C:/Users/shimikoi/OneDrive/图片/1785129005759.png';
const testVideoPath = 'C:/PublicFiles/Downloads/VM_Download/0.mp4';

async function replyWithMarkdown(event: any, text: string, isPrivate: boolean): Promise<void> {
  // 私聊 Markdown 不能包含 @ 标签，否则报 400
  // 如果 Markdown 中包含可能的 @ 格式，直接走纯文本
  if (isPrivate && /@\S+/.test(text)) {
    await event.reply(stripMarkdown(text));
    return;
  }

  try {
    // 发送 Markdown 消息（使用 QQ 官方 markdown 消息类型）
    await event.reply(segment.markdown(text));
  } catch {
    // Markdown 发送失败 → 降级为纯文本
    console.warn('[回复] ⚠️  Markdown 发送失败，降级为纯文本');
    const plainText = stripMarkdown(text);
    await event.reply(plainText);
  }
}

// ── 跨机器人媒体解析转发（HIKARI_BOT_NEO → HIKARI_AI） ────
//
// HIKARI_AI 收到含媒体链接的消息时，转发给 NEO 解析/下载，
// 然后通过共享目录直接发送媒体文件。
// 仅在内网可用（Docker 内网 或 192.168.31.x LAN）。

const NEO_API_URL = 'http://hikaribot:53123/api/parse_external';

const SUPPORTED_LINK_PATTERNS = [
  'bilibili.com', 'b23.tv',
  'douyin.com', 'iesdouyin.com', 'tiktok.com',
  'kuaishou.com', 'gifshow.com', 'chenzhongtech.com',
  'weibo.com', 'weibo.cn',
  'xiaohongshu.com', 'xhslink.com',
  'goofish.com', 'm.tb.cn',
  'toutiao.com', 'xiaoheihe.cn',
  'twitter.com', 'x.com',
  'pixiv.net', 'pximg.net',
  'youtube.com', 'youtu.be',
  'instagram.com', 'facebook.com', 'fb.com',
];

function containsMediaLink(text: string): boolean {
  const lowered = text.toLowerCase();
  return SUPPORTED_LINK_PATTERNS.some(pattern => lowered.includes(pattern));
}

interface NeoParseFile {
  path: string;
  type: string;
  size: number;
}

interface NeoParseItem {
  success: boolean;
  platform: string;
  title?: string;
  author?: string;
  source_url?: string;
  files: NeoParseFile[];
  error?: string;
}

interface NeoParseResponse {
  success: boolean;
  results: NeoParseItem[];
  error?: string;
}

async function parseMediaViaNeo(text: string): Promise<NeoParseResponse | null> {
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30 * 60 * 1000); // 30 min（与 NEO 侧 operation_timeout_seconds 对齐）

    const response = await fetch(NEO_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: text }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    if (!response.ok) {
      console.warn(`[媒体解析] NEO API 返回 HTTP ${response.status}`);
      return null;
    }
    return await response.json() as NeoParseResponse;
  } catch (err) {
    console.warn('[媒体解析] 调用 NEO API 失败:', err instanceof Error ? err.message : err);
    return null;
  }
}

async function handleMediaLinks(event: any, text: string): Promise<boolean> {
  if (!containsMediaLink(text)) return false;

  console.log('[媒体解析] 检测到媒体链接，转发到 NEO 解析...');
  const result = await parseMediaViaNeo(text);

  if (!result?.success || !result.results?.length) {
    console.warn('[媒体解析] NEO 解析失败或未返回结果');
    await event.reply('媒体解析失败，请稍后重试').catch(() => {});
    return true;
  }

  let sentCount = 0;
  for (const item of result.results) {
    if (!item.success || !item.files?.length) continue;

    for (const file of item.files) {
      try {
        if (file.type === 'video') {
          await event.reply(segment.video(file.path));
        } else {
          await event.reply(segment.image(file.path));
        }
        sentCount++;
      } catch (err) {
        console.error(`[媒体解析] 发送 ${file.type} 失败:`, err);
      }
    }
  }

  if (sentCount === 0) {
    console.warn('[媒体解析] 未能发送任何媒体文件');
    await event.reply('媒体解析失败，无法获取媒体文件').catch(() => {});
  } else {
    console.log(`[媒体解析] 成功发送 ${sentCount} 个媒体文件`);
  }

  return true;
}

// ── 主函数 ──────────────────────────────────────────────────
async function main(): Promise<void> {
  const qqConfig = appConfig.qqbot;
  const aiConfig = appConfig.ai;

  // ── 打印启动信息 ─────────────────────────────────────────
  console.log('');
  console.log('╔══════════════════════════════════════════╗');
  console.log('║         HIKARI AI · QQ Bot               ║');
  console.log('║         DeepSeek V4 驱动                  ║');
  console.log('╚══════════════════════════════════════════╝');
  console.log('');
  console.log(`🤖 模型:        ${aiConfig.model}`);
  console.log(`🧠 思考模式:    ${aiConfig.thinkingMode === 'disabled' ? '禁用（更快更省）' : '启用（推理更强）'}`);
  console.log(`🎯 推理强度:    ${aiConfig.reasoningEffort}`);
  console.log(`🌡️  Temperature: ${aiConfig.temperature}${aiConfig.thinkingMode !== 'disabled' ? '（思考模式下不生效）' : ''}`);
  console.log(`📚 历史记录:    ${aiConfig.historySize} 轮`);
  console.log(`📝 Markdown:    ✅ QQ 群聊+私聊渲染（失败自动降级纯文本）`);
  console.log(`🔗 媒体解析:    ✅ NEO 协同（bilibili/douyin/xhs/twitter/...）`);

  // 初始化 AI 聊天引擎（DeepSeek V4）
  const aiChat = new AIChat(aiConfig);

  // 创建 QQ Bot（WebSocket 模式）
  const bot = new Bot({
    appid: qqConfig.appid,
    secret: qqConfig.secret,
    mode: ReceiverMode.WEBSOCKET,
    intents: qqConfig.intents as Intent[],
    sandbox: qqConfig.sandbox ?? true,
    logLevel: (qqConfig.logLevel ?? 'info') as any,
    removeAt: qqConfig.removeAt ?? true,
    maxRetry: qqConfig.maxRetry ?? 10,
  });

  // ── 事件绑定 ────────────────────────────────────────────

  // 群聊消息（@机器人）
  bot.on('message.group', async (event: any) => {
    if (!aiConfig.enableGroupChat) return;

    const rawMessage = event.raw_message || event.content || '';
    if (!rawMessage.trim()) return;

    // ── 测试指令：123456 → 发送测试图片 ────────────────
    if (rawMessage.trim() === '123456') {
      console.log(`\n[群:${event.group_id}] ⏺️  测试指令，发送图片`);
      try {
        await event.reply(segment.image(testImagePath));
        console.log(`[群:${event.group_id}] ✅ 已发送测试图片`);
      } catch (err) {
        console.error(`[群:${event.group_id}] 图片发送失败:`, err);
      }
      return;
    }

    // ── 测试指令：视频测试 → 发送测试视频 ──────────
    if (rawMessage.trim() === '视频测试') {
      console.log(`\n[群:${event.group_id}] ⏺️  测试指令，发送视频`);
      try {
        await event.reply(segment.video(testVideoPath));
        console.log(`[群:${event.group_id}] ✅ 已发送测试视频`);
      } catch (err) {
        console.error(`[群:${event.group_id}] 视频发送失败:`, err);
      }
      return;
    }

    // ── 媒体链接 → 转发到 NEO 解析 ─────────────────
    if (await handleMediaLinks(event, rawMessage)) return;

    const sessionId = `group_${event.group_id}_user_${event.user_id}`;

    console.log(`\n[群:${event.group_id}] ${event.user_id}: ${rawMessage}`);

    try {
      const reply = await aiChat.chat(sessionId, rawMessage, event.user_id);
      await replyWithMarkdown(event, reply, false);
      console.log(`[群:${event.group_id}] ✅ 已回复 ${event.user_id}`);
    } catch (err) {
      console.error(`[群:${event.group_id}] 回复失败:`, err);
    }
  });

  // 好友私聊
  bot.on('message.private.friend', async (event: any) => {
    if (!aiConfig.enablePrivateChat) return;

    const rawMessage = event.raw_message || event.content || '';
    if (!rawMessage.trim()) return;

    // ── 测试指令：123456 → 发送测试图片 ────────────────
    if (rawMessage.trim() === '123456') {
      console.log(`\n[私聊] ⏺️  测试指令，发送图片`);
      try {
        await event.reply(segment.image(testImagePath));
        console.log(`[私聊] ✅ 已发送测试图片`);
      } catch (err) {
        console.error(`[私聊] 图片发送失败:`, err);
      }
      return;
    }

    // ── 测试指令：视频测试 → 发送测试视频 ──────────
    if (rawMessage.trim() === '视频测试') {
      console.log(`\n[私聊] ⏺️  测试指令，发送视频`);
      try {
        await event.reply(segment.video(testVideoPath));
        console.log(`[私聊] ✅ 已发送测试视频`);
      } catch (err) {
        console.error(`[私聊] 视频发送失败:`, err);
      }
      return;
    }

    // ── 媒体链接 → 转发到 NEO 解析 ─────────────────
    if (await handleMediaLinks(event, rawMessage)) return;

    const sessionId = `private_${event.user_id}`;

    console.log(`\n[私聊] ${event.user_id}: ${rawMessage}`);

    try {
      const reply = await aiChat.chat(sessionId, rawMessage, event.user_id);
      await replyWithMarkdown(event, reply, true);
      console.log(`[私聊] ✅ 已回复 ${event.user_id}`);
    } catch (err) {
      console.error(`[私聊] 回复失败:`, err);
    }
  });

  // 频道私信
  bot.on('message.private.direct', async (event: any) => {
    if (!aiConfig.enablePrivateChat) return;

    const rawMessage = event.raw_message || event.content || '';
    if (!rawMessage.trim()) return;

    // ── 媒体链接 → 转发到 NEO 解析 ─────────────────
    if (await handleMediaLinks(event, rawMessage)) return;

    const sessionId = `direct_${event.user_id}`;

    console.log(`\n[频道私信] ${event.user_id}: ${rawMessage}`);

    try {
      const reply = await aiChat.chat(sessionId, rawMessage, event.user_id);
      // 频道私信使用 Markdown
      await replyWithMarkdown(event, reply, true);
      console.log(`[频道私信] ✅ 已回复 ${event.user_id}`);
    } catch (err) {
      console.error(`[频道私信] 回复失败:`, err);
    }
  });

  // ── 生命周期事件 ────────────────────────────────────────

  bot.on('ready', () => {
    console.log(`\n✅ QQ Bot 已启动! AppID: ${qqConfig.appid}`);
    if (qqConfig.sandbox) {
      console.log('🏖️  当前为沙箱环境');
    }
  });

  bot.on('disconnect', () => {
    console.warn('\n⚠️  Bot 已断开连接，正在尝试重连...');
  });

  bot.on('error', (err: Error) => {
    console.error('\n❌ 连接错误:', err.message);
  });

  // ── 启动 ────────────────────────────────────────────────
  try {
    await bot.start();
  } catch (err) {
    console.error('❌ Bot 启动失败:', err);
    process.exit(1);
  }
}

// ── 启动 ──────────────────────────────────────────────────
main().catch((err) => {
  console.error('❌ 未捕获的异常:', err);
  process.exit(1);
});
