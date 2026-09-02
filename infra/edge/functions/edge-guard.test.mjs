import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SOURCE_PATH = join(dirname(fileURLToPath(import.meta.url)), 'edge-guard.js');
const SOURCE = readFileSync(SOURCE_PATH, 'utf8');
const TEST_ALLOWED_IPS = '203.0.113.10/32,192.0.2.0/24';
const ALLOWED_IP = '203.0.113.10';
const FORBIDDEN_BODY = '{"ok":false,"error":{"code":"IP_FORBIDDEN"}}';

function loadHandler(allowedIps) {
  return new Function(SOURCE.split('__ALLOWED_IPS__').join(allowedIps) + '\nreturn handler;')();
}

const handler = loadHandler(TEST_ALLOWED_IPS);

function makeRequest(uri) {
  return { method: 'GET', uri, headers: {}, querystring: {} };
}

function makeEvent(ip, uri) {
  return { viewer: { ip }, request: makeRequest(uri) };
}

function assertForbidden(result, label) {
  assert.equal(result.statusCode, 403, label);
  assert.equal(result.statusDescription, 'Forbidden', label);
  assert.equal(result.headers['content-type'].value, 'application/json', label);
  assert.equal(result.headers['cache-control'].value, 'no-store', label);
  assert.equal(result.body.encoding, 'text', label);
  assert.equal(result.body.data, FORBIDDEN_BODY, label);
}

test('許可 IP からの / は 403 にならず /index.html へ書き換わる', () => {
  const event = makeEvent(ALLOWED_IP, '/');
  const result = handler(event);
  assert.equal(result.statusCode, undefined);
  assert.equal(result.uri, '/index.html');
});

test('許可外 IP からの / は 403 を返す', () => {
  assertForbidden(handler(makeEvent('198.51.100.1', '/')));
});

test('許可外 IP からの API 経路も 403 で遮断される', () => {
  assertForbidden(handler(makeEvent('198.51.100.1', '/api/saas-a-billing/runs')));
});

test('/24 の両端（192.0.2.0 と 192.0.2.255）は通過する', () => {
  for (const ip of ['192.0.2.0', '192.0.2.255']) {
    const result = handler(makeEvent(ip, '/api/saas-a-billing/runs'));
    assert.equal(result.statusCode, undefined, ip);
    assert.equal(result.uri, '/api/saas-a-billing/runs', ip);
  }
});

test('隣接レンジ（192.0.1.255 と 192.0.3.0）は 403', () => {
  for (const ip of ['192.0.1.255', '192.0.3.0']) {
    assertForbidden(handler(makeEvent(ip, '/')), ip);
  }
});

test('/32 の隣（203.0.113.11）は 403', () => {
  assertForbidden(handler(makeEvent('203.0.113.11', '/')));
});

test('IPv4 として解釈できない値は fail-closed で 403', () => {
  for (const ip of ['2001:db8::1', '', '999.1.1.1']) {
    assertForbidden(handler(makeEvent(ip, '/')), ip);
  }
});

test('プレフィックス長が空の CIDR は許可エントリとして無効（/0 扱いにしない）', () => {
  const guard = loadHandler('192.0.2.0/');
  for (const ip of ['198.51.100.1', '10.0.0.1']) {
    assertForbidden(guard(makeEvent(ip, '/')), ip);
  }
});

test('プレフィックス長が空白の CIDR も許可エントリとして無効', () => {
  for (const allowedIps of ['192.0.2.0/ ', '192.0.2.0/ 24']) {
    const guard = loadHandler(allowedIps);
    for (const ip of ['198.51.100.1', '10.0.0.1']) {
      assertForbidden(guard(makeEvent(ip, '/')), allowedIps + ' / ' + ip);
    }
  }
});

test('正常な CIDR と不正な CIDR が混在しても不正エントリだけ無視される', () => {
  const guard = loadHandler('203.0.113.10/32,192.0.2.0/24,192.0.2.0/');
  assertForbidden(guard(makeEvent('198.51.100.1', '/')));
  const result = guard(makeEvent('192.0.2.0', '/api/saas-a-billing/runs'));
  assert.equal(result.statusCode, undefined);
  assert.equal(result.uri, '/api/saas-a-billing/runs');
});

test('event.viewer に ip が無い場合は例外ではなく 403', () => {
  assertForbidden(handler({ viewer: {}, request: makeRequest('/') }));
});

test('event.viewer.ip が undefined の場合は例外ではなく 403', () => {
  assertForbidden(handler({ viewer: { ip: undefined }, request: makeRequest('/') }));
});

test('/saas-a-billing は /index.html へ書き換わる', () => {
  assert.equal(handler(makeEvent(ALLOWED_IP, '/saas-a-billing')).uri, '/index.html');
});

test('/saas-a-billing/settings は /index.html へ書き換わる', () => {
  assert.equal(handler(makeEvent(ALLOWED_IP, '/saas-a-billing/settings')).uri, '/index.html');
});

test('/ は /index.html へ書き換わる', () => {
  assert.equal(handler(makeEvent(ALLOWED_IP, '/')).uri, '/index.html');
});

test('/assets/index-abc123.js は書き換えない', () => {
  const uri = '/assets/index-abc123.js';
  assert.equal(handler(makeEvent(ALLOWED_IP, uri)).uri, uri);
});

test('/index.html は書き換えない', () => {
  assert.equal(handler(makeEvent(ALLOWED_IP, '/index.html')).uri, '/index.html');
});

test('/favicon.ico は書き換えない', () => {
  const uri = '/favicon.ico';
  assert.equal(handler(makeEvent(ALLOWED_IP, uri)).uri, uri);
});

test('/api/ で始まるパスは書き換えない', () => {
  const uri = '/api/saas-a-billing/runs';
  assert.equal(handler(makeEvent(ALLOWED_IP, uri)).uri, uri);
});

test('/api/ で始まる階層の深いパスも書き換えない', () => {
  const uri = '/api/saas-a-billing/runs/2026-10/download';
  assert.equal(handler(makeEvent(ALLOWED_IP, uri)).uri, uri);
});

test('通過時の戻り値は event.request と同一オブジェクト', () => {
  const event = makeEvent(ALLOWED_IP, '/saas-a-billing');
  assert.equal(handler(event), event.request);
});

test('原文に __ALLOWED_IPS__ がちょうど 1 箇所あり、ドル記号 + 波括弧 を含まない', () => {
  assert.equal(SOURCE.split('__ALLOWED_IPS__').length - 1, 1);
  assert.equal(SOURCE.includes('$' + '{'), false);
});
