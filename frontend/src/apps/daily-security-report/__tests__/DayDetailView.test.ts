import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import HelpTip from '@/components/HelpTip.vue'
import { useAuthStore } from '@/stores/auth'
import { reportApi } from '../api'
import { HELP } from '../help'
import DayDetailView from '../views/DayDetailView.vue'
import type { DailySummary, S3SourceResult } from '../types'
import dailyS3 from '../../../../../apps/daily-security-report/tests/fixtures/daily_s3.json'
import { CONSOLE_LINKS, makeAi, makeDetail, makeS3Detail, makeTraceDetail, makeV1Detail } from './fixtures'

vi.mock('../api', () => ({ reportApi: { getDay: vi.fn(), startLogs: vi.fn(), getLogs: vi.fn() } }))
let wrapper: VueWrapper
let router: Router
async function mountView(groups: string[] = []) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useAuthStore().groups = groups
  router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/days/:date', name: 'daily-security-report.day', component: DayDetailView },
    { path: '/', name: 'daily-security-report.days', component: { template: '<p>レポート一覧</p>' } },
  ] })
  await router.push('/days/2026-10-14')
  await router.isReady()
  wrapper = mount({ template: '<router-view v-slot="{ Component }"><KeepAlive><component :is="Component" /></KeepAlive></router-view>' }, {
    global: { plugins: [pinia, router, createVuetify({ components, directives })] },
  })
  await flushPromises()
  return wrapper
}
beforeEach(() => { vi.resetAllMocks(); vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail()) })
afterEach(() => wrapper?.unmount())
describe('DayDetailView', () => {
  it('v2 の段階・見解全文・モデル・規則所見・人の操作と取得状況を描画する', async () => {
    const view = await mountView()
    expect(reportApi.getDay).toHaveBeenCalledWith('2026-10-14')
    expect(view.get('[data-test="day-summary"]').text()).toContain('Warning')
    expect(view.get('[data-test="detail-ai-opinion"]').text()).toBe(makeAi().opinion)
    expect(view.text()).toContain('Claude Opus 5.5')
    expect(view.text()).toContain('jp.anthropic.claude-opus-5-5')
    expect(view.text()).toContain('0123456789ab')
    expect(view.findAll('[data-test^="detail-"]').length).toBeGreaterThanOrEqual(7)
    const cloudtrail = view.get('[data-test="detail-cloudtrail"]')
    for (const text of ['規則', '最終', '休眠主体からの操作', 'user-example', '203.0.113.5', 'JP', 'ListBuckets', '2026-10-14 10:05']) expect(cloudtrail.text()).toContain(text)
    expect(view.get('[data-test="detail-metrics"]').text()).toContain('未集計（AccessDenied）')
    expect(view.get('[data-test="detail-insights"]').text()).toContain('停止中（設定）')
    expect(view.get('[data-test="detail-footer"]').text()).toContain('除外 12 件 / 所要 145 秒')
    expect(view.get('[data-test="detail-footer"]').text()).toContain('基準なし: 入口の指標')
  })
  it('休眠主体・棚卸しの各表と省略件数を表示する', async () => {
    const detail = makeDetail()
    detail.summary!.actors!.truncated = { dormant: 2 }
    detail.summary!.sources.cloudtrail!.details_truncated = { human_operations: 4 }
    detail.summary!.findings_truncated = { cloudtrail: 3 }
    await mountView()
    const actor = wrapper.get('[data-test="detail-actors"]')
    for (const text of ['休眠主体の利用', '2026-09-01', '未使用のユーザー', '未使用の有効キー', 'MFA なし']) expect(actor.text()).toContain(text)
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    await router.push('/days/2026-10-13')
    await flushPromises()
    expect(wrapper.get('[data-test="detail-actors"]').text()).toContain('ほか 2 件')
    expect(wrapper.get('[data-test="detail-cloudtrail"]').text()).toContain('ほか 4 件')
    expect(wrapper.get('[data-test="detail-cloudtrail"]').text()).toContain('ほか 3 件の所見')
  })
  it('所見の表は内容と件数を別の列に出し、件数を二重に出さない', async () => {
    const detail = makeDetail()
    detail.summary!.sources.insights = { status: 'ok' }
    detail.summary!.findings = [{ section: 'insights', code: 'ADMIN_EXTERNAL', level: 'warning', text: '198.51.100.7（US）/api/admin/{id}/settings', count: 12 }]
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const view = await mountView()
    const cells = view.get('[data-test="detail-insights"]').findAll('tbody tr td')
    expect(cells).toHaveLength(5)
    expect(cells[1]!.text()).toContain('198.51.100.7（US）/api/admin/{id}/settings')
    expect(cells[1]!.text()).not.toContain('件')
    expect(cells[2]!.text()).toBe('12')
    expect(cells[3]!.text()).toBe('—')
    expect(cells[4]!.text()).toBe('—')
  })
  it('actors の無い日には主体・棚卸しのカードを出さない', async () => {
    const detail = makeDetail()
    delete detail.summary!.actors
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    expect((await mountView()).find('[data-test="detail-actors"]').exists()).toBe(false)
  })
  it('AI が上げ下げした段階と注記を表示する', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ ai: makeAi({
      adjustments: [{ section: 'cloudtrail', from: 'warning', to: 'ok', reason: '既知の定例作業' }], note: '規則の所見は保持',
    }) }))
    const view = await mountView()
    expect(view.text()).toContain('AI が変えた段階')
    expect(view.text()).toContain('既知の定例作業')
    expect(view.text()).toContain('規則の所見は保持')
  })
  it.each(['disabled', 'failed', 'refused', 'timeout', 'skipped'] as const)('AI %s の理由を示す', async status => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ ai: makeAi({ status, error: { code: 'AI_UNAVAILABLE', message: '利用できません' } }) }))
    const view = await mountView()
    expect(view.get('[data-test="detail-ai-opinion"]').text()).toContain(status === 'disabled' ? '設定で無効' : '利用できません')
  })
  it('v1 の読み替え応答を同じ画面で示し AI 判定なしを表示する', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ legacy: true, ai: makeAi({ status: 'disabled', opinion: '取得できず（v1 は AI 判定なし）', adjustments: undefined, sections: undefined, model_id: null, prompt_hash: null }) }))
    const view = await mountView()
    expect(view.get('[data-test="day-summary"]').text()).toContain('v1')
    expect(view.get('[data-test="detail-ai-opinion"]').text()).toContain('v1 は AI 判定なし')
    expect(view.get('[data-test="detail-cloudtrail"]').text()).toContain('ListBuckets')
  })
  it('404 ではレポートがありませんと一覧へ戻る導線を表示する', async () => {
    vi.mocked(reportApi.getDay).mockRejectedValue({ response: { status: 404, data: { error: { code: 'NOT_FOUND', message: 'なし' } } } })
    const view = await mountView()
    expect(view.get('[data-test="day-detail-error"]').text()).toContain('レポートがありません')
    expect(view.find('[data-test="day-summary"]').exists()).toBe(false)
    await view.get('[data-test="back-to-days"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
  })
  it('取得失敗を示し再取得できる', async () => {
    vi.mocked(reportApi.getDay).mockRejectedValueOnce(new Error('offline'))
    const view = await mountView()
    expect(view.get('[data-test="day-detail-error"]').text()).toContain('取得に失敗')
    await view.get('[data-test="day-detail-error"] button').trigger('click')
    await flushPromises()
    expect(view.find('[data-test="day-detail-error"]').exists()).toBe(false)
    expect(view.get('[data-test="day-summary"]').text()).toContain('2026-10-14')
  })
  it('日付を変えたとき前日の内容を出さず、戻るとキャッシュを使う', async () => {
    await mountView()
    let resolve!: (value: ReturnType<typeof makeDetail>) => void
    vi.mocked(reportApi.getDay).mockReturnValueOnce(new Promise(done => { resolve = done }))
    await router.push('/days/2026-10-13')
    await flushPromises()
    expect(wrapper.find('[data-test="day-summary"]').exists()).toBe(false)
    resolve(makeDetail({ date: '2026-10-13' }))
    await flushPromises()
    expect(wrapper.get('[data-test="day-summary"]').text()).toContain('2026-10-13')
    await router.push('/days/2026-10-14')
    await flushPromises()
    expect(reportApi.getDay).toHaveBeenCalledTimes(2)
    expect(wrapper.get('[data-test="day-summary"]').text()).toContain('2026-10-14')
  })
  it('AI 文面を HTML として解釈しない', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ ai: makeAi({ opinion: '<img src=x onerror=alert(1)>' }) }))
    const view = await mountView()
    expect(view.get('[data-test="detail-ai-opinion"]').text()).toBe('<img src=x onerror=alert(1)>')
    expect(view.get('[data-test="detail-ai-opinion"]').find('img').exists()).toBe(false)
  })
})


