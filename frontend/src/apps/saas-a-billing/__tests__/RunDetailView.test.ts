import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { billingApi } from '../api'
import EnvBadge from '../components/EnvBadge.vue'
import type {
  BillingSummary,
  DeviceJudgement,
  MissingDevice,
  RunDetail,
  RunResult,
  RunSummary,
  SettingsView,
  TargetEnv,
  TenantResult,
} from '../types'
import RunDetailView from '../views/RunDetailView.vue'

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

vi.mock('../api', () => ({ billingApi: { getRun: vi.fn(), getSettings: vi.fn() } }))

function makeSettings(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    target_env: 'verification',
    cutoff_day: 20,
    cutoff_time: '24:00',
    auto_retry_days: 5,
    auto_run_enabled: false,
    auto_run_from_month: '2026-01',
    mail_enabled: true,
    slack_enabled: true,
    mail_to: ['devA@example.co.jp'],
    mail_cc: [],
    slack_channel_id: 'C0123ABCD',
    slack_channel_label: '#billing',
    slack_mention: '',
    slack_template: '{month}',
    exclude_keywords: ['デモ'],
    camera_format_names: [],
    version: 3,
    updated_at: '2026-08-01T00:00:00+09:00',
    updated_by: 'sub-1',
    updated_by_email: 'devA@example.co.jp',
    saasa_host: 'example.invalid',
    targets: { verification: true, production: true },
    slack_mode: 'bot',
    ...overrides,
  }
}

function makeDevice(overrides: Partial<DeviceJudgement> = {}): DeviceJudgement {
  return {
    device_cd: '10000021-21-LoggerA-400',
    device_name: '地点A',
    group_cds: ['g1'],
    group_names: ['現場A'],
    format_name: 'LoggerA-400',
    item_count: 984,
    device_description: '',
    is_camera: false,
    camera_source: null,
    retention: 3,
    received: true,
    first_received_at: '2026/09/21 00:05:00',
    last_received_at: '2026/10/20 23:55:00',
    boundary_only: false,
    judgement: 'billable',
    unit_price: 1350,
    amount: 1350,
    flags: [],
    note: '',
    ...overrides,
  }
}

function makeTenant(overrides: Partial<TenantResult> = {}): TenantResult {
  return {
    tenant_id: '00000000-0000-0000-0000-000000000011',
    tenant_cd: 'T001',
    tenant_name: '○○株式会社',
    tenant_description: '',
    retention_period: 3,
    device_count_api: 3,
    status: 'ok',
    error: null,
    export_calls: 5,
    export_bytes: 55000000,
    subtotal: 1350,
    counts: { billable: 1, no_data: 0, excluded: 0, review: 0 },
    devices: [makeDevice()],
    ...overrides,
  }
}

function makeSummary(overrides: Partial<BillingSummary> = {}): BillingSummary {
  return {
    tenant_count: 12,
    device_count: 340,
    billable_count: 300,
    no_data_count: 25,
    excluded_count: 15,
    review_count: 2,
    failed_tenant_count: 0,
    missing_count: 1,
    total_amount: 312000,
    ...overrides,
  }
}

function makeResult(overrides: Partial<RunResult> = {}): RunResult {
  return {
    schema: 'saas-a-billing.result.v1',
    target_env: 'verification',
    month: '2026-10',
    closing_period: {
      from: '2026-09-21T00:00:00+09:00',
      to: '2026-10-21T00:00:00+09:00',
      cutoff_day: 20,
      cutoff_time: '24:00',
      open: false,
    },
    run: {
      attempt: 1,
      trigger: 'scheduled',
      started_at: '2026-10-21T06:00:12+09:00',
      finished_at: '2026-10-21T06:04:40+09:00',
      requested_by: null,
      env: 'dev',
      target_env: 'verification',
      saasa_host: 'example.invalid',
      execution_arn: 'arn:aws:states:ap-northeast-1:000000000000:execution:x',
    },
    settings_snapshot: { exclude_keywords: ['デモ'], camera_format_names: [] },
    price_table: {
      effective_from: '2025-09-01',
      bands: [
        { max_items: 500, price_3y: 650, price_1y: 450 },
        { max_items: 800, price_3y: 1000, price_1y: 700 },
        { max_items: 1600, price_3y: 1200, price_1y: 900 },
        { max_items: 1700, price_3y: 1350, price_1y: 1000 },
      ],
      camera: { price_3y: 1800, price_1y: 1400 },
      note: 'support-tracker #2001',
    },
    summary: makeSummary(),
    tenants: [makeTenant()],
    missing_devices: [],
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
    started_at: '2026-10-21T06:00:12+09:00',
    finished_at: '2026-10-21T06:04:40+09:00',
    summary: makeSummary(),
    s3_xlsx_key: 'saas-a-billing/verification/2026/10/report.xlsx',
    ...overrides,
  }
}

