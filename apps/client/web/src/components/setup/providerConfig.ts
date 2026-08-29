export interface ProviderConfig {
  key: string;
  label: string;
  protocol: string;
  envVar: string;
  baseUrl: string;
  defaultModel: string;
}

export const PROVIDERS: ProviderConfig[] = [
  { key: 'openai', label: 'OpenAI', protocol: 'openai', envVar: 'OPENAI_API_KEY', baseUrl: 'https://api.openai.com/v1', defaultModel: 'gpt-4o-mini' },
  { key: 'gemini', label: 'Gemini', protocol: 'gemini', envVar: 'GEMINI_API_KEY', baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai', defaultModel: 'gemini-1.5-flash' },
  { key: 'anthropic', label: 'Anthropic', protocol: 'anthropic', envVar: 'ANTHROPIC_API_KEY', baseUrl: 'https://api.anthropic.com/v1', defaultModel: 'claude-3-5-sonnet-latest' },
  { key: 'deepseek', label: 'DeepSeek', protocol: 'deepseek', envVar: 'DEEPSEEK_API_KEY', baseUrl: 'https://api.deepseek.com/v1', defaultModel: 'deepseek-chat' },
];

export function getProvider(key: string): ProviderConfig {
  return PROVIDERS.find((p) => p.key === key) ?? PROVIDERS[0];
}

export function buildTestPayload(provider: ProviderConfig, apiKey: string, model: string) {
  return {
    name: `wizard-${provider.protocol}`,
    protocol: provider.protocol,
    apiSurface: 'chat_completions' as const,
    baseUrl: provider.baseUrl,
    apiKey,
    models: model ? [model] : [],
    capabilityChecks: [] as string[],
    useSavedSecret: false,
  };
}

/**
 * Secrets persisted to the OS keystore after a successful connection test.
 * Always pairs the provider key with LITELLM_MODEL (provider-prefixed) so the
 * engine routes to the tested channel instead of a default/empty model.
 */
export function deriveWizardSecrets(provider: ProviderConfig, apiKey: string, model: string): Record<string, string> {
  const secrets: Record<string, string> = {};
  if (apiKey) secrets[provider.envVar] = apiKey;
  if (model) secrets.LITELLM_MODEL = `${provider.protocol}/${model}`;
  return secrets;
}
