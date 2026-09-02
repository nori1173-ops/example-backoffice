// infra/edge/functions/edge-guard.js（正本。テンプレートの FunctionCode に !Sub でインライン展開する）
// 下の ALLOWED の文字列リテラルはプレースホルダ。テンプレート側ではこの 1 語だけを !Sub の変数参照
// （ドル記号 + 波括弧 + AllowedIps）に置き換え、第 2 要素の AllowedIps: !Join [',', !Ref AllowedIpAddresses]
// が値を埋める。このファイル自体に !Sub の変数参照記法を書いてはいけない（!Sub が解釈してしまう）。
var ALLOWED = '__ALLOWED_IPS__'.split(',').map(function (s) { return s.trim(); });

function ipToLong(ip) {
  var p = ip.split('.');
  if (p.length !== 4) { return -1; }
  var n = 0;
  for (var i = 0; i < 4; i++) {
    if (!/^[0-9]{1,3}$/.test(p[i])) { return -1; }
    var o = Number(p[i]);
    if (o > 255) { return -1; }
    n = n * 256 + o;
  }
  return n;
}

function isAllowed(ip) {
  var addr = ipToLong(ip);
  if (addr < 0) { return false; }
  for (var i = 0; i < ALLOWED.length; i++) {
    var parts = ALLOWED[i].split('/');
    var base = ipToLong(parts[0]);
    if (base < 0) { continue; }
    var bits = 32;
    if (parts.length > 1) {
      if (!/^[0-9]{1,2}$/.test(parts[1])) { continue; }
      bits = Number(parts[1]);
      if (bits > 32) { continue; }
    }
    var block = Math.pow(2, 32 - bits);
    if (Math.floor(addr / block) === Math.floor(base / block)) { return true; }
  }
  return false;
}

function handler(event) {
  var ip = event.viewer ? event.viewer.ip : null;
  if (typeof ip !== 'string' || !isAllowed(ip)) {
    return {
      statusCode: 403,
      statusDescription: 'Forbidden',
      headers: {
        'content-type': { value: 'application/json' },
        'cache-control': { value: 'no-store' }
      },
      body: { encoding: 'text', data: '{"ok":false,"error":{"code":"IP_FORBIDDEN"}}' }
    };
  }
  var req = event.request;
  if (!req.uri.startsWith('/api/') && !req.uri.includes('.')) {
    req.uri = '/index.html';
  }
  return req;
}
