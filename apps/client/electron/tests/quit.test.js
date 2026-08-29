'use strict';

const test = require('node:test');
const assert = require('node:assert');
const { shouldConfirmQuit } = require('../main.js');

test('confirms quit when analysis running', () => {
  assert.strictEqual(shouldConfirmQuit(true), true);
  assert.strictEqual(shouldConfirmQuit(false), false);
});

test('skipConfirm bypasses running-analysis prompt', () => {
  assert.strictEqual(shouldConfirmQuit(true, true), false);
  assert.strictEqual(shouldConfirmQuit(false, true), false);
});
