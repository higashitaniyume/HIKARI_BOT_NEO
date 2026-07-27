import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bot, ReceiverMode, segment } from 'qq-official-bot';
import type { Intent } from 'qq-official-bot';
import type { AppConfig } from './types.js';
import { AIChat } from './ai.js';
import { stripMarkdown } from './utils.js';

// ═══════════════════════════════════════════════════════════════
//  日志工具
// ═══════════════════════════════════════════════════════════════

function ts(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false });
}

function rid(): string {
  return Date.now().toString(36).slice(-4) + Math.random().toString(36).slice(2, 5);
}

function fmtBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`;
}

function fmtDuration(ms: number): string {
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60_000)}m${Math.floor((ms % 60_000) / 1000)}s`;
}

function eventTag(event: any, traceId: string): string {
  const src = event.group_id ? `群[${event.group_id}]` : event.user_id ? `私聊[${event.user_id}]` : '未知';
  return `[${ts()}] [${src}] [${traceId}]`;
}

// ═══════════════════════════════════════════════════════════════
//  配置加载
// ═══════════════════════════════════════════════════════════════

const __dirname = dirname(fileURLToPath(import.meta.url));
const configPath = resolve(__dirname, '..', 'config.json');
let appConfig: AppConfig;

try {
  const raw = readFileSync(configPath, 'utf-8');
  appConfig = JSON.parse(raw) as AppConfig;
  validateConfig(appConfig);
} catch (err) {
  console.error(`[${ts()}] [启动] ❌ 无法加载配置文件 config.json:`, err);
  process.exit(1);
}

function validateConfig(config: AppConfig): void {
  const { qqbot, ai } = config;
  if (!qqbot.appid || qqbot.appid === 'YOUR_APPID') {
    console.error(`[${ts()}] [启动] ❌ 请先配置 config.json 中的 qqbot.appid`);
    process.exit(1);
  }
  if (!qqbot.secret || qqbot.secret === 'YOUR_APPSECRET') {
    console.error(`[${ts()}] [启动] ❌ 请先配置 config.json 中的 qqbot.secret`);
    process.exit(1);
  }
  if (!ai.apiKey || ai.apiKey.startsWith('YOUR_')) {
    console.error(`[${ts()}] [启动] ❌ 请先配置 config.json 中的 ai.apiKey（DeepSeek API Key）`);
    process.exit(1);
  }
}

// ═══════════════════════════════════════════════════════════════
//  回复辅助
// ═══════════════════════════════════════════════════════════════

// 测试指令用媒体路径（仅宿主机开发调试有效，容器内不存在）
const testImagePath = 'C:/Users/shimikoi/OneDrive/图片/1785129005759.png';
const testVideoPath = 'C:/PublicFiles/Downloads/VM_Download/0.mp4';

async function replyWithMarkdown(event: any, text: string, isPrivate: boolean): Promise<void> {
  if (isPrivate && /@\S+/.test(text)) {
    await event.reply(stripMarkdown(text));
    return;
  }

  try {
    await event.reply(segment.markdown(text));
  } catch {
    console.warn(`[${ts()}] [回复] ⚠️  Markdown 发送失败，降级为纯文本`);
    const plainText = stripMarkdown(text);
    await event.reply(plainText);
  }
}

// ═══════════════════════════════════════════════════════════════
//  跨机器人媒体解析转发
// ═══════════════════════════════════════════════════════════════

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

async function parseMediaViaNeo(
  text: string,
  tag: string,
): Promise<NeoParseResponse | null> {
  const startedAt = Date.now();
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30 * 60 * 1000);

    console.log(`${tag} → NEO ${NEO_API_URL} (等待解析下载...)`);
    const response = await fetch(NEO_API_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: text }),
      signal: controller.signal,
    });
    clearTimeout(timeout);

    const elapsed = Date.now() - startedAt;
    console.log(`${tag} ← NEO HTTP ${response.status} (${fmtDuration(elapsed)})`);

    if (!response.ok) {
      console.warn(`${tag} ⚠️  NEO 返回非 200: HTTP ${response.status}`);
      return null;
    }
    return await response.json() as NeoParseResponse;
  } catch (err) {
    const elapsed = Date.now() - startedAt;
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes('abort') || msg.includes('timeout')) {
      console.warn(`${tag} ⚠️  NEO 请求超时 (${fmtDuration(elapsed)})`);
    } else {
      console.warn(`${tag} ⚠️  NEO 请求失败 (${fmtDuration(elapsed)}): ${msg}`);
    }
    return null;
  }
}

