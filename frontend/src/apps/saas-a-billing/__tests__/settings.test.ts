import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { billingApi } from '../api'
import type { AuditEntry, PriceTable, PriceVersion, SettingsView } from '../types'
import { useSettingsStore } from '../stores/settings'

vi.mock('../api', () => ({
  billingApi: {
    getSettings: vi.fn(),
    updateSettings: vi.fn(),
    listPrices: vi.fn(),
    createPrice: vi.fn(),
    listAudits: vi.fn(),
  },
}))

function makeSettings(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    target_env: 'verification',
    cutoff_day: 20,
    cutoff_time: '24:00',
    auto_retry_days: 5,
    auto_run_enabled: false,
    auto_run_from_month: '2026-10',
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

function makeAudit(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    target: 'settings',
    actor: 'sub',
    actor_email: 'devA@example.co.jp',
    before: { cutoff_day: 20 },
    after: { cutoff_day: 25 },
    at: '2026-11-01T10:00:00+09:00',
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
  vi.mocked(billingApi.listAudits).mockResolvedValue([])
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

  it('addPrice が effective_from と bands を送る', async () => {
    const store = useSettingsStore()
    const price = makePriceTable({ effective_from: '2026-05-01' })

    const added = await store.addPrice(price)

    expect(added).toBe(true)
    expect(billingApi.createPrice).toHaveBeenCalledWith(price)
    expect(vi.mocked(billingApi.createPrice).mock.calls[0][0].bands).toHaveLength(2)
    expect(billingApi.listPrices).toHaveBeenCalledTimes(1)
  })

  it('fetchAudits が GET /audits を呼び audits に入る', async () => {
    const entries = [makeAudit(), makeAudit({ at: '2026-10-01T10:00:00+09:00' })]
    vi.mocked(billingApi.listAudits).mockResolvedValue(entries)
    const store = useSettingsStore()

    await store.fetchAudits()

    expect(billingApi.listAudits).toHaveBeenCalledTimes(1)
    expect(store.audits).toEqual(entries)
    expect(store.error).toBeNull()
  })

  it('fetchAudits が 403 でも例外にせず audits が空のまま', async () => {
    vi.mocked(billingApi.listAudits).mockRejectedValue(rejectWith(403))
    const store = useSettingsStore()

    await expect(store.fetchAudits()).resolves.toBeUndefined()

    expect(store.audits).toEqual([])
    expect(store.error).toBeNull()
  })
})
