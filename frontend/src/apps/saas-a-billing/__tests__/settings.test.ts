import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { billingApi } from '../api'
import type { PriceTable, PriceVersion, SettingsView, SlackDirectory } from '../types'
import { useSettingsStore } from '../stores/settings'

vi.mock('../api', () => ({
  billingApi: {
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
    listPrices: vi.fn(),
    createPrice: vi.fn(),
    getSlackDirectory: vi.fn(),
  },
}))

function makeDirectory(): SlackDirectory {
  return {
    channels: [{ id: 'C0123ABCD', name: 'proj-saas-a-billing', is_private: false, is_member: false }],
    users: [{ id: 'U0000DEVA1', handle: 'devA', real_name: 'DevA', display_name: '開発者本人' }],
    fetched_at: '2026-09-04T02:30:00Z',
  }
}

function makeSettings(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    target_env: 'verification',
    cutoff_day: 20,
    cutoff_time: '24:00',
    auto_run_enabled: false,
    auto_run_from_month: '2026-10',
    mail_enabled: false,
    slack_enabled: false,
    mail_to: ['devA@example.co.jp'],
    mail_cc: [],
    mail_attachment_sheets: 'billing_only',
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
    targets: { verification: true, production: false },
    slack_mode: 'bot',
    ...overrides,
  }
}

function makePriceTable(overrides: Partial<PriceTable> = {}): PriceTable {
  return {
    effective_from: '2026-04-01',
    bands: [
      { max_items: 500, price_3y: 650, price_1y: 450 },
      { max_items: 800, price_3y: 1000, price_1y: 700 },
    ],
    camera: { price_3y: 1800, price_1y: 1400 },
    note: '',
    ...overrides,
  }
}

function makePriceVersion(overrides: Partial<PriceVersion> = {}): PriceVersion {
  return {
    ...makePriceTable(),
    created_at: '2026-03-01T10:00:00+09:00',
    created_by: 'sub',
    created_by_email: 'devA@example.co.jp',
    ...overrides,
  }
}

