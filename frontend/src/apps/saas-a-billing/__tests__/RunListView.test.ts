import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { billingApi } from '../api'
import EnvBadge from '../components/EnvBadge.vue'
import StatusChip from '../components/StatusChip.vue'
import type { BillingSummary, RunSummary, SettingsView, TargetEnv } from '../types'
import RunListView from '../views/RunListView.vue'

interface RunsStoreMock {
  runs: RunSummary[]
  loading: boolean
  error: string | null
  targetEnvFilter: TargetEnv | null
  fetchRuns: Mock
  download: Mock
  createRun: Mock
  pollRun: Mock
}

const holder = vi.hoisted(() => ({ runs: null as unknown as RunsStoreMock }))

vi.mock('../stores/runs', async () => {
  const { reactive } = await import('vue')
  holder.runs = reactive({
    runs: [],
    loading: false,
    error: null,
    targetEnvFilter: null,
    fetchRuns: vi.fn(),
    download: vi.fn(),
    createRun: vi.fn(),
    pollRun: vi.fn(),
  }) as RunsStoreMock
  return { useRunsStore: () => holder.runs }
})

vi.mock('../api', () => ({ billingApi: { getSettings: vi.fn() } }))

const StubView = { template: '<div />' }

function makeSummary(overrides: Partial<BillingSummary> = {}): BillingSummary {
  return {
    tenant_count: 12,
    device_count: 340,
    billable_count: 300,
    no_data_count: 20,
    excluded_count: 15,
    review_count: 3,
    failed_tenant_count: 1,
    missing_count: 2,
    total_amount: 1234567,
    ...overrides,
  }
}

function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    month: '2026-10',
    target_env: 'verification',
    status: 'succeeded',
    trigger: 'scheduled',
    attempt: 1,
    requested_by: null,
    requested_by_email: null,
    distribute: true,
    force: false,
    started_at: '2026-11-21T06:00:00+09:00',
    finished_at: '2026-11-21T06:12:00+09:00',
    summary: makeSummary(),
    s3_xlsx_key: 'saas-a-billing/verification/2026/10/report.xlsx',
    ...overrides,
  }
}

function makeSettings(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    target_env: 'verification',
    cutoff_day: 20,
    cutoff_time: '24:00',
    auto_retry_days: 5,
    auto_run_enabled: true,
    auto_run_from_month: '2026-01',
    mail_enabled: false,
    slack_enabled: false,
    mail_to: ['devA@example.co.jp'],
    mail_cc: [],
    slack_channel_id: 'C0123ABCD',
    slack_channel_label: '#proj-saas-a-billing',
    slack_mention: '',
    slack_template: '{month} のレポートです',
    exclude_keywords: ['デモ'],
    camera_format_names: [],
    version: 3,
    updated_at: '2026-11-01T10:00:00+09:00',
    updated_by: 'sub',
    updated_by_email: 'devA@example.co.jp',
    saasa_host: 'example.invalid',
    targets: { verification: true, production: true },
    slack_mode: 'bot',
    ...overrides,
  }
}

async function mountView(): Promise<VueWrapper> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/saas-a-billing', name: 'saas-a-billing.runs', component: StubView },
      {
        path: '/saas-a-billing/runs/:targetEnv/:month',
        name: 'saas-a-billing.run-detail',
        component: StubView,
      },
    ],
  })
  await router.push('/saas-a-billing')
  await router.isReady()

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(RunListView, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  holder.runs.runs = []
  holder.runs.loading = false
  holder.runs.error = null
  holder.runs.targetEnvFilter = null
  holder.runs.fetchRuns.mockReset()
  holder.runs.download.mockReset()
  holder.runs.createRun.mockReset()
  holder.runs.pollRun.mockReset()
  vi.mocked(billingApi.getSettings).mockResolvedValue(makeSettings())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RunListView', () => {
  it('検証環境の行にオレンジのバッジが出る', async () => {
    holder.runs.runs = [makeRun({ target_env: 'verification' })]
    const wrapper = await mountView()

    const badges = wrapper.findAllComponents(EnvBadge)
    expect(badges).toHaveLength(1)
    expect(badges[0].classes()).toContain('bg-orange')
    expect(badges[0].text()).toContain('検証環境')
  })

  it('本番の行に緑のバッジが出る', async () => {
    holder.runs.runs = [makeRun({ target_env: 'production' })]
    const wrapper = await mountView()

    const badges = wrapper.findAllComponents(EnvBadge)
    expect(badges).toHaveLength(1)
    expect(badges[0].classes()).toContain('bg-green')
    expect(badges[0].text()).toContain('本番')
  })

  it('環境フィルタを変えると fetchRuns が呼ばれる', async () => {
    const wrapper = await mountView()
    expect(holder.runs.fetchRuns).toHaveBeenCalledWith(undefined)

    const filter = wrapper.find('[data-test="env-filter"]')
    const production = filter.findAll('button').find((button) => button.text().includes('本番'))
    await production!.trigger('click')
    await flushPromises()

    expect(holder.runs.fetchRuns).toHaveBeenLastCalledWith('production')
  })

  it('合計金額が通貨表記で出る', async () => {
    holder.runs.runs = [makeRun({ summary: makeSummary({ total_amount: 1234567 }) })]
    const wrapper = await mountView()

    expect(wrapper.text()).toContain('¥1,234,567')
  })

  it('状態に応じて色が変わる', async () => {
    holder.runs.runs = [
      makeRun({ month: '2026-10', status: 'succeeded' }),
      makeRun({ month: '2026-09', status: 'failed', error: 'TIMEOUT(推定)' }),
      makeRun({ month: '2026-08', status: 'running' }),
    ]
    const wrapper = await mountView()

    const chips = wrapper.findAllComponents(StatusChip)
    expect(chips).toHaveLength(3)
    expect(chips[0].classes()).toContain('bg-green')
    expect(chips[1].classes()).toContain('bg-red')
    expect(chips[2].classes()).toContain('bg-blue')
  })

  // ダウンロードは status ∈ {succeeded, distributed} でのみ有効。
  // API が succeeded / distributed 以外を 404 にするため、s3_xlsx_key の有無では判定しない。
  it('ダウンロードボタンが s3_xlsx_key の無い行では無効', async () => {
    holder.runs.runs = [
      makeRun({ month: '2026-10', status: 'succeeded' }),
      makeRun({ month: '2026-09', status: 'failed', s3_xlsx_key: undefined }),
      makeRun({ month: '2026-08', status: 'running' }),
    ]
    const wrapper = await mountView()

    const button = (month: string) =>
      wrapper.find(`[data-test="download-verification-${month}"]`)
    expect(button('2026-10').attributes('disabled')).toBeUndefined()
    expect(button('2026-09').attributes('disabled')).toBeDefined()
    expect(button('2026-08').attributes('disabled')).toBeDefined()

    await button('2026-10').trigger('click')
    expect(holder.runs.download).toHaveBeenCalledWith('2026-10', 'verification')
  })

  it('次回自動実行予定が設定から算出されて表示される', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(2026, 8, 1, 9, 0, 0))
    vi.mocked(billingApi.getSettings).mockResolvedValue(
      makeSettings({ cutoff_day: 20, cutoff_time: '24:00', auto_run_enabled: true }),
    )
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="next-auto-run"]').text()).toContain('2026/09/21 06:00')
  })
})
