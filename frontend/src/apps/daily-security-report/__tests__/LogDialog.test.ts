import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import LogDialog from '../components/LogDialog.vue'
import type { LogQueryState } from '../composables/useLogQuery'
import type { LogQueryResult, LogRef, LogRow } from '../types'

const CONSOLE = 'https://us-east-1.console.aws.amazon.com/cloudtrailv2/home?region=us-east-1#/events/evt-1'
const GUARDDUTY = 'https://ap-northeast-1.console.aws.amazon.com/guardduty/home?region=ap-northeast-1#/findings?macros=current&fId=f1'
const RANGE = { from: '2026-10-14T00:00:00+09:00', to: '2026-10-15T00:00:00+09:00' }
const result = (rows: LogRow[], extra: Partial<LogQueryResult> = {}): LogQueryResult => ({ source: '取得元の例', range: RANGE, rows, truncated: false, ...extra })
let wrapper: VueWrapper
async function mountDialog(props: { logRef: LogRef; status?: LogQueryState; result?: LogQueryResult | null; error?: string | null; errorCode?: string | null; link?: string | null }) {
  setActivePinia(createPinia())
  wrapper = mount(LogDialog, {
    props: { modelValue: true, status: 'succeeded', result: null, error: null, ...props },
    global: { plugins: [createVuetify({ components, directives })] },
  })
  await flushPromises()
  return wrapper
}
const dialog = () => document.body.querySelector('[data-test="log-dialog"]') as HTMLElement
const headers = () => Array.from(dialog().querySelectorAll('[data-test="log-table"] thead th')).map(th => th.textContent?.trim())
const cells = (row = 0) => Array.from(dialog().querySelectorAll('[data-test="log-table"] tbody tr')[row]!.querySelectorAll('td')).map(td => td.textContent?.trim())
afterEach(() => {
  wrapper?.unmount()
  document.body.innerHTML = ''
})

