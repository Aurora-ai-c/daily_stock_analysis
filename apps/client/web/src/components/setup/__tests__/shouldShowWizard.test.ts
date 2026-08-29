import { describe, it, expect } from 'vitest';
import { shouldShowWizard, type DsaBridge } from '../shouldShowWizard';

describe('setup wizard gate', () => {
  it('does not show outside the desktop shell (no dsa bridge)', async () => {
    expect(await shouldShowWizard(undefined)).toBe(false);
  });

  it('does not show when secrets already exist', async () => {
    const dsa: DsaBridge = { hasSecrets: async () => true };
    expect(await shouldShowWizard(dsa)).toBe(false);
  });

  it('shows on first run when the desktop shell reports no secrets', async () => {
    const dsa: DsaBridge = { hasSecrets: async () => false };
    expect(await shouldShowWizard(dsa)).toBe(true);
  });

  it('treats a failing hasSecrets probe as "do not show"', async () => {
    const dsa: DsaBridge = { hasSecrets: async () => { throw new Error('boom'); } };
    expect(await shouldShowWizard(dsa)).toBe(false);
  });
});
