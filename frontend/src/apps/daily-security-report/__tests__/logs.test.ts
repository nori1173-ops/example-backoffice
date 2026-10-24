import { describe, expect, it } from 'vitest'
import { GUARDDUTY_FIELDS, LOG_COLUMNS, describeLogRef, formatLogCell, formatScanned, isLogRef, isMetricCounter, logErrorMessage } from '../utils/logs'

describe('isLogRef', () => {
  it.each([
    { kind: 'lambda', function: 'example-fn' },
    { kind: 'alarmhuba', ip: '198.51.100.7' },
    { kind: 'alarmhuba', ip: '198.51.100.7', path: '/api/admin' },
    { kind: 'gps', ip: '203.0.113.9' },
    { kind: 'cloudtrail', region: 'ap-northeast-1', actor: 'user-example', from: '2026-10-14T00:00:00+09:00', to: '2026-10-15T00:00:00+09:00' },
    { kind: 'cloudtrail', region: 'us-east-1', event_id: 'evt-1' },
    { kind: 'guardduty', region: 'ap-northeast-1', id: 'f1' },
    { kind: 'metric', key: 'waf.blocked' },
    { kind: 'metric', key: 'alb.4xx', target: 'alb:ap-northeast-1:app/example/0123' },
  ])('第 2 段階の問い合わせに使える ref を受け付ける: %o', ref => {
    expect(isLogRef(ref)).toBe(true)
  })
  it.each([
    undefined, null, '', 'lambda', [], {},
    { kind: 'unknown', ip: '198.51.100.7' },
    { kind: 'toString' },
    { kind: 'lambda' },
    { kind: 'lambda', function: '' },
    { kind: 'alarmhuba', path: '/api' },
    { kind: 'gps', ip: 7 },
    { kind: 'guardduty', id: 'f1' },
    { kind: 'cloudtrail', event_id: 'evt-1' },
    { kind: 'cloudtrail', region: 'us-east-1', from: '2026-10-14T00:00:00+09:00', to: '2026-10-15T00:00:00+09:00' },
    { kind: 'guardduty', region: 'ap-northeast-1' },
    { kind: 'metric' },
  ])('必須項目の無い ref と ref でない値を弾く: %o', value => {
    expect(isLogRef(value)).toBe(false)
  })
})

describe('isMetricCounter', () => {
  it('CloudWatch 指標で引ける集計値だけを指標の問い合わせの対象にする', () => {
    for (const key of ['waf.blocked', 'apigw.5xx', 'alb.4xx', 'cloudfront.requests.E1EXAMPLE', 'lambda.errors', 'gps.rejected',
      'instances.ec2.i-example.cpu_avg', 'instances.lightsail.example-web.net_out']) expect(isMetricCounter(key)).toBe(true)
    for (const key of ['alarmhuba.requests', 'gps.received', 'lambda.throttles', 'guardduty.new.medium', 'cloudtrail.root',
      'instances.ec2.count', 'instances.rds.example.cpu_avg', 'unknown']) expect(isMetricCounter(key)).toBe(false)
  })
})

describe('logErrorMessage', () => {
  it('同時実行の上限と打ち切りを日本語にし、それ以外はコードを出す', () => {
    expect(logErrorMessage('TOO_MANY_QUERIES')).toBe('同時に見られるのは 3 件まで')
    expect(logErrorMessage('TIMEOUT')).toBe('60 秒で打ち切り')
    expect(logErrorMessage('QUERY_FAILED')).toBe('QUERY_FAILED')
    expect(logErrorMessage('INVALID_QUERY', '問い合わせの内容が不正です')).toBe('問い合わせの内容が不正です（INVALID_QUERY）')
    expect(logErrorMessage(null, null)).toBe('取得に失敗しました')
  })
})

describe('formatScanned', () => {
  it('走査量を MB で出し、無ければ空にする', () => {
    expect(formatScanned(1048576)).toBe('1.0 MB')
    expect(formatScanned(52428800 + 524288)).toBe('50.5 MB')
    expect(formatScanned(2048)).toBe('0.0 MB')
    expect(formatScanned(undefined)).toBe('')
    expect(formatScanned(null)).toBe('')
    expect(formatScanned(Number.NaN)).toBe('')
  })
})

