export interface QQBotConfig {
  appid: string;
  secret: string;
  sandbox?: boolean;
  intents: string[];
  logLevel?: string;
  removeAt?: boolean;
  maxRetry?: number;
  timeout?: number;
}

export type DeepSeekModel = 'deepseek-v4-flash' | 'deepseek-v4-pro';
export type ThinkingMode = 'disabled' | 'enabled';
export type ReasoningEffort = 'high' | 'max';

export interface AIConfig {
  provider: string;
  baseURL: string;
  apiKey: string;
  /** DeepSeek 模型名: deepseek-v4-flash 或 deepseek-v4-pro */
  model: DeepSeekModel;
  maxTokens: number;
  /** DeepSeek 建议默认 1.0。思考模式下 temperature 不生效！ */
  temperature: number;
  /** DeepSeek 建议默认 1.0。思考模式下 top_p 不生效！ */
  topP: number;
  systemPrompt: string;
  historySize: number;
  enableGroupChat: boolean;
  enablePrivateChat: boolean;
  /** 思考模式: "disabled"（默认，更快更便宜）或 "enabled"（数学/代码更准） */
  thinkingMode: ThinkingMode;
  /** 推理强度: "high" 或 "max"。仅在 thinkingMode=enabled 时生效 */
  reasoningEffort: ReasoningEffort;
}

export interface AppConfig {
  qqbot: QQBotConfig;
  ai: AIConfig;
}
