export interface DsaBridge {
  hasSecrets?: () => Promise<boolean>;
  saveSecrets?: (secrets: Record<string, string>) => Promise<unknown>;
  restartBackend?: () => Promise<unknown>;
}

/**
 * The setup wizard only appears inside the desktop shell (where `window.dsa`
 * exposes secret storage) and only on first run (no saved secrets yet).
 */
export async function shouldShowWizard(dsa?: DsaBridge): Promise<boolean> {
  if (!dsa || typeof dsa.hasSecrets !== 'function') return false;
  try {
    return !(await dsa.hasSecrets());
  } catch {
    return false;
  }
}
