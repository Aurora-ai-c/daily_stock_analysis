const fs = require('fs');
const path = require('path');

const STORE_NAME = '.keystore';

function storePath(dataDir) {
  return path.join(dataDir, STORE_NAME);
}

// Injected crypto provider (used by tests and by main.js once Electron is loaded).
let injectedSafeStorage = undefined;
function setSafeStorage(provider) {
  injectedSafeStorage = provider;
}
function resolveSafeStorage() {
  if (injectedSafeStorage !== undefined) return injectedSafeStorage;
  try {
    // eslint-disable-next-line global-require
    const { safeStorage } = require('electron');
    injectedSafeStorage = safeStorage || null;
  } catch {
    injectedSafeStorage = null;
  }
  return injectedSafeStorage;
}

function saveSecrets(dataDir, secrets) {
  fs.mkdirSync(dataDir, { recursive: true });
  const plain = JSON.stringify(secrets);
  const ss = resolveSafeStorage();
  let payload;
  if (ss && typeof ss.isEncryptionAvailable === 'function' && ss.isEncryptionAvailable()) {
    const buf = ss.encryptString(plain);
    payload = { __enc: 'safe-storage', data: buf.toString('base64') };
  } else {
    payload = { __enc: 'plain', data: plain };
  }
  fs.writeFileSync(storePath(dataDir), JSON.stringify(payload), { mode: 0o600 });
}

function loadSecrets(dataDir) {
  const p = storePath(dataDir);
  if (!fs.existsSync(p)) return {};
  const payload = JSON.parse(fs.readFileSync(p, 'utf8'));
  if (payload.__enc === 'safe-storage') {
    const ss = resolveSafeStorage();
    if (!ss || !ss.isEncryptionAvailable()) {
      console.warn('[keyVault] store is encrypted but safeStorage unavailable');
      return {};
    }
    const buf = Buffer.from(payload.data, 'base64');
    return JSON.parse(ss.decryptString(buf).toString('utf8'));
  }
  if (payload.__enc === 'dpapi') {
    console.warn('[keyVault] legacy dpapi store found, ignoring');
    return {};
  }
  return JSON.parse(payload.data);
}

function hasSecrets(dataDir) {
  const p = storePath(dataDir);
  if (!fs.existsSync(p)) return false;
  try {
    return Object.keys(loadSecrets(dataDir)).length > 0;
  } catch {
    return false;
  }
}

function clearSecrets(dataDir) {
  const p = storePath(dataDir);
  if (fs.existsSync(p)) fs.unlinkSync(p);
}

module.exports = {
  saveSecrets,
  loadSecrets,
  hasSecrets,
  clearSecrets,
  storePath,
  setSafeStorage,
  resolveSafeStorage,
};
