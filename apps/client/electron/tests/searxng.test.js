const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');
const searxng = require('../searxng');

test('assembleSearxngEnv returns empty when no baseUrls', () => {
  assert.deepStrictEqual(searxng.assembleSearxngEnv(null), {});
  assert.deepStrictEqual(searxng.assembleSearxngEnv(undefined), {});
});

test('assembleSearxngEnv builds SEARXNG_BASE_URLS and disables public instances', () => {
  assert.deepStrictEqual(searxng.assembleSearxngEnv('http://127.0.0.1:8080'), {
    SEARXNG_BASE_URLS: 'http://127.0.0.1:8080',
    SEARXNG_PUBLIC_INSTANCES_ENABLED: 'false',
  });
});

test('assembleSearxngEnv joins an array of base urls', () => {
  assert.strictEqual(
    searxng.assembleSearxngEnv(['http://a:8080', 'http://b:8080']).SEARXNG_BASE_URLS,
    'http://a:8080,http://b:8080',
  );
});

test('evaluateSearxngStatus classifies the situation', () => {
  assert.strictEqual(searxng.evaluateSearxngStatus({ dockerAvailable: false }), 'docker_missing');
  assert.strictEqual(searxng.evaluateSearxngStatus({ dockerAvailable: true, containerRunning: false }), 'stopped');
  assert.strictEqual(searxng.evaluateSearxngStatus({ dockerAvailable: true, containerRunning: true, healthy: false }), 'unhealthy');
  assert.strictEqual(searxng.evaluateSearxngStatus({ dockerAvailable: true, containerRunning: true, healthy: true }), 'running');
});

test('nextSearxngAction maps status to action', () => {
  assert.strictEqual(searxng.nextSearxngAction('running'), 'stop');
  assert.strictEqual(searxng.nextSearxngAction('stopped'), 'start');
  assert.strictEqual(searxng.nextSearxngAction('unhealthy'), 'start');
  assert.strictEqual(searxng.nextSearxngAction('docker_missing'), 'start');
});

test('buildComposeCommand points cwd at the searxng dir so relative mounts resolve', () => {
  const cmd = searxng.buildComposeCommand('/opt/searxng', 'up', ['-d']);
  assert.strictEqual(cmd.command, 'docker');
  assert.strictEqual(cmd.cwd, '/opt/searxng');
  assert.strictEqual(cmd.args[0], 'compose');
  assert.strictEqual(cmd.args[1], '-f');
  assert.strictEqual(cmd.args[2], path.join('/opt/searxng', 'docker-compose.searxng.yml'));
  assert.strictEqual(cmd.args[3], 'up');
  assert.strictEqual(cmd.args[4], '-d');
});

test('parseSearchJson round-trips valid JSON and rejects garbage', () => {
  assert.deepStrictEqual(searxng.parseSearchJson('{"results":[]}'), { ok: true, json: { results: [] } });
  assert.strictEqual(searxng.parseSearchJson('not json').ok, false);
});
