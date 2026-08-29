import { describe, it, expect } from 'vitest';
import {
  PROVIDERS,
  getProvider,
  buildTestPayload,
  deriveWizardSecrets,
} from '../providerConfig';

describe('provider -> secrets mapping', () => {
  it('derives the key env var AND LITELLM_MODEL for every provider', () => {
    for (const p of PROVIDERS) {
      const secrets = deriveWizardSecrets(p, 'sk-test', p.defaultModel);
      expect(secrets[p.envVar]).toBe('sk-test');
      expect(secrets.LITELLM_MODEL).toBe(`${p.protocol}/${p.defaultModel}`);
    }
  });

  it('deepseek yields DEEPSEEK_API_KEY + deepseek-prefixed LITELLM_MODEL', () => {
    const deepseek = getProvider('deepseek');
    const secrets = deriveWizardSecrets(deepseek, 'sk-d', 'deepseek-chat');
    expect(secrets).toEqual({
      DEEPSEEK_API_KEY: 'sk-d',
      LITELLM_MODEL: 'deepseek/deepseek-chat',
    });
  });

  it('omits LITELLM_MODEL when no model is supplied', () => {
    const openai = getProvider('openai');
    const secrets = deriveWizardSecrets(openai, 'sk-o', '');
    expect(secrets).toEqual({ OPENAI_API_KEY: 'sk-o' });
  });
});

describe('test payload and saved secrets share one channel definition', () => {
  it('uses the same provider protocol for both the test call and LITELLM_MODEL', () => {
    for (const p of PROVIDERS) {
      const payload = buildTestPayload(p, 'sk', p.defaultModel);
      const secrets = deriveWizardSecrets(p, 'sk', p.defaultModel);
      const litellmPrefix = (secrets.LITELLM_MODEL ?? '').split('/')[0];
      expect(payload.protocol).toBe(p.protocol);
      expect(payload.baseUrl).toBe(p.baseUrl);
      expect(litellmPrefix).toBe(p.protocol);
    }
  });

  it('flags the test as unsaved (useSavedSecret=false)', () => {
    const payload = buildTestPayload(getProvider('gemini'), 'sk', 'gemini-1.5-flash');
    expect(payload.useSavedSecret).toBe(false);
    expect(payload.apiKey).toBe('sk');
    expect(payload.models).toEqual(['gemini-1.5-flash']);
  });
});
