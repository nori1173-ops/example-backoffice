import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { makeDay } from './fixtures'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { reportApi } from '../api'
import DayListView from '../views/DayListView.vue'

vi.mock('../api', () => ({ reportApi: { listDays: vi.fn() } }))
let wrapper: VueWrapper
async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', name: 'daily-security-report.days', component: DayListView },
    { path: '/days/:date', name: 'daily-security-report.day', component: { template: '<p>detail</p>' } },
  ] })
  await router.push('/')
  await router.isReady()
  wrapper = mount({ components: { DayListView }, template: '<KeepAlive><DayListView /></KeepAlive>' }, {
    global: { plugins: [pinia, router, createVuetify({ components, directives })] },
  })
  await flushPromises()
  return wrapper
}
beforeEach(() => vi.resetAllMocks())
afterEach(() => wrapper?.unmount())
describe('DayListView', () => {
  it('日付・段階・状態・AI の見解・配布チップ・JST を表示する', async () => {
    vi.mocked(reportApi.listDays).mockResolvedValue([
      makeDay({ date: '2026-10-14', status: 'succeeded', red_count: 3, notify_channels: ['email', 'slack'], notified_at: '2026-10-15T07:01:23+09:00', sent_count: 1 }),
      makeDay({ date: '2026-10-13', status: 'failed', red_count: 0, notify_channels: [], notify_note: '配布先が無効のため送付なし', sent_count: 0, error: 'TIMEOUT(推定)' }),
      makeDay({ date: '2026-10-12', status: 'running', red_count: 0, notify_channels: [], sent_count: 0 }),
    ])
    const view = await mountView()
    const rows = view.findAll('[data-test="day-row"]')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('2026-10-14')
    expect(rows[0].text()).toContain('成功')
    expect(rows[0].get('[data-test="day-level"]').text()).toContain('Warning')
    expect(rows[0].get('[data-test="ai-opinion"]').text()).toBe(makeDay().ai_opinion)
    expect(rows[0].text()).toContain('メール')
    expect(rows[0].text()).toContain('Slack')
    expect(rows[0].text()).toContain('2026-10-15 07:01')
    expect(rows[1].text()).toContain('配布先が無効のため送付なし')
    expect(rows[1].text()).toContain('失敗')
    expect(rows[1].text()).toContain('TIMEOUT(推定)')
    expect(rows[2].text()).toContain('実行中')
    expect(view.text()).toContain('JST')
    expect(view.text()).not.toContain('再実行')
    await view.get('[data-test="days-refresh"]').trigger('click')
    expect(reportApi.listDays).toHaveBeenCalledTimes(2)
  })
  it.each(['queued', 'constructor'])('想定外の状態 %s を灰色のチップで表示し一覧を維持する', async status => {
    vi.mocked(reportApi.listDays).mockResolvedValue([
      makeDay({ date: '2026-10-14', status, red_count: 0, notify_channels: [], sent_count: 0 }),
      makeDay({ date: '2026-10-13', status: 'succeeded', red_count: 0, notify_channels: [], sent_count: 0 }),
    ])
    const rows = (await mountView()).findAll('[data-test="day-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].get('.v-chip').text()).toBe(status)
    expect(rows[0].get('.v-chip').classes()).toContain('text-grey')
    expect(rows[1].text()).toContain('成功')
  })
  it('date と error が無い行も空欄のまま表示できる', async () => {
    vi.mocked(reportApi.listDays).mockResolvedValue([
      makeDay({ date: undefined, status: 'failed', red_count: 0, notify_channels: [], sent_count: 0 }),
      makeDay({ date: undefined, status: 'running', red_count: 0, notify_channels: [], sent_count: 0 }),
    ])
    const rows = (await mountView()).findAll('[data-test="day-row"]')
    expect(rows).toHaveLength(2)
    for (const row of rows) {
      expect(row.get('td').text()).toBe('')
      expect(row.find('.text-caption.text-error').exists()).toBe(false)
      expect(row.text()).not.toContain('undefined')
      expect(row.text()).not.toContain('TIMEOUT')
    }
  })
  it('空一覧を表示する', async () => {
    vi.mocked(reportApi.listDays).mockResolvedValue([])
    expect((await mountView()).text()).toContain('レポートはまだありません')
  })
  it('読み出しの失敗を表示し更新で回復する', async () => {
    vi.mocked(reportApi.listDays).mockRejectedValue(new Error('offline'))
    const view = await mountView()
    expect(view.get('[data-test="days-error"]').text()).toContain('取得に失敗')
    vi.mocked(reportApi.listDays).mockResolvedValue([])
    await view.get('[data-test="days-refresh"]').trigger('click')
    await flushPromises()
    expect(view.find('[data-test="days-error"]').exists()).toBe(false)
  })
})


describe('DayListView v2', () => {
  it.each([['critical', 'Critical', 'error'], ['warning', 'Warning', 'warning'], ['ok', 'OK', 'success']] as const)('%s を文字と色で示す', async (level, label, color) => {
    vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ level })])
    const view = await mountView()
    const chip = view.get('[data-test="day-level"] .v-chip')
    expect(chip.text()).toBe(label)
    expect(chip.classes()).toContain(`text-${color}`)
    expect(view.get('[data-test="day-detail"]').attributes('href')).toBe('/days/2026-10-14')
  })
  it('見解を Unicode の 120 字で切り、詳細に誘導する', async () => {
    vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ ai_opinion: '😀'.repeat(121) })])
    const view = await mountView()
    expect(view.get('[data-test="ai-opinion"]').text()).toBe('😀'.repeat(120) + '…')
  })
  it('120 字ちょうどは省略しない', async () => {
    vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ ai_opinion: 'あ'.repeat(120) })])
    expect((await mountView()).get('[data-test="ai-opinion"]').text()).toBe('あ'.repeat(120))
  })
  it.each([['disabled', '取得できず（設定で無効）'], ['failed', '取得できず（NO_TOOL_USE）'], ['refused', '取得できず（refusal）'], ['timeout', '取得できず（TIMEOUT）'], ['skipped', '取得できず（呼ぶ時間不足）']] as const)('AI %s をグレーで示す', async (ai_status, label) => {
    vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ ai_status, ai_opinion: label })])
    const cell = (await mountView()).get('[data-test="ai-opinion"]')
    expect(cell.text()).toBe(label)
    expect(cell.classes()).toContain('text-medium-emphasis')
  })
  it('v1 は grey 注記、未完了の段階はダッシュ', async () => {
    vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ legacy: true }), makeDay({ status: 'failed' }), makeDay({ status: 'running' })])
    const rows = (await mountView()).findAll('[data-test="day-row"]')
    expect(rows[0].get('[data-test="day-level"]').text()).toContain('v1')
    expect(rows[0].get('[data-test="day-level"] .text-grey').text()).toBe('v1')
    for (const row of rows.slice(1)) expect(row.get('[data-test="day-level"]').text()).toBe('—')
  })
})


it('v1 の AI 判定なしを理由付きで表示する', async () => {
  vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ legacy: true, ai_status: 'disabled', ai_opinion: '取得できず（v1 は AI 判定なし）' })])
  expect((await mountView()).get('[data-test="ai-opinion"]').text()).toBe('取得できず（v1 は AI 判定なし）')
})

it('失敗時の理由付き見解も Unicode の 120 字で切る', async () => {
  const opinion = '取得できず（' + '😀'.repeat(130) + '）'
  vi.mocked(reportApi.listDays).mockResolvedValue([makeDay({ ai_status: 'failed', ai_opinion: opinion })])
  expect((await mountView()).get('[data-test="ai-opinion"]').text()).toBe([...opinion].slice(0, 120).join('') + '…')
})