it('停止中で記録も棚卸しも無い日は主体カードを出さない', async () => {
  const detail = makeDetail()
  detail.summary!.actors = { status: 'disabled', dormant: [], new: [], external: [] }
  vi.mocked(reportApi.getDay).mockResolvedValue(detail)
  expect((await mountView()).find('[data-test="detail-actors"]').exists()).toBe(false)
})


it('AI が返した取得不能の説明を二重に囲まずに表示する', async () => {
  vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ ai: makeAi({ status: 'skipped', error: null, opinion: '取得できず（残り時間不足）' }) }))
  expect((await mountView()).get('[data-test="detail-ai-opinion"]').text()).toBe('取得できず（残り時間不足）')
})


it('実 v1 形の読み替え応答で旧明細と基準なしを表示する', async () => {
  const detail = makeV1Detail()
  expect('no_baseline' in detail.summary!).toBe(true)
  vi.mocked(reportApi.getDay).mockResolvedValue(detail)
  const view = await mountView()
  expect(view.get('[data-test="day-summary"]').text()).toContain('v1')
  expect(view.get('[data-test="detail-ai-opinion"]').text()).toBe('取得できず（v1 は AI 判定なし）')
  expect(view.get('[data-test="detail-cloudtrail"]').text()).toContain('ListBuckets')
  expect(view.get('[data-test="detail-footer"]').text()).toContain('基準なし: CloudTrail、入口の指標、Logs Insights、インスタンス')
})