function rejectWith(status: number, code?: string): unknown {
  return {
    response: {
      status,
      data: code ? { error: { code, message: 'サーバからのメッセージ' } } : {},
    },
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(billingApi.getSettings).mockResolvedValue(makeSettings())
  vi.mocked(billingApi.updateSettings).mockResolvedValue(makeSettings({ version: 4 }))
  vi.mocked(billingApi.listPrices).mockResolvedValue({
    versions: [makePriceVersion()],
    current: makePriceVersion(),
  })
  vi.mocked(billingApi.createPrice).mockResolvedValue(makePriceVersion())
  vi.mocked(billingApi.getSlackDirectory).mockResolvedValue(makeDirectory())
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('settings ストア', () => {
  it('fetch が settings/prices/slackMode/targets/saasaHost を取る', async () => {
    const store = useSettingsStore()

    await store.fetch()

    expect(billingApi.getSettings).toHaveBeenCalledTimes(1)
    expect(billingApi.listPrices).toHaveBeenCalledTimes(1)
    expect(store.settings?.cutoff_day).toBe(20)
    expect(store.prices).toHaveLength(1)
    expect(store.slackMode).toBe('bot')
    expect(store.targets).toEqual({ verification: true, production: false })
    expect(store.saasaHost).toBe('example.invalid')
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('save が version を載せる', async () => {
    const store = useSettingsStore()
    await store.fetch()

    const saved = await store.save({ cutoff_day: 25 })

    expect(saved).toBe(true)
    expect(billingApi.updateSettings).toHaveBeenCalledWith({ cutoff_day: 25, version: 3 })
    expect(store.error).toBeNull()
  })

  it('409 でメッセージを出す', async () => {
    vi.mocked(billingApi.updateSettings).mockRejectedValue(rejectWith(409, 'VERSION_CONFLICT'))
    const store = useSettingsStore()
    await store.fetch()

    const saved = await store.save({ cutoff_day: 25 })

    expect(saved).toBe(false)
    expect(store.error).toContain('他の人が更新しました')
    expect(store.loading).toBe(false)
  })

  // A1 以降 `targets` は固定環境のキーだけを返す（設計 R1）
  it('固定環境のキーだけの targets をそのまま持つ', async () => {
    vi.mocked(billingApi.getSettings).mockResolvedValue(
      makeSettings({ targets: { verification: true } }),
    )
    const store = useSettingsStore()

    await store.fetch()

    expect(store.targets).toEqual({ verification: true })
  })

  it('TARGET_ENV_FIXED でメッセージを出す', async () => {
    vi.mocked(billingApi.updateSettings).mockRejectedValue(rejectWith(400, 'TARGET_ENV_FIXED'))
    const store = useSettingsStore()
    await store.fetch()

    const saved = await store.save({ cutoff_day: 25 })

    expect(saved).toBe(false)
    expect(store.error).toContain('集計対象環境')
    expect(store.loading).toBe(false)
  })

  it('loadSlackDirectory が候補を取る', async () => {
    const store = useSettingsStore()

    await store.loadSlackDirectory()

    expect(billingApi.getSlackDirectory).toHaveBeenCalledWith(false)
    expect(store.directory?.channels).toHaveLength(1)
    expect(store.directory?.users[0].display_name).toBe('開発者本人')
    expect(store.directoryError).toBeNull()
    expect(store.directoryLoading).toBe(false)
  })

  it('loadSlackDirectory(true) が再取得を頼む', async () => {
    const store = useSettingsStore()

    await store.loadSlackDirectory(true)

    expect(billingApi.getSlackDirectory).toHaveBeenCalledWith(true)
  })

  // 候補が無くても設定は保存できる。取得失敗は例外にせず理由だけ持つ（#1011）
  it('候補の取得に失敗しても例外にせず理由を持つ', async () => {
    vi.mocked(billingApi.getSlackDirectory).mockRejectedValue(rejectWith(503, 'SLACK_NOT_CONFIGURED'))
    const store = useSettingsStore()

    await store.loadSlackDirectory()

    expect(store.directory).toBeNull()
    expect(store.directoryError).toBe('サーバからのメッセージ')
    expect(store.directoryLoading).toBe(false)
  })

  it('addPrice が effective_from と bands を送る', async () => {
    const store = useSettingsStore()
    const price = makePriceTable({ effective_from: '2026-05-01' })

    const added = await store.addPrice(price)

    expect(added).toBe(true)
    expect(billingApi.createPrice).toHaveBeenCalledWith(price)
    expect(vi.mocked(billingApi.createPrice).mock.calls[0][0].bands).toHaveLength(2)
    expect(billingApi.listPrices).toHaveBeenCalledTimes(1)
  })
})


describe('メール添付のシート範囲', () => {
  it('旧サーバーの応答はシート 1 のみで補完する', async () => {
    const legacy = makeSettings()
    Reflect.deleteProperty(legacy, 'mail_attachment_sheets')
    vi.mocked(billingApi.getSettings).mockResolvedValue(legacy)
    const store = useSettingsStore()
    await store.fetch()
    expect(store.settings?.mail_attachment_sheets).toBe('billing_only')
  })

  it.each([null, '', 'billing-only', 'BILLING_ONLY', 123])(
    '不正な値 %s はシート 1 のみで補完する',
    async (value) => {
      const invalid = makeSettings()
      Reflect.set(invalid, 'mail_attachment_sheets', value)
      vi.mocked(billingApi.getSettings).mockResolvedValue(invalid)
      const store = useSettingsStore()

      await store.fetch()

      expect(store.settings?.mail_attachment_sheets).toBe('billing_only')
    },
  )

  it.each(['billing_only', 'all'] as const)('%s を保存して再取得する', async (value) => {
    const store = useSettingsStore()
    await store.fetch()
    vi.mocked(billingApi.getSettings).mockResolvedValue(makeSettings({ mail_attachment_sheets: value }))
    expect(await store.save({ mail_attachment_sheets: value })).toBe(true)
    expect(billingApi.updateSettings).toHaveBeenCalledWith({ mail_attachment_sheets: value, version: 3 })
    expect(store.settings?.mail_attachment_sheets).toBe(value)
  })
})