async function handleMediaLinks(event: any, text: string, tag: string): Promise<boolean> {
  if (!containsMediaLink(text)) return false;

  const startedAt = Date.now();
  let matchedPlatforms = '';
  for (const pattern of SUPPORTED_LINK_PATTERNS) {
    if (text.toLowerCase().includes(pattern)) {
      matchedPlatforms += (matchedPlatforms ? ', ' : '') + pattern;
    }
  }
  console.log(`${tag} 🔗 检测到媒体链接 [${matchedPlatforms}]`);

  const result = await parseMediaViaNeo(text, tag);

  if (!result?.success || !result.results?.length) {
    const elapsed = Date.now() - startedAt;
    console.warn(`${tag} ⚠️  NEO 解析结果为失败或无结果 (${fmtDuration(elapsed)})`);
    if (result?.error) console.warn(`${tag}   原因: ${result.error}`);
    await event.reply('媒体解析失败，请稍后重试').catch(() => {});
    return true;
  }

  let sentCount = 0;
  let totalBytes = 0;
  const platformSummary: string[] = [];

  for (const item of result.results) {
    if (!item.success) {
      console.warn(`${tag}   └─ ${item.platform}: ❌ ${item.error || '解析失败'}`);
      continue;
    }
    if (!item.files?.length) {
      console.log(`${tag}   └─ ${item.platform}: 无媒体文件（可能为远程链接）`);
      continue;
    }

    const fileSummary = item.files.map(f => `${f.type}[${fmtBytes(f.size)}]`).join(', ');
    console.log(`${tag}   └─ ${item.platform}「${item.title || '无标题'}」: ${fileSummary}`);

    for (const file of item.files) {
      try {
        if (file.type === 'video') {
          await event.reply(segment.video(file.path));
        } else {
          await event.reply(segment.image(file.path));
        }
        sentCount++;
        totalBytes += file.size;
      } catch (err) {
        console.error(`${tag}   └─ ⚠️  发送 ${file.type} 失败 (${file.path}):`, err instanceof Error ? err.message : err);
      }
    }
    platformSummary.push(`${item.platform}(${item.files.length}个)`);
  }

  const elapsed = Date.now() - startedAt;
  if (sentCount === 0) {
    console.warn(`${tag} ⚠️  未能发送任何媒体文件 (${fmtDuration(elapsed)})`);
    await event.reply('媒体解析失败，无法获取媒体文件').catch(() => {});
  } else {
    console.log(`${tag} ✅ 媒体解析完成: ${platformSummary.join(', ')} | 共 ${sentCount} 个文件 ${fmtBytes(totalBytes)} | 耗时 ${fmtDuration(elapsed)}`);
  }

  return true;
}

// ═══════════════════════════════════════════════════════════════
//  主函数
// ═══════════════════════════════════════════════════════════════