it('baseline_missing が無い旧応答でも詳細を描画する', async () => {
  const detail = makeV1Detail()
  Reflect.deleteProperty(detail.summary!, 'baseline_missing')
  vi.mocked(reportApi.getDay).mockResolvedValue(detail)
  const view = await mountView()
  expect(view.get('[data-test="detail-cloudtrail"]').text()).toContain('ListBuckets')
  expect(view.get('[data-test="detail-footer"]').text()).toContain('基準なし: なし')
})

it('無効時も一覧用の見解を全文表示する', async () => {
  const opinion = '取得できず（設定で無効）'
  vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ ai_status: 'disabled', ai_opinion: opinion, ai: makeAi({ status: 'disabled', opinion }) }))
  expect((await mountView()).get('[data-test="detail-ai-opinion"]').text()).toBe(opinion)
})

describe('所見の追跡（v2.1）', () => {
  beforeEach(() => { vi.mocked(reportApi.getDay).mockResolvedValue(makeTraceDetail()) })
  it('所見表に時刻と場所の列を出し、コンソールのリンクだけを新しいタブで開く', async () => {
    const view = await mountView()
    const cloudtrail = view.get('[data-test="detail-cloudtrail"]')
    expect(cloudtrail.findAll('[data-test="finding-row"]')).toHaveLength(1)
    expect(cloudtrail.findAll('thead th').slice(0, 5).map(th => th.text())).toEqual(['段階', '内容', '件数', '時刻', '場所'])
    const cells = cloudtrail.get('[data-test="finding-row"]').findAll('td')
    expect(cells[3]!.text()).toBe('10:00〜10:05')
    expect(cells[4]!.text()).toContain('CloudTrail ap-northeast-1')
    const anchor = cells[4]!.get('a')
    expect(anchor.text()).toBe('コンソール')
    expect(anchor.attributes('href')).toBe(CONSOLE_LINKS.cloudtrail)
    expect(anchor.attributes('target')).toBe('_blank')
    expect(anchor.attributes('rel')).toBe('noopener')
    const insights = view.get('[data-test="detail-insights"]').get('[data-test="finding-row"]').findAll('td')
    expect(insights[3]!.text()).toBe('09:12')
    expect(insights[4]!.find('a').exists()).toBe(false)
    expect(insights[4]!.text()).toContain('http://example.com/not-console')
    const metrics = view.get('[data-test="detail-metrics"]').get('[data-test="finding-row"]').findAll('td')
    expect(metrics[3]!.text()).toBe('—')
    expect(metrics[4]!.text()).toBe('CloudWatch 指標 AWS/WAFV2')
  })
  it('AI が keys で挙げた所見コードの行だけに「AI の根拠」チップを付ける', async () => {
    const view = await mountView()
    expect(view.get('[data-test="detail-cloudtrail"] [data-test="finding-row"]').find('[data-test="ai-key-chip"]').exists()).toBe(true)
    expect(view.get('[data-test="detail-metrics"] [data-test="finding-row"]').find('[data-test="ai-key-chip"]').exists()).toBe(false)
    expect(view.get('[data-test="detail-guardduty"] [data-test="finding-row"]').find('[data-test="ai-key-chip"]').exists()).toBe(false)
  })
  it('集計値を表示名・基準・比率・判定・時間帯の表にし、AI の根拠の行に印を付ける', async () => {
    const view = await mountView()
    const table = view.get('[data-test="detail-metrics"] [data-test="counter-table"]')
    const rows = table.findAll('[data-test="counter-row"]')
    expect(rows.map(row => row.findAll('td')[0]!.text())).toEqual([expect.stringContaining('WAF 遮断'), expect.stringContaining('ALB 4xx')])
    const waf = rows[0]!.findAll('td').map(td => td.text())
    expect(waf[0]).toContain('waf.blocked')
    expect(waf[0]).toContain('AI の根拠')
    expect(waf.slice(1, 5)).toEqual(['231', '180', '×1.28', 'Warning'])
    expect(waf[5]).toContain('03 時 200 件、17 時 20 件')
    expect(rows[0]!.classes()).toContain('exceeded')
    const alb = rows[1]!.findAll('td').map(td => td.text())
    expect(alb[0]).not.toContain('AI の根拠')
    expect(alb.slice(2, 5)).toEqual(['基準なし', '—', '基準なし'])
    const insights = view.get('[data-test="detail-insights"] [data-test="counter-table"]')
    expect(insights.text()).toContain('社外からの管理 API')
    expect(insights.text()).toContain('alarmhuba.admin_external')
    expect(view.get('[data-test="detail-guardduty"] [data-test="counter-table"]').text()).toContain('GuardDuty 新規 Medium')
  })
  it('上位表を区分のカードに日本語の見出しで出し、省略件数と時間帯とコンソールを添える', async () => {
    const view = await mountView()
    const metrics = view.get('[data-test="detail-metrics"]')
    expect(metrics.text()).toContain('Lambda エラーの関数（上位）')
    expect(metrics.text()).toContain('example-fn')
    expect(metrics.text()).toContain('17 時 7 件')
    expect(metrics.text()).not.toContain('Recon:EC2')
    const guardduty = view.get('[data-test="detail-guardduty"]')
    expect(guardduty.text()).toContain('GuardDuty の検出（上位）')
    expect(guardduty.text()).toContain('GuardDuty の検出（明細）')
    expect(guardduty.text()).toContain('i-example')
    expect(guardduty.findAll('a').filter(a => a.attributes('href') === CONSOLE_LINKS.guardduty).length).toBeGreaterThanOrEqual(2)
    const insights = view.get('[data-test="detail-insights"]')
    expect(insights.text()).toContain('alarmhuba の上位 IP')
    expect(insights.text()).toContain('社外からの管理 API')
    expect(insights.text()).toContain('ほか 48 件')
    expect(view.get('[data-test="detail-cloudtrail"]').text()).toContain('AccessDenied の主体（上位）')
  })
  it('AI の区分理由の下に根拠を表示名で出す', async () => {
    const view = await mountView()
    expect(view.get('[data-test="detail-metrics"] [data-test="ai-basis"]').text()).toBe('根拠: WAF 遮断、Lambda エラーの関数（上位）')
    expect(view.get('[data-test="detail-insights"] [data-test="ai-basis"]').text()).toBe('根拠: 198.51.100.7（US）/api/admin、社外からの管理 API、mystery')
    expect(view.get('[data-test="detail-cloudtrail"] [data-test="ai-basis"]').text()).toBe('根拠: 休眠主体からの操作')
    expect(view.get('[data-test="detail-instances"]').find('[data-test="ai-basis"]').exists()).toBe(false)
  })
  it('区分カードの見出しに区分の説明の HelpTip を付ける', async () => {
    const view = await mountView()
    const tips = view.findAllComponents(HelpTip).map(tip => tip.props('text'))
    for (const key of ['guardduty', 'cloudtrail', 'metrics', 'insights', 'instances', 's3'] as const) expect(tips).toContain(HELP[`section_${key}`])
    expect(HELP.section_insights).toContain('GPS 受信口')
  })
  it('glossary・comparisons・時刻・場所の無い旧記録でも描画が落ちず、キー名で出す', async () => {
    const detail = makeDetail()
    detail.summary!.sources.metrics = { status: 'ok' }
    detail.summary!.top = { lambda_errors: [{ function: 'example-fn', count: 7 }] }
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const view = await mountView()
    const metrics = view.get('[data-test="detail-metrics"]')
    const counter = metrics.get('[data-test="counter-row"]').findAll('td').map(td => td.text())
    expect(counter[0]).toBe('waf.blocked')
    expect(counter.slice(1, 6)).toEqual(['231', '—', '—', '', '—'])
    expect(metrics.text()).toContain('Lambda エラーの関数（上位）')
    expect(metrics.find('[data-test="ai-basis"]').exists()).toBe(false)
    const finding = view.get('[data-test="detail-cloudtrail"] [data-test="finding-row"]').findAll('td').map(td => td.text())
    expect(finding.slice(2)).toEqual(['3', '—', '—'])
    expect(view.get('[data-test="detail-cloudtrail"]').find('[data-test="ai-basis"]').exists()).toBe(false)
  })
  it('v1 の読み替え応答でも集計値の表に基準を出す', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeV1Detail())
    const view = await mountView()
    const counter = view.get('[data-test="detail-metrics"] [data-test="counter-row"]').findAll('td').map(td => td.text())
    expect(counter.slice(0, 5)).toEqual(['waf.blocked', '231', '210', '×1.10', ''])
  })
})

