import OpenAI from 'openai';
import type { AIConfig } from './types.js';

interface ChatHistory {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

interface SessionEntry {
  history: ChatHistory[];
  lastActive: number;
  turnCount: number;       // 对话轮次计数（用于日志）
}

export class AIChat {
  private client: OpenAI;
  private config: AIConfig;
  private sessions: Map<string, SessionEntry> = new Map();

  constructor(config: AIConfig) {
    this.config = config;

    console.log(`[AI] 初始化 DeepSeek V4 客户端`);
    console.log(`[AI] 模型: ${config.model}`);
    console.log(`[AI] 思考模式: ${config.thinkingMode === 'disabled' ? '禁用（更快更省）' : '启用（数学/代码更准）'}`);
    console.log(`[AI] 推理强度: ${config.reasoningEffort}`);
    console.log(`[AI] Temperature: ${config.temperature}（思考模式下不生效）`);

    this.client = new OpenAI({
      baseURL: config.baseURL,
      apiKey: config.apiKey,
    });
  }

  /**
   * 向 DeepSeek V4 发送消息并获取回复
   *
   * @param sessionId - 会话 ID，用于维护对话历史
   * @param message   - 用户消息
   * @param userId    - 用户标识（可选，传递给 DeepSeek V4 的 user_id 参数，
   *                    用于 KVCache 隔离和内容安全）
   */
  async chat(sessionId: string, message: string, userId?: string): Promise<string> {
    const session = this.getOrCreateSession(sessionId);

    // 添加用户消息到历史
    session.history.push({ role: 'user', content: message });
    session.turnCount++;

    try {
      // ── 构建 DeepSeek V4 请求 ──────────────────────────────
      //
      // 关键特性：
      //   - thinking.mode: "enabled" → 启用推理链（数学/代码任务更准）
      //                     "disabled" → 禁用（普通对话更快更省）
      //   - 思考模式下 temperature / top_p 被忽略（API 静默接受但不生效）
      //   - frequency_penalty / presence_penalty 已弃用，传了也无效
      //   - user_id 可用于会话隔离与内容安全（仅 [a-zA-Z0-9\-_]）
      //
      // 参考: https://api-docs.deepseek.com/api/create-chat-completion/

      // ── 构建请求 ────────────────────────────────────────────
      // DeepSeek V4 使用 OpenAI 兼容接口，支持额外的 thinking 参数
      // OpenAI SDK v6 允许通过 as any 透传未知字段
      const response = await this.client.chat.completions.create({
        model: this.config.model,
        messages: session.history,
        max_tokens: this.config.maxTokens,
        temperature: this.config.temperature,
        top_p: this.config.topP,
        thinking: { type: this.config.thinkingMode },
      } as OpenAI.Chat.Completions.ChatCompletionCreateParamsNonStreaming & {
        thinking: { type: string };
      });

      // ── 处理响应 ──────────────────────────────────────────
      const choice = response.choices[0];

      // reasoning_content 是 DeepSeek V4 的推理链（思考模式下有值）
      // eslint-disable-next-line @typescript-eslint/no-unused-vars
      const reasoningContent = (choice.message as any).reasoning_content;

      const reply = choice.message?.content || '抱歉，我没有理解你的问题。';

      // 添加助手回复到历史
      session.history.push({ role: 'assistant', content: reply });

      // 修剪历史（保留 system prompt + 最近 N 轮对话）
      this.trimHistory(session);

      session.lastActive = Date.now();

      // ── 日志 ──────────────────────────────────────────────
      const usage = response.usage;
      if (usage) {
        const u = usage as any;
        const cacheHit = u.prompt_cache_hit_tokens ?? 0;
        const cacheMiss = u.prompt_cache_miss_tokens ?? 0;
        const reasoning = u.completion_tokens_details?.reasoning_tokens ?? 0;
        const cacheRate = (cacheHit + cacheMiss) > 0
          ? ((cacheHit / (cacheHit + cacheMiss)) * 100).toFixed(1)
          : '-';

        console.log(
          `[AI] 第 ${session.turnCount} 轮 | ` +
          `token: ${usage.prompt_tokens}→${usage.completion_tokens} | ` +
          `推理: ${reasoning} | ` +
          `缓存命中率: ${cacheRate}%`
        );
      }

      return reply;
    } catch (error) {
      console.error('[AI] DeepSeek V4 API 请求失败:', error);
      // 恢复历史：移除刚添加的用户消息
      session.history.pop();
      session.turnCount--;
      return '抱歉，我现在有点卡顿，请稍后再试。';
    }
  }

  /**
   * 清除指定会话的历史记录
   */
  clearSession(sessionId: string): void {
    this.sessions.delete(sessionId);
    console.log(`[AI] 已清除会话: ${sessionId}`);
  }

  /**
   * 获取或创建会话
   */
  private getOrCreateSession(sessionId: string): SessionEntry {
    let session = this.sessions.get(sessionId);
    if (!session) {
      session = {
        history: [{ role: 'system', content: this.config.systemPrompt }],
        lastActive: Date.now(),
        turnCount: 0,
      };
      this.sessions.set(sessionId, session);
    }
    return session;
  }

  /**
   * 修剪历史记录，保留 system prompt + 最近 N 轮对话。
   *
   * DeepSeek V4 的混合注意力机制会偏向上下文首尾两端，
   * 因此保留 system prompt（开头）和最近的对话（结尾）是最优策略。
   */
  private trimHistory(session: SessionEntry): void {
    // historySize = N，实际容量 = 1 (system) + N * 2 (user+assistant 各 N 轮)
    const maxSize = 1 + this.config.historySize * 2;
    if (session.history.length > maxSize) {
      const systemPrompt = session.history[0];
      // 保留最近的 N 轮对话
      const recentMessages = session.history.slice(-this.config.historySize * 2);
      session.history = [systemPrompt, ...recentMessages];
    }
  }
}