function makeDetail(overrides: Partial<RunDetail> = {}): RunDetail {
  return { run: makeRun(), result: makeResult(), ...overrides }
}

function makeMissing(overrides: Partial<MissingDevice> = {}): MissingDevice {
  return {
    tenant_cd: 'T002',
    tenant_name: '△△工業',
    device_cd: '30000002-11-LoggerA-210',
    device_name: '消えた地点X',
    group_names: ['現場B'],
    last_seen_month: '2026-09',
    ...overrides,
  }
}

async function mountView(targetEnv: TargetEnv = 'verification'): Promise<VueWrapper> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/saas-a-billing', name: 'saas-a-billing.runs', component: { template: '<div />' } },
      {
        path: '/saas-a-billing/runs/:targetEnv/:month',
        name: 'saas-a-billing.run-detail',
        component: RunDetailView,
      },
    ],
  })
  await router.push(`/saas-a-billing/runs/${targetEnv}/2026-10`)
  await router.isReady()

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(RunDetailView, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

function deviceRowTexts(wrapper: VueWrapper): string[] {
  return wrapper.findAll('[data-test="device-row"]').map((row) => row.text())
}

beforeEach(() => {
  document.body.innerHTML = ''
  holder.runs.runs = []
  holder.runs.loading = false
  holder.runs.error = null
  holder.runs.targetEnvFilter = null
  holder.runs.fetchRuns.mockReset()
  holder.runs.download.mockReset()
  holder.runs.createRun.mockReset()
  holder.runs.pollRun.mockReset()
  vi.mocked(billingApi.getRun).mockResolvedValue(makeDetail())
  vi.mocked(billingApi.getSettings).mockResolvedValue(makeSettings())
})

describe('RunDetailView', () => {
  it('サマリの 9 項目が表示される', async () => {
    const wrapper = await mountView()

    const items = wrapper.findAll('[data-test="summary-item"]')
    expect(items).toHaveLength(9)
    const text = items.map((item) => item.text()).join(' ')
    for (const label of [
      'テナント数',
      '地点数',
      '課金',
      '通信なし',
      '除外',
      '要確認',
      '取得失敗',
      '消えた地点',
      '合計金額',
    ]) {
      expect(text).toContain(label)
    }
    expect(text).toContain('¥312,000')
  })

  it('テナント → 現場 → 地点の階層で表示される', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({
              devices: [
                makeDevice({ device_cd: 'A1', group_names: ['現場A'] }),
                makeDevice({ device_cd: 'A2', group_names: ['現場A'] }),
                makeDevice({ device_cd: 'B1', group_names: ['現場B'] }),
              ],
            }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    const kinds = wrapper
      .find('[data-test="tree"]')
      .findAll('tbody tr')
      .map((row) => row.attributes('data-test'))
    expect(kinds).toEqual([
      'tenant-row',
      'group-row',
      'device-row',
      'device-row',
      'group-row',
      'device-row',
    ])
    expect(wrapper.find('[data-test="tenant-row"]').text()).toContain('T001')
    expect(wrapper.findAll('[data-test="group-row"]')[1].text()).toContain('現場B')
  })

  it('通信なしの行がアンバー', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({ devices: [makeDevice({ judgement: 'no_data', amount: 0 })] }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="device-row"]').attributes('style')).toContain('#FDF3DC')
  })

  it('除外の行が青', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({ devices: [makeDevice({ judgement: 'excluded', amount: 0 })] }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="device-row"]').attributes('style')).toContain('#DFE9F8')
  })

  it('取得失敗の行が灰', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({
              status: 'failed',
              error: 'TIMEOUT',
              devices: [makeDevice({ judgement: 'failed', amount: 0 })],
            }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="device-row"]').attributes('style')).toContain('#E0E0E0')
  })

  it('要確認の地点が赤文字でフラグ理由付き', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({ devices: [makeDevice({ flags: ['OVER_MAX_ITEMS'], item_count: 1800 })] }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    const review = wrapper.find('[data-test="device-row"] .judgement-chip__review')
    expect(review.exists()).toBe(true)
    expect(review.text()).toContain('要確認')
    expect(review.text()).toContain('OVER_MAX_ITEMS')
    expect(review.attributes('style')).toContain('#C00000')
  })

  it('消えた地点が灰字の別ブロックに出る', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({ result: makeResult({ missing_devices: [makeMissing()] }) }),
    )
    const wrapper = await mountView()

    const block = wrapper.find('[data-test="missing-devices"]')
    expect(block.exists()).toBe(true)
    expect(block.text()).toContain('消えた地点X')
    expect(deviceRowTexts(wrapper).join(' ')).not.toContain('消えた地点X')
    expect(block.find('[data-test="missing-row"]').attributes('style')).toContain('#808080')
  })

  it('判定フィルタで行が絞られる', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({
              devices: [
                makeDevice({ device_cd: 'A1', device_name: '課金地点' }),
                makeDevice({ device_cd: 'A2', device_name: '無通信地点', judgement: 'no_data' }),
              ],
            }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()
    expect(deviceRowTexts(wrapper)).toHaveLength(2)

    const filter = wrapper.find('[data-test="judgement-filter"]')
    const noData = filter.findAll('button').find((button) => button.text().includes('通信なし'))
    await noData!.trigger('click')
    await flushPromises()

    const rows = deviceRowTexts(wrapper)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toContain('無通信地点')
  })

  it('要確認のみフィルタ', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({
              devices: [
                makeDevice({ device_cd: 'A1', device_name: '通常地点' }),
                makeDevice({
                  device_cd: 'A2',
                  device_name: '要確認地点',
                  judgement: 'excluded',
                  flags: ['NO_ITEMS'],
                }),
              ],
            }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    await wrapper.find('[data-test="review-only"] input').setValue(true)
    await flushPromises()

    const rows = deviceRowTexts(wrapper)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toContain('要確認地点')
  })

  it('境界日のみ受信フィルタ', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({
              devices: [
                makeDevice({ device_cd: 'A1', device_name: '通常地点' }),
                makeDevice({
                  device_cd: 'A2',
                  device_name: '境界地点',
                  boundary_only: true,
                  note: '境界日のみ受信',
                }),
              ],
            }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    await wrapper.find('[data-test="boundary-only"] input').setValue(true)
    await flushPromises()

    const rows = deviceRowTexts(wrapper)
    expect(rows).toHaveLength(1)
    expect(rows[0]).toContain('境界地点')
  })

  it('使用単価表が表示される', async () => {
    const wrapper = await mountView()

    const table = wrapper.find('[data-test="price-table"]')
    expect(table.exists()).toBe(true)
    expect(table.text()).toContain('2025/09/01')
    expect(table.text()).toContain('¥650')
    expect(table.text()).toContain('¥450')
    expect(table.text()).toContain('¥1,800')
    expect(table.text()).toContain('カメラ地点')
  })

  it('課金対象期間が 23:59:59 表記で出る', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="closing-period"]').text()).toContain(
      '2026/09/21 00:00:00 〜 2026/10/20 23:59:59',
    )
  })

  it('環境バッジが出る', async () => {
    const wrapper = await mountView()

    const badge = wrapper.findComponent(EnvBadge)
    expect(badge.exists()).toBe(true)
    expect(badge.classes()).toContain('bg-orange')
    expect(badge.text()).toContain('検証環境')
  })

  it('再実行ボタンが確認ダイアログを出す', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()

    expect(holder.runs.createRun).not.toHaveBeenCalled()
    const dialog = document.body.querySelector('[data-test="rerun-dialog"]')
    expect(dialog).not.toBeNull()
    expect(dialog!.textContent).toContain('2026-10')

    const confirm = document.body.querySelector('[data-test="rerun-confirm"]') as HTMLElement
    confirm.click()
    await flushPromises()

    expect(holder.runs.createRun).toHaveBeenCalledWith({
      month: '2026-10',
      targetEnv: 'verification',
      distribute: false,
      force: true,
    })
  })

  it.each([
    [{ mail_enabled: true, slack_enabled: true }, '配布先: メール・Slack'],
    [{ mail_enabled: true, slack_enabled: false }, '配布先: メール'],
    [{ mail_enabled: false, slack_enabled: false }, '配布先: なし（設定で配布が無効）'],
  ] satisfies [Partial<SettingsView>, string][])('再実行ダイアログに現在の %s を表示する', async (overrides, label) => {
    vi.mocked(billingApi.getSettings).mockResolvedValue(makeSettings(overrides))
    const wrapper = await mountView()

    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()

    const targets = document.body.querySelector('[data-test="distribute-targets"]')
    expect(targets?.textContent).toContain(label)
  })
})