describe('「ログを見る」（v2.1 第 2 段階）', () => {
  const RESULT = { source: 'CloudTrail イベント履歴 ap-northeast-1', range: { from: '2026-10-14T00:00:00+09:00', to: '2026-10-15T00:00:00+09:00' },
    rows: [{ time: '2026-10-14T10:00:00+09:00', event: 'ListBuckets', actor: 'user-example', ip: '203.0.113.5', event_id: 'evt-1', link: CONSOLE_LINKS.cloudtrail }], truncated: false }
  const logButtons = (selector: string) => wrapper.get(selector).findAll('[data-test="log-button"]')
  const logDialogs = () => document.body.querySelectorAll('[data-test="log-dialog"]')
  beforeEach(() => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeTraceDetail())
    vi.mocked(reportApi.startLogs).mockResolvedValue({ query_id: 'q1', status: 'succeeded', result: RESULT })
  })
  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })
  it('admin 以外にはボタンを出さない', async () => {
    const view = await mountView(['ops'])
    expect(view.findAll('[data-test="log-button"]')).toHaveLength(0)
    expect(view.text()).not.toContain('ログを見る')
  })
  it('admin には ref のある所見・明細・上位表と指標の集計値の行にだけボタンを出す', async () => {
    await mountView(['admin'])
    for (const section of ['cloudtrail', 'metrics', 'insights', 'guardduty']) {
      expect(logButtons(`[data-test="detail-${section}"] [data-test="finding-row"]`)).toHaveLength(1)
    }
    const metricsCounters = wrapper.get('[data-test="detail-metrics"] [data-test="counter-table"]').findAll('[data-test="counter-row"]')
    expect(metricsCounters.map(row => row.find('[data-test="log-button"]').exists())).toEqual([true, true])
    expect(wrapper.get('[data-test="detail-metrics"]').findAll('[data-test="log-button"]')).toHaveLength(4)
    expect(logButtons('[data-test="detail-insights"] [data-test="counter-table"]')).toHaveLength(0)
    expect(logButtons('[data-test="detail-guardduty"] [data-test="counter-table"]')).toHaveLength(0)
    expect(wrapper.get('[data-test="detail-insights"]').findAll('[data-test="log-button"]')).toHaveLength(1)
    expect(wrapper.get('[data-test="detail-guardduty"]').findAll('[data-test="log-button"]')).toHaveLength(1)
    expect(wrapper.get('[data-test="detail-cloudtrail"]').findAll('[data-test="log-button"]')).toHaveLength(1)
    expect(wrapper.get('[data-test="detail-instances"]').findAll('[data-test="log-button"]')).toHaveLength(0)
  })
  it('ref の無い行（旧記録・壊れた ref）にはボタンを出さない', async () => {
    const detail = makeTraceDetail()
    const insightsFinding = detail.summary!.findings.find(finding => finding.section === 'insights')!
    delete insightsFinding.ref
    const guarddutyFinding = detail.summary!.findings.find(finding => finding.section === 'guardduty')!
    guarddutyFinding.ref = { kind: 'guardduty' } as never
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    await mountView(['admin'])
    expect(logButtons('[data-test="detail-insights"] [data-test="finding-row"]')).toHaveLength(0)
    expect(logButtons('[data-test="detail-guardduty"] [data-test="finding-row"]')).toHaveLength(0)
    expect(wrapper.get('[data-test="detail-insights"]').findAll('[data-test="log-button"]')).toHaveLength(0)
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail())
    await router.push('/days/2026-10-13')
    await flushPromises()
    expect(wrapper.findAll('[data-test="finding-row"] [data-test="log-button"]')).toHaveLength(0)
    expect(logButtons('[data-test="detail-cloudtrail"]')).toHaveLength(0)
  })
  it('所見のボタンでダイアログを開き、ref と同じ本文で startLogs を呼び、所見のコンソールを添える', async () => {
    await mountView(['admin'])
    const finding = makeTraceDetail().summary!.findings.find(item => item.section === 'cloudtrail')!
    await logButtons('[data-test="detail-cloudtrail"] [data-test="finding-row"]')[0]!.trigger('click')
    await flushPromises()
    expect(reportApi.startLogs).toHaveBeenCalledWith('2026-10-14', finding.ref)
    expect(logDialogs()).toHaveLength(1)
    const dialog = logDialogs()[0]!
    expect(dialog.textContent).toContain('CloudTrail ap-northeast-1 user-example')
    expect(dialog.textContent).toContain('CloudTrail イベント履歴 ap-northeast-1')
    expect(dialog.querySelector('[data-test="log-console"]')?.getAttribute('href')).toBe(CONSOLE_LINKS.cloudtrail)
  })
  it('上位表と集計値のボタンも同じダイアログを使い回し、それぞれの参照で問い合わせる', async () => {
    await mountView(['admin'])
    const metrics = wrapper.get('[data-test="detail-metrics"]')
    await metrics.get('[data-test="counter-table"] [data-test="log-button"]').trigger('click')
    await flushPromises()
    expect(reportApi.startLogs).toHaveBeenLastCalledWith('2026-10-14', { kind: 'metric', key: 'waf.blocked' })
    ;(document.body.querySelector('[data-test="log-close"]') as HTMLElement).click()
    await flushPromises()
    const lambdaRow = metrics.findAll('tbody tr').find(row => row.text().includes('example-fn'))!
    await lambdaRow.get('[data-test="log-button"]').trigger('click')
    await flushPromises()
    expect(reportApi.startLogs).toHaveBeenLastCalledWith('2026-10-14', { kind: 'lambda', function: 'example-fn' })
    expect(logDialogs()).toHaveLength(1)
  })
  it('閉じるとポーリングを止める', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    vi.mocked(reportApi.startLogs).mockResolvedValue({ query_id: 'q1', status: 'running' })
    vi.mocked(reportApi.getLogs).mockResolvedValue({ status: 'running' })
    await mountView(['admin'])
    await logButtons('[data-test="detail-guardduty"] [data-test="finding-row"]')[0]!.trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="log-running"]')).toBeTruthy()
    await vi.advanceTimersByTimeAsync(2000)
    expect(reportApi.getLogs).toHaveBeenCalledTimes(1)
    ;(document.body.querySelector('[data-test="log-close"]') as HTMLElement).click()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    expect(reportApi.getLogs).toHaveBeenCalledTimes(1)
  })
})

