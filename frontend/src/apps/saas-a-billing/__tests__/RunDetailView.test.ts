import { readFileSync } from 'node:fs'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
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
  downloading: string | null
  fetchRuns: Mock
  download: Mock
  createRun: Mock
  pollRun: Mock
}

const holder = vi.hoisted(() => ({ runs: null as unknown as RunsStoreMock }))

vi.mock('../stores/runs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../stores/runs')>()
  const { reactive } = await import('vue')
  holder.runs = reactive({
    runs: [],
    loading: false,
    error: null,
    downloading: null,
    fetchRuns: vi.fn(),
    download: vi.fn(),
    createRun: vi.fn(),
    pollRun: vi.fn(),
  }) as RunsStoreMock
  return { ...actual, useRunsStore: () => holder.runs }
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
    unit_price: 1400,
    amount: 1400,
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
    subtotal: 1400,
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

/** onActivated は KeepAlive 配下でのみ呼ばれるため、実際の配置に合わせて包む */
const Host = { components: { RunDetailView }, template: '<KeepAlive><RunDetailView /></KeepAlive>' }

let router: Router

async function mountView(targetEnv: TargetEnv = 'verification'): Promise<VueWrapper> {
  router = createRouter({
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
  const wrapper = mount(Host, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

function deviceRowTexts(wrapper: VueWrapper): string[] {
  return wrapper.findAll('[data-test="device-row"]').map((row) => row.text())
}

beforeEach(() => {
  document.body.innerHTML = ''
  holder.runs.runs = []
  holder.runs.loading = false
  holder.runs.error = null
  holder.runs.downloading = null
  holder.runs.fetchRuns.mockReset()
  holder.runs.download.mockReset()
  holder.runs.createRun.mockReset()
  holder.runs.pollRun.mockReset()
  vi.mocked(billingApi.getRun).mockClear().mockResolvedValue(makeDetail())
  vi.mocked(billingApi.getSettings).mockClear().mockResolvedValue(makeSettings())
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RunDetailView', () => {
  it('サマリの 8 項目が表示される', async () => {
    const wrapper = await mountView()

    const items = wrapper.findAll('[data-test="summary-item"]')
    expect(items).toHaveLength(8)
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
    ]) {
      expect(text).toContain(label)
    }
  })

  // 合計金額が小さな数字の 1 つに埋もれて読み取れなかった（要望 2026-09）
  it('合計金額が見出し直下に専用スタイルで出る', async () => {
    const wrapper = await mountView()

    const total = wrapper.find('[data-test="total-amount"]')
    expect(total.text()).toBe('¥312,000')
    expect(total.classes()).toContain('total-amount')
    expect(total.classes()).not.toContain('total-amount--compact')

    const html = wrapper.html()
    expect(html.indexOf('total-amount-card')).toBeLessThan(html.indexOf('data-test="tree"'))
  })

  // Vuetify 4 には text-h3 等のユーティリティが無く、クラスだけでは 14px のままだった
  it('合計金額の大きさ・太さ・色を scoped CSS で持つ', () => {
    // vitest は frontend 直下で走る（相対パスで SFC の原文を読む）
    const sfc = readFileSync('src/apps/saas-a-billing/views/RunDetailView.vue', 'utf-8')

    expect(sfc).toMatch(/\.total-amount\s*\{[^}]*font-size:\s*2\.5rem/)
    expect(sfc).toMatch(/\.total-amount\s*\{[^}]*line-height:\s*1\.2/)
    expect(sfc).toMatch(/\.total-amount\s*\{[^}]*font-weight:\s*700/)
    expect(sfc).toMatch(/\.total-amount\s*\{[^}]*--v-theme-primary/)
    expect(sfc).toMatch(/\.total-amount--compact\s*\{[^}]*font-size:\s*1\.75rem/)
  })

  it('合計金額の隣に環境バッジを併置する', async () => {
    const wrapper = await mountView()

    const badge = wrapper.find('[data-test="total-amount-card"]').findComponent(EnvBadge)
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toContain('検証環境')
  })

  it.each([
    ['running', '集計中'],
    ['queued', '順番待ち'],
    ['failed', '失敗'],
  ] as const)('金額の無い %s は — と状態の説明を出す', async (status, note) => {
    vi.mocked(billingApi.getRun).mockResolvedValue({
      run: makeRun({ status, summary: undefined }),
      result: null,
    })
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="total-amount"]').text()).toBe('—')
    expect(wrapper.find('[data-test="total-amount-note"]').text()).toContain(note)
  })

  it('内訳表のフッターに太字の合計行が出る', async () => {
    const wrapper = await mountView()

    const cells = wrapper.find('[data-test="total-row"]').findAll('td')
    const amount = cells[cells.length - 2]
    expect(amount.text()).toBe('¥312,000')
    expect(amount.classes()).toContain('font-weight-bold')
    expect(amount.classes()).toContain('text-end')
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
    expect(table.text()).toContain('2025-09-01')
    expect(table.text()).toContain('¥650')
    expect(table.text()).toContain('¥450')
    expect(table.text()).toContain('¥1,800')
    expect(table.text()).toContain('カメラ地点')
  })

  it('課金対象期間が 23:59:59 表記で出る', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="closing-period"]').text()).toContain(
      '2026-09-21 00:00:00 〜 2026-10-20 23:59:59',
    )
  })

  // 日時・金額の表記は format.ts に統一する（UI-13 / UI-16）
  it('最終受信日時が YYYY-MM-DD HH:mm で出る', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        result: makeResult({
          tenants: [
            makeTenant({ devices: [makeDevice({ last_received_at: '2026-10-20T23:55:00+09:00' })] }),
          ],
        }),
      }),
    )
    const wrapper = await mountView()

    const row = wrapper.find('[data-test="device-row"]').text()
    expect(row).toContain('2026-10-20 23:55')
    expect(row).not.toContain('+09:00')
  })

  it('判定・単価・金額の列は常時表示、副次列は lg 未満で隠す', async () => {
    const wrapper = await mountView()

    const header = (label: string) =>
      wrapper.find('[data-test="tree"]').findAll('th').find((th) => th.text() === label)
    for (const label of ['地点名', '判定', '単価', '金額', '備考']) {
      expect(header(label)!.classes()).not.toContain('d-none')
    }
    for (const label of ['フォーマット', '項目数', 'カメラ']) {
      expect(header(label)!.classes()).toContain('d-none')
      expect(header(label)!.classes()).toContain('d-lg-table-cell')
    }
  })

  // 色だけが手掛かりだった行の塗り分けに意味を添える（UI-20）
  it('判定の凡例が地点表に出る', async () => {
    const wrapper = await mountView()

    const legend = wrapper.find('[data-test="judgement-legend"]')
    expect(legend.exists()).toBe(true)
    for (const label of ['課金', '通信なし', '除外', '取得失敗', '消えた地点']) {
      expect(legend.text()).toContain(label)
    }
    expect(wrapper.find('[data-test="judgement-help"]').text()).toContain('OVER_MAX_ITEMS')
  })

  // keep-alive のせいで別の月の数字が残っていた（UI-01）
  it('別の月へ移ると内訳を取り直す', async () => {
    const wrapper = await mountView()
    expect(billingApi.getRun).toHaveBeenCalledTimes(1)

    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({
        run: makeRun({ month: '2026-06' }),
        result: makeResult({ month: '2026-06', summary: makeSummary({ total_amount: 999111 }) }),
      }),
    )
    await router.push('/saas-a-billing/runs/verification/2026-06')
    await flushPromises()

    expect(billingApi.getRun).toHaveBeenLastCalledWith('2026-06', {
      targetEnv: 'verification',
      detail: true,
    })
    expect(wrapper.find('[data-test="total-amount"]').text()).toBe('¥999,111')
    expect(wrapper.text()).not.toContain('¥312,000')
  })

  // 対象月が変わっていない再ナビゲーションで取り直さない（無駄な二重取得を避ける）
  it('同じ月への再ナビゲーションでは取り直さない', async () => {
    await mountView()
    expect(billingApi.getRun).toHaveBeenCalledTimes(1)

    await router.push('/saas-a-billing/runs/verification/2026-10?from=list')
    await flushPromises()

    expect(billingApi.getRun).toHaveBeenCalledTimes(1)
  })

  it('実行履歴へ戻る導線がある', async () => {
    const wrapper = await mountView()

    const back = wrapper.find('[data-test="back-to-runs"]')
    expect(back.exists()).toBe(true)
    expect(back.attributes('href')).toBe('/saas-a-billing')
  })

  it('ダウンロード中はボタンが進行表示になる', async () => {
    holder.runs.downloading = 'verification#2026-10'
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="download"]').classes()).toContain('v-btn--loading')
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
    expect(dialog!.textContent).toContain('上書き')

    const confirm = document.body.querySelector('[data-test="rerun-confirm"]') as HTMLElement
    confirm.click()
    await flushPromises()

    // 集計対象環境はサイト固定なので送らない（設計 R1）。force は明示チェックのみ（UI-07）
    expect(holder.runs.createRun).toHaveBeenCalledWith({
      month: '2026-10',
      distribute: false,
      force: false,
    })
  })

  it('強制再実行はチェックしたときだけ有効で警告が出る', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="rerun-force-warning"]')).toBeNull()

    ;(document.body.querySelector('[data-test="rerun-force"] input') as HTMLElement).click()
    await flushPromises()

    expect(document.body.querySelector('[data-test="rerun-force-warning"]')!.textContent).toContain(
      '二重',
    )

    ;(document.body.querySelector('[data-test="rerun-confirm"]') as HTMLElement).click()
    await flushPromises()

    expect(holder.runs.createRun).toHaveBeenCalledWith({
      month: '2026-10',
      distribute: false,
      force: true,
    })
  })

  it('配布 ON では実際の宛先を出し、確認チェックまで実行できない', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()

    ;(document.body.querySelector('[data-test="rerun-distribute"] input') as HTMLElement).click()
    await flushPromises()

    const dialog = document.body.querySelector('[data-test="rerun-dialog"]')!
    expect(dialog.textContent).toContain('devA@example.co.jp')
    expect(dialog.textContent).toContain('#billing')
    const confirm = document.body.querySelector('[data-test="rerun-confirm"]') as HTMLElement
    expect(confirm.hasAttribute('disabled')).toBe(true)

    ;(document.body.querySelector('[data-test="distribute-ack"] input') as HTMLElement).click()
    await flushPromises()

    expect(confirm.hasAttribute('disabled')).toBe(false)
  })

  it('実行中の月は再実行できない', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({ run: makeRun({ status: 'running' }) }),
    )
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="rerun"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="rerun-blocked-reason"]').text()).toContain('実行中')
  })

  // 409 の生メッセージ「RUN を取得できません: ALREADY_SUCCEEDED」が出ていた
  it('再実行が競合したらダイアログを閉じずに中へ理由を出す', async () => {
    holder.runs.createRun.mockImplementation(async () => {
      holder.runs.error = 'この月は既に集計済みです。上書きするには「強制再実行」にチェックを入れてください。'
      return null
    })
    const wrapper = await mountView()

    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()
    ;(document.body.querySelector('[data-test="rerun-confirm"]') as HTMLElement).click()
    await flushPromises()

    expect(document.body.querySelector('[data-test="rerun-dialog"]')).not.toBeNull()
    expect(document.body.querySelector('[data-test="rerun-error"]')!.textContent).toContain(
      '強制再実行',
    )
    // 上部の帯は詳細取得の失敗専用にする（実行の失敗と混ざって読めなかった）
    expect(wrapper.find('[data-test="page-error"]').exists()).toBe(false)
  })

  it('成功済みの月は開いた時点で強制再実行が要ると伝える', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[data-test="rerun-force-required"]')!.textContent).toContain(
      '強制再実行',
    )
  })

  it('失敗した月には強制再実行の注記を出さない', async () => {
    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({ run: makeRun({ status: 'failed' }), result: null }),
    )
    const wrapper = await mountView()

    await wrapper.find('[data-test="rerun"]').trigger('click')
    await flushPromises()

    expect(document.body.querySelector('[data-test="rerun-force-required"]')).toBeNull()
  })

  // 強制再実行が通っても旧結果（配布済み・旧金額）が残っていた
  it('再実行が通ったら詳細を取り直し、実行中は 10 秒ごとに更新する', async () => {
    vi.useFakeTimers()
    holder.runs.createRun.mockResolvedValue({
      month: '2026-10',
      target_env: 'verification',
      status: 'queued',
      attempt: 2,
      execution_arn: 'arn:aws:states:ap-northeast-1:000000000000:execution:x',
    })
    const wrapper = await mountView()
    expect(billingApi.getRun).toHaveBeenCalledTimes(1)

    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({ run: makeRun({ status: 'running', summary: undefined }), result: null }),
    )
    await wrapper.find('[data-test="rerun"]').trigger('click')
    await vi.advanceTimersByTimeAsync(0)
    ;(document.body.querySelector('[data-test="rerun-confirm"]') as HTMLElement).click()
    await vi.advanceTimersByTimeAsync(0)

    expect(billingApi.getRun).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-test="total-amount"]').text()).toBe('—')
    expect(wrapper.find('[data-test="total-amount-note"]').text()).toContain('集計中')

    await vi.advanceTimersByTimeAsync(10_000)
    expect(billingApi.getRun).toHaveBeenCalledTimes(3)

    vi.mocked(billingApi.getRun).mockResolvedValue(
      makeDetail({ result: makeResult({ summary: makeSummary({ total_amount: 777000 }) }) }),
    )
    await vi.advanceTimersByTimeAsync(10_000)
    expect(billingApi.getRun).toHaveBeenCalledTimes(4)
    expect(wrapper.find('[data-test="total-amount"]').text()).toBe('¥777,000')

    await vi.advanceTimersByTimeAsync(30_000)
    expect(billingApi.getRun).toHaveBeenCalledTimes(4)
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