describe('LOG_COLUMNS', () => {
  it('kind ごとの列を仕様の順に持つ', () => {
    const labels = (kind: keyof typeof LOG_COLUMNS) => LOG_COLUMNS[kind].map(column => column.label)
    expect(labels('lambda')).toEqual(['時刻（JST）', '内容'])
    expect(labels('alarmhuba')).toEqual(['時刻（JST）', 'メソッド', 'パス', '状態'])
    expect(labels('gps')).toEqual(['時刻（JST）', 'メソッド', 'パス'])
    expect(labels('cloudtrail')).toEqual(['時刻（JST）', '操作', '主体', 'IP', 'イベント ID'])
    expect(labels('metric')).toEqual(['時刻（JST）', '件数'])
    expect(GUARDDUTY_FIELDS.map(field => field.key)).toEqual(['type', 'severity', 'title', 'description', 'resource', 'remote_ip', 'remote_country', 'first_seen', 'last_seen', 'count'])
  })
})

describe('formatLogCell', () => {
  it('時刻を JST の秒まで、指標は分まで出し、数値は桁区切り、空は — にする', () => {
    expect(formatLogCell('2026-10-14T09:12:03+09:00', 'time', 'lambda')).toBe('2026-10-14 09:12:03')
    expect(formatLogCell('2026-10-14T22:45:00+09:00', 'last_seen', 'guardduty')).toBe('2026-10-14 22:45:00')
    expect(formatLogCell('03:15', 'time', 'metric')).toBe('03:15')
    expect(formatLogCell('2026-10-14T03:15:00+09:00', 'time', 'metric')).toBe('03:15')
    expect(formatLogCell(12345, 'count', 'metric')).toBe('12,345')
    expect(formatLogCell(8.5, 'severity', 'guardduty')).toBe('8.5')
    expect(formatLogCell(403, 'status', 'alarmhuba')).toBe('403')
    expect(formatLogCell(null, 'path', 'alarmhuba')).toBe('—')
    expect(formatLogCell(undefined, 'ip', 'cloudtrail')).toBe('—')
    expect(formatLogCell('', 'text', 'lambda')).toBe('—')
    expect(formatLogCell('not-a-time', 'time', 'lambda')).toBe('not-a-time')
    expect(formatLogCell('<b>x</b>', 'text', 'lambda')).toBe('<b>x</b>')
  })
})

describe('describeLogRef', () => {
  it('問い合わせの対象を 1 行で表す', () => {
    expect(describeLogRef({ kind: 'lambda', function: 'example-fn' })).toBe('Lambda example-fn')
    expect(describeLogRef({ kind: 'alarmhuba', ip: '198.51.100.7', path: '/api/admin' })).toBe('alarmhuba 198.51.100.7 /api/admin')
    expect(describeLogRef({ kind: 'alarmhuba', ip: '198.51.100.7' })).toBe('alarmhuba 198.51.100.7')
    expect(describeLogRef({ kind: 'gps', ip: '203.0.113.9' })).toBe('GPS 受信口 203.0.113.9')
    expect(describeLogRef({ kind: 'cloudtrail', region: 'us-east-1', event: 'CreateUser', actor: 'user-example', from: 'a', to: 'b' })).toBe('CloudTrail us-east-1 CreateUser')
    expect(describeLogRef({ kind: 'cloudtrail', actor: 'user-example', from: 'a', to: 'b' })).toBe('CloudTrail user-example')
    expect(describeLogRef({ kind: 'guardduty', region: 'ap-northeast-1', id: 'f1' })).toBe('GuardDuty ap-northeast-1 f1')
    expect(describeLogRef({ kind: 'metric', key: 'waf.blocked' })).toBe('指標 waf.blocked')
    expect(describeLogRef({ kind: 'metric', key: 'waf.blocked' }, { 'waf.blocked': { label: 'WAF 遮断', description: '' } })).toBe('指標 WAF 遮断')
  })
})
