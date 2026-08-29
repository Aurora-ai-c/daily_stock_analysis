'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { buildBackendArgs, buildBackendEnvironment, buildBackendUrl } = require('../main.js');

test('buildBackendArgs uses serve-only with host/port', () => {
  assert.deepStrictEqual(
    buildBackendArgs({ host: '127.0.0.1', port: 8000 }),
    ['--serve-only', '--host', '127.0.0.1', '--port', '8000'],
  );
});

test('buildBackendEnvironment injects desktop mode and disables bots', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
    sourceEnv: { PATH: '/usr/bin' },
  });
  assert.strictEqual(env.DSA_DESKTOP_MODE, 'true');
  assert.strictEqual(env.ENV_FILE, '/data/.env');
  assert.strictEqual(env.DATABASE_PATH, '/data/db.sqlite');
  assert.strictEqual(env.LOG_DIR, '/data/logs');
  assert.strictEqual(env.WEBUI_HOST, '127.0.0.1');
  assert.strictEqual(env.WEBUI_ENABLED, 'false');
  assert.strictEqual(env.BOT_ENABLED, 'false');
  assert.strictEqual(env.DINGTALK_STREAM_ENABLED, 'false');
  assert.strictEqual(env.FEISHU_STREAM_ENABLED, 'false');
  assert.strictEqual(env.WEBUI_PORT, '8000');
  assert.strictEqual(env.PATH, '/usr/bin');
});

test('buildBackendUrl composes host and path', () => {
  assert.strictEqual(
    buildBackendUrl('127.0.0.1', 8000, '/api/health'),
    'http://127.0.0.1:8000/api/health',
  );
});

test('buildBackendEnvironment merges injected secrets', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
    sourceEnv: { PATH: '/usr/bin' },
    secrets: { OPENAI_API_KEY: 'sk-secret', TUSHARE_TOKEN: 'tok' },
  });
  assert.strictEqual(env.OPENAI_API_KEY, 'sk-secret');
  assert.strictEqual(env.TUSHARE_TOKEN, 'tok');
  assert.strictEqual(env.DSA_DESKTOP_MODE, 'true');
});

test('buildBackendEnvironment omits secrets when not provided', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
  });
  assert.strictEqual(env.OPENAI_API_KEY, undefined);
});

test('buildBackendEnvironment injects SearXNG env when running', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
    searxng: { baseUrls: 'http://127.0.0.1:8080' },
  });
  assert.strictEqual(env.SEARXNG_BASE_URLS, 'http://127.0.0.1:8080');
  assert.strictEqual(env.SEARXNG_PUBLIC_INSTANCES_ENABLED, 'false');
});

test('buildBackendEnvironment omits SearXNG env when not running', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
  });
  assert.strictEqual(env.SEARXNG_BASE_URLS, undefined);
  assert.strictEqual(env.SEARXNG_PUBLIC_INSTANCES_ENABLED, undefined);
});

test('buildBackendEnvironment opens 0.0.0.0 and forces auth in remote mode', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
    remote: { enabled: true },
  });
  assert.strictEqual(env.WEBUI_HOST, '0.0.0.0');
  assert.strictEqual(env.ADMIN_AUTH_ENABLED, 'true');
});

test('buildBackendEnvironment keeps localhost and disables auth by default', () => {
  const env = buildBackendEnvironment({
    envFile: '/data/.env',
    dbPath: '/data/db.sqlite',
    logDir: '/data/logs',
    port: 8000,
    host: '127.0.0.1',
  });
  assert.strictEqual(env.WEBUI_HOST, '127.0.0.1');
  assert.strictEqual(env.ADMIN_AUTH_ENABLED, 'false');
});