describe('S3 区分', () => {
  beforeEach(() => { vi.mocked(reportApi.getDay).mockResolvedValue(makeS3Detail()) })
  it('インスタンスの後に S3 を表示し、3 段階のバケットと前日比・容量・要求を描画する', async () => {
    const view = await mountView(['admin'])
    const sections = view.findAll('[data-test="detail-instances"], [data-test="detail-s3"]')
    expect(sections.map(section => section.attributes('data-test'))).toEqual(['detail-instances', 'detail-s3'])
    const s3 = view.get('[data-test="detail-s3"]')
    expect(s3.get('h2').text()).toBe('S3')
    const table = s3.get('[data-test="s3-buckets"]')
    expect(table.findAll('th').map(th => th.text())).toEqual(['バケット', 'リージョン', 'オブジェクト数', '前日差', '容量', '前日比', 'ダウンロード量', '削除要求', '判定'])
    const rows = table.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.findAll('td').map(td => td.text())).toEqual(['archive-ok', 'ap-northeast-1', '10,000', '+1,000 件 (+11.1%)', '1.0 TB', '+2.4%', '—', '—', 'OK'])
    expect(rows[1]!.findAll('td').map(td => td.text())).toEqual(['download-warning', 'us-east-1', '1,200', '—', '1.0 GB', '—', '1.0 GB', '0', 'Warning'])
    expect(rows[2]!.findAll('td').map(td => td.text())).toEqual(['delete-critical', 'ap-northeast-1', '0', '-1,200 件 (-100.0%)', '0 B', '-100.0%', '0 B', '120', 'Critical'])
    for (const index of [3, 5]) expect(rows[2]!.findAll('td')[index]!.classes()).toContain('text-error')
    for (const index of [3, 5]) expect(rows[0]!.findAll('td')[index]!.classes()).not.toContain('text-error')
    expect(rows[0]!.get('a').attributes()).toMatchObject({ href: 'https://ap-northeast-1.console.aws.amazon.com/s3/buckets/archive-ok?region=ap-northeast-1&tab=metrics', target: '_blank', rel: 'noopener' })
    expect(rows[1]!.get('a').attributes('href')).toBe('https://us-east-1.console.aws.amazon.com/s3/buckets/download-warning?region=us-east-1&tab=metrics')
    expect(rows[2]!.get('a').attributes('href')).toBe('https://ap-northeast-1.console.aws.amazon.com/s3/buckets/delete-critical?region=ap-northeast-1&tab=metrics')
    expect(s3.findAll('[data-test="log-button"]')).toHaveLength(0)
    expect(s3.text()).not.toContain('ログを見る')
    expect(s3.text()).toContain('取得状況: 取得済み')
    expect(s3.text()).toContain('リージョン: ap-northeast-1 / us-east-1')
    expect(s3.text()).not.toContain('容量上位 50 件')
  })
  it('S3 の集計値を用語集・比較・判定付きの既存 CounterTable に渡し、所見と AI の区分も表示する', async () => {
    const view = await mountView(['admin'])
    const s3 = view.get('[data-test="detail-s3"]')
    const table = s3.get('[data-test="counter-table"]')
    expect(table.findAll('th').map(th => th.text())).toEqual(['指標', '件数', '基準', '比率', '判定', '時間帯'])
    const rows = table.findAll('[data-test="counter-row"]')
    expect(rows).toHaveLength(5)
    expect(rows.map(row => row.findAll('td')[0]!.text())).toEqual([
      expect.stringContaining('S3 バケット数'), expect.stringContaining('S3 オブジェクト数'), expect.stringContaining('S3 ダウンロード量'),
      expect.stringContaining('S3 削除要求'), expect.stringContaining('S3 GET 要求'),
    ])
    expect(rows[2]!.findAll('td').slice(1, 5).map(td => td.text())).toEqual(['1.0 GB', '512.0 MB', '×2.00', ''])
    expect(rows[3]!.findAll('td').slice(1, 5).map(td => td.text())).toEqual(['120', '20', '×6.00', 'Warning'])
    expect(rows[4]!.findAll('td').slice(2, 5).map(td => td.text())).toEqual(['基準なし', '—', '基準なし'])
    expect(view.findAllComponents(HelpTip).map(tip => tip.props('text'))).toContain('要求メトリクスが有効なバケットから取得したバイト数')
    expect(s3.findAll('[data-test="finding-row"]').map(row => row.text())).toEqual([
      expect.stringContaining('S3_DOWNLOAD_SPIKE'), expect.stringContaining('S3_MASS_DELETE'),
    ])
    const findings = s3.findAll('[data-test="finding-row"]')
    expect(findings.map(row => row.findAll('td')[2]!.text())).toEqual(['1', '1,200'])
    expect(findings.map(row => row.findAll('td')[4]!.text())).toEqual([
      'CloudWatch メトリクス AWS/S3 (us-east-1) コンソール',
      'CloudWatch メトリクス AWS/S3 (ap-northeast-1) コンソール',
    ])
    expect(findings.map(row => row.get('a').attributes('href'))).toEqual([
      'https://us-east-1.console.aws.amazon.com/s3/buckets/download-warning?region=us-east-1&tab=metrics',
      'https://ap-northeast-1.console.aws.amazon.com/s3/buckets/delete-critical?region=ap-northeast-1&tab=metrics',
    ])
    expect(s3.get('[data-test="ai-basis"]').text()).toBe('根拠: オブジェクト数が前日から 100.0% 減、S3 ダウンロード量')
    const adjustment = view.findAll('[aria-label="AI が変えた段階"] tbody tr').find(row => row.text().includes('大量削除と持ち出し'))!
    expect(adjustment.findAll('td')[0]!.text()).toBe('S3')
    expect(view.findAllComponents(HelpTip).map(tip => tip.props('text'))).toContain(HELP.section_s3)
    expect(HELP.section_s3).toBe('全バケットの容量とオブジェクト数の前日比、要求メトリクスが有効なバケットのダウンロード量と削除要求の前 7 日平均との比較です。大量削除と持ち出しの検知用です。')
    const tables = s3.findAll('[data-test="counter-table"], [data-test="s3-buckets"]')
    expect(tables.map(table => table.attributes('data-test'))).toEqual(['counter-table', 's3-buckets'])
  })
  it('前日差ゼロ・前日件数ゼロによる比率なしと、要求メトリクスなしを区別する', async () => {
    const detail = makeS3Detail()
    Object.assign(detail.summary!.top!.s3_buckets![0]!, { objects_prev: 10000, objects_diff: 0, objects_pct: 0, size_pct: 0 })
    Object.assign(detail.summary!.top!.s3_buckets![1]!, { objects_prev: 0, objects_diff: 1200, objects_pct: null })
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const s3 = (await mountView()).get('[data-test="detail-s3"]')
    const rows = s3.get('[data-test="s3-buckets"]').findAll('tbody tr')
    expect(rows[0]!.findAll('td')[3]!.text()).toBe('0 件 (0.0%)')
    expect(rows[0]!.findAll('td')[5]!.text()).toBe('0.0%')
    expect(rows[1]!.findAll('td')[3]!.text()).toBe('+1,200 件 (—)')
  })
  it.each([null, 'https://console.aws.amazon.com.evil.example/s3', 'https://evil.example/s3', 'javascript:alert(1)', 'http://console.aws.amazon.com/s3'])('バケットの不許可リンク %s をリンクにしない', async link => {
    const detail = makeS3Detail()
    detail.summary!.top!.s3_buckets![0]!.link = link
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const row = (await mountView()).get('[data-test="s3-buckets"] tbody tr')
    expect(row.find('a').exists()).toBe(false)
    expect(row.findAll('td')[0]!.text()).toBe('archive-ok')
  })
  it.each([
    [{ status: 'failed', error: 'AccessDenied', note: 'S3 の参照権限がありません' }, '未集計（AccessDenied）'],
    [{ status: 'disabled', note: '設定で無効' }, '停止中（設定）'],
  ] satisfies [S3SourceResult, string][])('取得状況 %j の日に任意項目がなくても全区分を描画する', async (source, status) => {
    const detail = makeS3Detail()
    detail.summary!.sources.s3 = source
    delete detail.summary!.top
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const view = await mountView()
    const s3 = view.get('[data-test="detail-s3"]')
    expect(s3.text()).toContain(`取得状況: ${status}`)
    expect(s3.text()).toContain(source.note)
    expect(s3.text()).not.toContain('リージョン:')
    expect(s3.find('[data-test="s3-buckets"]').exists()).toBe(false)
    for (const section of ['guardduty', 'cloudtrail', 'metrics', 'insights', 'instances']) {
      expect(view.find(`[data-test="detail-${section}"]`).exists()).toBe(true)
    }
  })
  it('正常取得の日も note を表示し、regions 等の任意項目がなくても描画する', async () => {
    const detail = makeS3Detail()
    detail.summary!.sources.s3 = { status: 'ok', note: '表は 50 バケットまで（全 61）' }
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const s3 = (await mountView()).get('[data-test="detail-s3"]')
    expect(s3.text()).toContain('取得状況: 取得済み')
    expect(s3.text()).toContain('表は 50 バケットまで（全 61）')
    expect(s3.text()).not.toContain('リージョン:')
  })
  it.each<[number | null, number | null]>([[null, 1024], [100, null], [null, null]])('片方だけの指標 objects=%s / size=%s も欠損を — として表示する', async (objects, size) => {
    const detail = makeS3Detail()
    const bucket = detail.summary!.top!.s3_buckets![0]!
    bucket.objects = objects
    bucket.size = size
    bucket.objects_prev = bucket.objects_diff = bucket.objects_pct = bucket.size_prev = bucket.size_pct = null
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    const cells = (await mountView()).get('[data-test="s3-buckets"] tbody tr').findAll('td')
    expect(cells.slice(2, 6).map(td => td.text())).toEqual([
      objects === null ? '—' : '100', '—', size === null ? '—' : '1.0 KB', '—',
    ])
  })
  it('バックエンド生成の daily_s3.json をバケット・所見・容量付きで描画する', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ summary: { ...structuredClone(dailyS3), ai: makeAi({ status: 'skipped' }) } as DailySummary }))
    const s3 = (await mountView()).get('[data-test="detail-s3"]')
    const rows = s3.get('[data-test="s3-buckets"]').findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[0]!.findAll('td').slice(2, 6).map(td => td.text())).toEqual(['40,000', '-60,000 件 (-60.0%)', '20.0 GB', '-60.0%'])
    expect(rows[0]!.get('a').attributes('href')).toBe(dailyS3.top.s3_buckets[0]!.link)
    const download = s3.findAll('[data-test="counter-row"]').find(row => row.text().includes('s3.bytes_downloaded'))!
    expect(download.findAll('td').slice(1, 3).map(td => td.text())).toEqual(['12.3 GB', '3.0 GB'])
    const findings = s3.findAll('[data-test="finding-row"]')
    expect(findings.map(row => row.findAll('td')[2]!.text())).toEqual(['60,000', '1', '4,800', '1'])
  })
  it('truncated のときバックエンドの注記を表示する', async () => {
    const detail = makeS3Detail()
    detail.summary!.sources.s3!.truncated = true
    detail.summary!.sources.s3!.note = '表は 50 バケットまで（全 60）'
    vi.mocked(reportApi.getDay).mockResolvedValue(detail)
    expect((await mountView()).get('[data-test="detail-s3"]').text()).toContain('表は 50 バケットまで（全 60）')
  })
  it('S3 のない旧記録でも区分を描画できる', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(makeV1Detail())
    const s3 = (await mountView()).get('[data-test="detail-s3"]')
    expect(s3.text()).toContain('取得状況: 情報なし')
    expect(s3.find('[data-test="s3-buckets"]').exists()).toBe(false)
  })
})