describe('LogDialog', () => {
  it('取得元・期間・行数・走査量と lambda の列を出し、内容をプレーンテキストで描画する', async () => {
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, result: result([
      { time: '2026-10-14T17:03:04+09:00', text: '<img src=x onerror=alert(1)> Task timed out' },
    ], { source: 'CloudWatch Logs /aws/lambda/example-fn', scanned_bytes: 3145728 }) })
    const text = dialog().textContent!
    for (const expected of ['Lambda example-fn', 'CloudWatch Logs /aws/lambda/example-fn', '2026-10-14 00:00', '2026-10-15 00:00', '1 行', '3.0 MB']) expect(text).toContain(expected)
    expect(headers()).toEqual(['時刻（JST）', '内容'])
    expect(cells()).toEqual(['2026-10-14 17:03:04', '<img src=x onerror=alert(1)> Task timed out'])
    expect(dialog().querySelector('img')).toBeNull()
  })
  it.each([
    ['alarmhuba', { kind: 'alarmhuba', ip: '198.51.100.7', path: '/api/admin' }, { time: '2026-10-14T09:12:03+09:00', method: 'GET', path: '/api/admin', status: 403 },
      ['時刻（JST）', 'メソッド', 'パス', '状態'], ['2026-10-14 09:12:03', 'GET', '/api/admin', '403']],
    ['gps', { kind: 'gps', ip: '203.0.113.9' }, { time: '2026-10-14T09:12:03+09:00', method: 'POST', path: '/receive' },
      ['時刻（JST）', 'メソッド', 'パス'], ['2026-10-14 09:12:03', 'POST', '/receive']],
    ['metric', { kind: 'metric', key: 'waf.blocked' }, { time: '03:15', count: 1200 },
      ['時刻（JST）', '件数'], ['03:15', '1,200']],
  ] as const)('%s の列を出す', async (_kind, logRef, row, expectedHeaders, expectedCells) => {
    await mountDialog({ logRef, result: result([row]) })
    expect(headers()).toEqual(expectedHeaders)
    expect(cells()).toEqual(expectedCells)
  })
  it('cloudtrail はイベント ID をコンソールのリンクにし、それ以外の URL はリンクにしない', async () => {
    await mountDialog({ logRef: { kind: 'cloudtrail', region: 'us-east-1', event: 'CreateUser', from: RANGE.from, to: RANGE.to }, result: result([
      { time: '2026-10-14T10:00:00+09:00', event: 'CreateUser', actor: 'user-example', ip: '203.0.113.5', event_id: 'evt-1', link: CONSOLE },
      { time: '2026-10-14T10:05:00+09:00', event: 'CreateUser', actor: 'user-example', ip: '203.0.113.5', event_id: 'evt-2', link: 'https://example.com/?u=console.aws.amazon.com' },
    ]) })
    expect(headers()).toEqual(['時刻（JST）', '操作', '主体', 'IP', 'イベント ID'])
    expect(cells(0)).toEqual(['2026-10-14 10:00:00', 'CreateUser', 'user-example', '203.0.113.5', 'evt-1'])
    const rows = dialog().querySelectorAll('[data-test="log-table"] tbody tr')
    const anchor = rows[0]!.querySelector('a')!
    expect(anchor.getAttribute('href')).toBe(CONSOLE)
    expect(anchor.getAttribute('target')).toBe('_blank')
    expect(anchor.getAttribute('rel')).toBe('noopener')
    expect(rows[1]!.querySelector('a')).toBeNull()
  })
  it('guardduty は項目と値の縦の表にし、検出のリンクを「コンソールで開く」にする', async () => {
    await mountDialog({ logRef: { kind: 'guardduty', region: 'ap-northeast-1', id: 'f1' }, result: result([{
      time: '2026-10-14T22:45:00+09:00', type: 'Recon:EC2/PortProbeUnprotectedPort', severity: 5, title: '保護されていないポートへの探索',
      description: 'EC2 インスタンスの保護されていないポートが探索されています。', resource: 'i-example', remote_ip: '203.0.113.9', remote_country: 'US',
      first_seen: '2026-10-14T02:10:00+09:00', last_seen: '2026-10-14T22:45:00+09:00', count: 3, link: GUARDDUTY,
    }]) })
    const table = dialog().querySelector('[data-test="log-fields"]')!
    const pairs = Array.from(table.querySelectorAll('tbody tr')).map(tr => Array.from(tr.querySelectorAll('th, td')).map(cell => cell.textContent?.trim()))
    expect(pairs[0]).toEqual(['種別', 'Recon:EC2/PortProbeUnprotectedPort'])
    expect(pairs).toContainEqual(['重大度', '5'])
    expect(pairs).toContainEqual(['相手の国', 'US'])
    expect(pairs).toContainEqual(['初回観測', '2026-10-14 02:10:00'])
    expect(pairs).toContainEqual(['回数', '3'])
    expect(pairs.find(pair => pair[0] === 'link')).toBeUndefined()
    const open = dialog().querySelector('[data-test="log-console"]') as HTMLAnchorElement
    expect(open.textContent).toContain('コンソールで開く')
    expect(open.getAttribute('href')).toBe(GUARDDUTY)
    expect(open.getAttribute('target')).toBe('_blank')
    expect(open.getAttribute('rel')).toBe('noopener')
  })
  it('truncated なら先頭 200 行と出し、注記を添える', async () => {
    const rows = Array.from({ length: 200 }, (_, index) => ({ time: '2026-10-14T00:00:00+09:00', text: `line ${index}` }))
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, result: result(rows, { truncated: true, note: '上限で打ち切りました' }) })
    expect(dialog().textContent).toContain('先頭 200 行')
    expect(dialog().textContent).toContain('上限で打ち切りました')
    expect(dialog().querySelectorAll('[data-test="log-table"] tbody tr')).toHaveLength(200)
  })
  it('コンソールのリンクは https の AWS コンソールだけにする', async () => {
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, result: result([]), link: 'https://evil.example/?u=console.aws.amazon.com' })
    expect(dialog().querySelector('[data-test="log-console"]')).toBeNull()
    expect(dialog().textContent).toContain('該当するログはありません')
    wrapper.unmount()
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, result: result([]), link: 'https://ap-northeast-1.console.aws.amazon.com/cloudwatch/home' })
    expect(dialog().querySelector('[data-test="log-console"]')?.getAttribute('href')).toBe('https://ap-northeast-1.console.aws.amazon.com/cloudwatch/home')
  })
  it('running 中は問い合わせ中と出し、表を出さない', async () => {
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, status: 'running' })
    expect(dialog().querySelector('[data-test="log-running"]')?.textContent).toContain('問い合わせ中')
    expect(dialog().querySelector('[data-test="log-table"]')).toBeNull()
  })
  it('失敗は理由とコードを出す', async () => {
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, status: 'failed', error: '60 秒で打ち切り', errorCode: 'TIMEOUT' })
    const error = dialog().querySelector('[data-test="log-error"]')!.textContent!
    expect(error).toContain('60 秒で打ち切り')
    expect(error).toContain('TIMEOUT')
  })
  it('「閉じる」で閉じるよう親に知らせる', async () => {
    await mountDialog({ logRef: { kind: 'lambda', function: 'example-fn' }, status: 'running' })
    ;(dialog().querySelector('[data-test="log-close"]') as HTMLElement).click()
    await flushPromises()
    expect(wrapper.emitted('update:modelValue')).toEqual([[false]])
  })
})
