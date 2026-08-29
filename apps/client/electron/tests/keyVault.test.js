const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const keyVault = require('../keyVault');

function makeSafeStorage(available) {
  return {
    isEncryptionAvailable: () => available,
    encryptString: (s) => Buffer.from(`ENC:${s}`),
    decryptString: (b) => Buffer.from(b.toString('utf8').replace('ENC:', '')),
  };
}

test('save and load round-trips secrets with safeStorage', () => {
  keyVault.setSafeStorage(makeSafeStorage(true));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsa-vault-'));
  try {
    assert.strictEqual(keyVault.hasSecrets(dir), false);
    const secrets = { OPENAI_API_KEY: 'sk-test', TUSHARE_TOKEN: 'tok-123' };
    keyVault.saveSecrets(dir, secrets);
    assert.strictEqual(keyVault.hasSecrets(dir), true);
    const loaded = keyVault.loadSecrets(dir);
    assert.deepStrictEqual(loaded, secrets);
    const raw = JSON.parse(fs.readFileSync(keyVault.storePath(dir), 'utf8'));
    assert.strictEqual(raw.__enc, 'safe-storage');
    assert.notStrictEqual(raw.data, JSON.stringify(secrets));
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('save falls back to plaintext when safeStorage unavailable', () => {
  keyVault.setSafeStorage(makeSafeStorage(false));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsa-vault-'));
  try {
    const secrets = { A: '1' };
    keyVault.saveSecrets(dir, secrets);
    const raw = JSON.parse(fs.readFileSync(keyVault.storePath(dir), 'utf8'));
    assert.strictEqual(raw.__enc, 'plain');
    assert.deepStrictEqual(keyVault.loadSecrets(dir), secrets);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('loadSecrets returns empty object when no store', () => {
  keyVault.setSafeStorage(makeSafeStorage(true));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsa-vault-'));
  try {
    assert.deepStrictEqual(keyVault.loadSecrets(dir), {});
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('legacy dpapi store is ignored', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsa-vault-'));
  try {
    fs.writeFileSync(keyVault.storePath(dir), JSON.stringify({ __enc: 'dpapi', data: 'xxx' }));
    assert.deepStrictEqual(keyVault.loadSecrets(dir), {});
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('clearSecrets removes the store', () => {
  keyVault.setSafeStorage(makeSafeStorage(true));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dsa-vault-'));
  try {
    keyVault.saveSecrets(dir, { A: '1' });
    keyVault.clearSecrets(dir);
    assert.strictEqual(keyVault.hasSecrets(dir), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});