async function main(): Promise<void> {
  const qqConfig = appConfig.qqbot;
  const aiConfig = appConfig.ai;

  // ── 启动信息 ─────────────────────────────────────────────
  console.log('');
  console.log('╔══════════════════════════════════════════════╗');
  console.log('║           HIKARI AI · QQ Bot                ║');
  console.log('╚══════════════════════════════════════════════╝');
  console.log('');
  console.log(`  [${ts()}] 🤖 模型:           ${aiConfig.model}`);
  console.log(`  [${ts()}] 🧠 思考模式:       ${aiConfig.thinkingMode === 'disabled' ? '禁用' : '启用'}`);
  console.log(`  [${ts()}] 🎯 推理强度:       ${aiConfig.reasoningEffort}`);
  console.log(`  [${ts()}] 🌡️  Temperature:   ${aiConfig.temperature}`);
  console.log(`  [${ts()}] 📚 历史记录:       ${aiConfig.historySize} 轮`);
  console.log(`  [${ts()}] 👥 群聊:           ${aiConfig.enableGroupChat ? '✅' : '❌'}`);
  console.log(`  [${ts()}] 💬 私聊:           ${aiConfig.enablePrivateChat ? '✅' : '❌'}`);
  console.log(`  [${ts()}] 🔗 媒体解析:       ✅ NEO 协同 (${SUPPORTED_LINK_PATTERNS.length} 个平台)`);
  console.log(`  [${ts()}] 🌐 NEO API:        ${NEO_API_URL}`);
  console.log('');

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

  // ── 群聊消息（@机器人） ─────────────────────────────────
  bot.on('message.group', async (event: any) => {
    const traceId = rid();
    const tag = `${eventTag(event, traceId)}`;

    if (!aiConfig.enableGroupChat) {
      console.log(`${tag} 🔇 群聊已禁用，忽略消息`);
      return;
    }

    const rawMessage = (event.raw_message || event.content || '').trim();
    if (!rawMessage) {
      console.log(`${tag} ⏭️  空消息，跳过`);
      return;
    }

    const msgLen = rawMessage.length;
    const msgPreview = rawMessage.length > 80 ? rawMessage.slice(0, 80) + '...' : rawMessage;
    console.log(`\n${tag} 📩 收到群消息 ${event.group_id} | ${event.user_id} | ${msgLen}字符`);
    console.log(`${tag}   内容: ${msgPreview}`);

    const overallStart = Date.now();

    // 测试指令
    if (rawMessage === '123456') {
      console.log(`${tag} 🧪 测试指令 → 发送测试图片`);
      try {
        await event.reply(segment.image(testImagePath));
        console.log(`${tag} ✅ 测试图片已发送`);
      } catch (err) {
        console.error(`${tag} ❌ 测试图片发送失败:`, err instanceof Error ? err.message : err);
      }
      return;
    }

    if (rawMessage === '视频测试') {
      console.log(`${tag} 🧪 测试指令 → 发送测试视频`);
      try {
        await event.reply(segment.video(testVideoPath));
        console.log(`${tag} ✅ 测试视频已发送`);
      } catch (err) {
        console.error(`${tag} ❌ 测试视频发送失败:`, err instanceof Error ? err.message : err);
      }
      return;
    }

    // 媒体链接
    if (await handleMediaLinks(event, rawMessage, tag)) {
      console.log(`${tag} 🏁 总耗时: ${fmtDuration(Date.now() - overallStart)}`);
      return;
    }

    // AI 聊天
    const sessionId = `group_${event.group_id}_user_${event.user_id}`;
    const aiStart = Date.now();

    try {
      console.log(`${tag} 🤖 请求 AI 回复...`);
      const reply = await aiChat.chat(sessionId, rawMessage, event.user_id);
      const aiElapsed = Date.now() - aiStart;
      const replyLen = reply.length;
      console.log(`${tag} ✅ AI 回复完成 (${fmtDuration(aiElapsed)}, ${replyLen}字符)`);

      await replyWithMarkdown(event, reply, false);
      const totalElapsed = Date.now() - overallStart;
      console.log(`${tag} ✅ 已回复用户 | 总耗时 ${fmtDuration(totalElapsed)}`);
    } catch (err) {
      console.error(`${tag} ❌ AI 回复失败:`, err instanceof Error ? err.message : err);
    }
  });

  // ── 好友私聊 ────────────────────────────────────────────
  bot.on('message.private.friend', async (event: any) => {
    const traceId = rid();
    const tag = `${eventTag(event, traceId)}`;

    if (!aiConfig.enablePrivateChat) {
      console.log(`${tag} 🔇 私聊已禁用，忽略消息`);
      return;
    }

    const rawMessage = (event.raw_message || event.content || '').trim();
    if (!rawMessage) {
      console.log(`${tag} ⏭️  空消息，跳过`);
      return;
    }

    const msgLen = rawMessage.length;
    const msgPreview = rawMessage.length > 80 ? rawMessage.slice(0, 80) + '...' : rawMessage;
    console.log(`\n${tag} 📩 收到私聊 | ${event.user_id} | ${msgLen}字符`);
    console.log(`${tag}   内容: ${msgPreview}`);

    const overallStart = Date.now();

    // 测试指令
    if (rawMessage === '123456') {
      console.log(`${tag} 🧪 测试指令 → 发送测试图片`);
      try {
        await event.reply(segment.image(testImagePath));
        console.log(`${tag} ✅ 测试图片已发送`);
      } catch (err) {
        console.error(`${tag} ❌ 测试图片发送失败:`, err instanceof Error ? err.message : err);
      }
      return;
    }

    if (rawMessage === '视频测试') {
      console.log(`${tag} 🧪 测试指令 → 发送测试视频`);
      try {
        await event.reply(segment.video(testVideoPath));
        console.log(`${tag} ✅ 测试视频已发送`);
      } catch (err) {
        console.error(`${tag} ❌ 测试视频发送失败:`, err instanceof Error ? err.message : err);
      }
      return;
    }

    // 媒体链接
    if (await handleMediaLinks(event, rawMessage, tag)) {
      console.log(`${tag} 🏁 总耗时: ${fmtDuration(Date.now() - overallStart)}`);
      return;
    }

    // AI 聊天
    const sessionId = `private_${event.user_id}`;
    const aiStart = Date.now();

    try {
      console.log(`${tag} 🤖 请求 AI 回复...`);
      const reply = await aiChat.chat(sessionId, rawMessage, event.user_id);
      const aiElapsed = Date.now() - aiStart;
      const replyLen = reply.length;
      console.log(`${tag} ✅ AI 回复完成 (${fmtDuration(aiElapsed)}, ${replyLen}字符)`);

      await replyWithMarkdown(event, reply, true);
      const totalElapsed = Date.now() - overallStart;
      console.log(`${tag} ✅ 已回复用户 | 总耗时 ${fmtDuration(totalElapsed)}`);
    } catch (err) {
      console.error(`${tag} ❌ AI 回复失败:`, err instanceof Error ? err.message : err);
    }
  });

  // ── 频道私信 ────────────────────────────────────────────
  bot.on('message.private.direct', async (event: any) => {
    const traceId = rid();
    const tag = `${eventTag(event, traceId)}`;

    if (!aiConfig.enablePrivateChat) {
      console.log(`${tag} 🔇 私聊已禁用，忽略消息`);
      return;
    }

    const rawMessage = (event.raw_message || event.content || '').trim();
    if (!rawMessage) {
      console.log(`${tag} ⏭️  空消息，跳过`);
      return;
    }

    const msgLen = rawMessage.length;
    const msgPreview = rawMessage.length > 80 ? rawMessage.slice(0, 80) + '...' : rawMessage;
    console.log(`\n${tag} 📩 收到频道私信 | ${event.user_id} | ${msgLen}字符`);
    console.log(`${tag}   内容: ${msgPreview}`);

    const overallStart = Date.now();

    // 媒体链接
    if (await handleMediaLinks(event, rawMessage, tag)) {
      console.log(`${tag} 🏁 总耗时: ${fmtDuration(Date.now() - overallStart)}`);
      return;
    }

    // AI 聊天
    const sessionId = `direct_${event.user_id}`;
    const aiStart = Date.now();

    try {
      console.log(`${tag} 🤖 请求 AI 回复...`);
      const reply = await aiChat.chat(sessionId, rawMessage, event.user_id);
      const aiElapsed = Date.now() - aiStart;
      const replyLen = reply.length;
      console.log(`${tag} ✅ AI 回复完成 (${fmtDuration(aiElapsed)}, ${replyLen}字符)`);

      await replyWithMarkdown(event, reply, true);
      const totalElapsed = Date.now() - overallStart;
      console.log(`${tag} ✅ 已回复用户 | 总耗时 ${fmtDuration(totalElapsed)}`);
    } catch (err) {
      console.error(`${tag} ❌ AI 回复失败:`, err instanceof Error ? err.message : err);
    }
  });

  // ── 生命周期事件 ────────────────────────────────────────

  bot.on('ready', () => {
    console.log(`\n[${ts()}] [生命周期] ✅ Bot 已就绪`);
    console.log(`  AppID:    ${qqConfig.appid}`);
    console.log(`  沙箱模式: ${qqConfig.sandbox ? '🏖️ 是' : '🏭 否'}`);
    console.log(`  连接方式: WebSocket`);
    console.log(`  意图:     ${(qqConfig.intents || []).join(', ')}`);
    console.log('');
  });

  bot.on('disconnect', () => {
    console.warn(`\n[${ts()}] [生命周期] ⚠️  Bot 已断开连接，正在尝试重连...`);
  });

  bot.on('error', (err: Error) => {
    console.error(`\n[${ts()}] [生命周期] ❌ 连接错误: ${err.message}`);
  });

  // ── 启动 ────────────────────────────────────────────────
  try {
    await bot.start();
  } catch (err) {
    console.error(`[${ts()}] [启动] ❌ Bot 启动失败:`, err);
    process.exit(1);
  }
}

// ── 启动 ──────────────────────────────────────────────────
main().catch((err) => {
  console.error(`[${ts()}] [启动] ❌ 未捕获的异常:`, err);
  process.exit(1);
});
