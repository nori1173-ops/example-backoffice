import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useAuthStore } from '@/stores/auth'
import EnvBadge from '../components/EnvBadge.vue'
import type { AuditEntry, PriceVersion, SettingsView, SlackMode, TargetEnv } from '../types'
import SettingsViewComponent from '../views/SettingsView.vue'

interface SettingsStoreMock {
  settings: SettingsView | null
  prices: PriceVersion[]
  audits: AuditEntry[]
  slackMode: SlackMode | null
  targets: Record<TargetEnv, boolean>
  saasaHost: string | null
  loading: boolean
  error: string | null
  fetch: Mock
  save: Mock
  addPrice: Mock
  fetchAudits: Mock
}

const holder = vi.hoisted(() => ({ settings: null as unknown as SettingsStoreMock }))

vi.mock('../stores/settings', async () => {
  const { reactive } = await import('vue')
  holder.settings = reactive({
    settings: null,
    prices: [],
    audits: [],
    slackMode: null,
    targets: { verification: false, production: false },
    saasaHost: null,
    loading: false,
    error: null,
    fetch: vi.fn(),
    save: vi.fn().mockResolvedValue(true),
    addPrice: vi.fn().mockResolvedValue(true),
    fetchAudits: vi.fn(),
  }) as SettingsStoreMock
  return { useSettingsStore: () => holder.settings }
})

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
    targets: { verification: true, production: true },
    slack_mode: 'bot',
    ...overrides,
  }
}

function makePriceVersion(effectiveFrom: string): PriceVersion {
  return {
    effective_from: effectiveFrom,
    bands: [
      { max_items: 500, price_3y: 650, price_1y: 450 },
      { max_items: 800, price_3y: 1000, price_1y: 700 },
      { max_items: 1600, price_3y: 1200, price_1y: 900 },
      { max_items: 1700, price_3y: 1350, price_1y: 1000 },
    ],
    camera: { price_3y: 1800, price_1y: 1400 },
    note: 'support-tracker #2001',
    created_at: `${effectiveFrom}T10:00:00+09:00`,
    created_by: 'sub',
    created_by_email: 'devA@example.co.jp',
  }
}

function makeAudit(at: string, target: AuditEntry['target'] = 'settings'): AuditEntry {
  return {
    target,
    actor: 'sub',
    actor_email: 'devA@example.co.jp',
    before: { cutoff_day: 20 },
    after: { cutoff_day: 25 },
    at,
  }
}

let pinia: Pinia

async function mountView(groups: string[] = ['admin']): Promise<VueWrapper> {
  const auth = useAuthStore()
  auth.email = 'devA@example.co.jp'
  auth.groups = groups

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(SettingsViewComponent, { global: { plugins: [pinia, vuetify] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  const store = holder.settings
  store.settings = makeSettings()
  store.prices = [makePriceVersion('2025-09-01')]
  store.audits = []
  store.slackMode = 'bot'
  store.targets = { verification: true, production: true }
  store.saasaHost = 'example.invalid'
  store.loading = false
  store.error = null
  store.fetch.mockReset()
  store.save.mockReset().mockResolvedValue(true)
  store.addPrice.mockReset().mockResolvedValue(true)
  store.fetchAudits.mockReset()
})

describe('SettingsView', () => {
  it('admin 以外は入力が読み取り専用', async () => {
    const wrapper = await mountView([])

    expect(wrapper.find('[data-test="cutoff-day"] input').attributes('readonly')).toBeDefined()
    expect(wrapper.find('[data-test="slack-channel-id"] input').attributes('readonly')).toBeDefined()
    expect(wrapper.find('[data-test="settings-save"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="price-add"]').exists()).toBe(false)
  })

  it('環境バッジと接続先ホストが常時表示される', async () => {
    const wrapper = await mountView([])

    const summary = wrapper.find('[data-test="target-summary"]')
    expect(summary.exists()).toBe(true)
    expect(summary.findComponent(EnvBadge).text()).toContain('検証環境')
    expect(summary.text()).toContain('example.invalid')
  })

  it('Webhook フォールバック中は注意書きが出る', async () => {
    holder.settings.slackMode = 'webhook'
    const wrapper = await mountView()

    const notice = wrapper.find('[data-test="webhook-notice"]')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain('チャンネル ID')
  })

  it('bot モードでは注意書きが出ない', async () => {
    holder.settings.slackMode = 'bot'
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="webhook-notice"]').exists()).toBe(false)
  })

  it('cutoff_day の範囲外でエラー表示', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="cutoff-day"] input').setValue('29')
    await flushPromises()

    expect(wrapper.find('[data-test="cutoff-day"]').text()).toContain('1〜28')
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeDefined()
  })

  it('slack_channel_id が #name 形式ならエラー表示', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="slack-channel-id"] input').setValue('#proj-saas-a-billing')
    await flushPromises()

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).toContain('C または G')
  })

  it('接続情報（SSM）未登録なら保存ボタンの横に理由が出る', async () => {
    holder.settings.targets = { verification: false, production: false }
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="save-blocked-reason"]').text()).toContain(
      '接続情報（SSM）が未登録',
    )
  })

  it('入力エラー中は保存ボタンの横に理由が出る', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="cutoff-day"] input').setValue('29')
    await flushPromises()

    expect(wrapper.find('[data-test="save-blocked-reason"]').text()).toContain('入力エラー')
  })

  it('保存可能なら理由表示は出ない', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="save-blocked-reason"]').exists()).toBe(false)
  })

  it('配布トグルが両方 OFF ならチャンネル ID も宛先も空欄で保存できる', async () => {
    holder.settings.settings = makeSettings({ slack_channel_id: '', mail_to: [] })
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).not.toContain('必要です')
    expect(wrapper.find('[data-test="mail-to"]').text()).not.toContain('必要です')
    expect(wrapper.find('[data-test="save-blocked-reason"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeUndefined()
  })

  it('Slack 配布 ON でチャンネル ID が空欄ならエラー表示', async () => {
    holder.settings.settings = makeSettings({ slack_channel_id: '', slack_enabled: true })
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).toContain(
      'Slack 配布を有効にするにはチャンネル ID が必要です',
    )
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeDefined()
  })

  it('Slack 配布 OFF でもチャンネル ID の形式は検証する', async () => {
    holder.settings.settings = makeSettings({ slack_channel_id: '#proj-saas-a-billing' })
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).toContain('C または G')
  })

  it('メール配布 ON で宛先が空ならエラー表示', async () => {
    holder.settings.settings = makeSettings({ mail_to: [], mail_enabled: true })
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="mail-to"]').text()).toContain(
      'メール配布を有効にするには宛先が必要です',
    )
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeDefined()
  })

  it('保存ペイロードに配布トグルが含まれる', async () => {
    holder.settings.settings = makeSettings({ mail_enabled: true, slack_enabled: true })
    const wrapper = await mountView()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.objectContaining({ mail_enabled: true, slack_enabled: true }),
    )
  })

  it('未登録の環境名が集計対象環境の注意書きに出る', async () => {
    holder.settings.targets = { verification: true, production: false }
    const wrapper = await mountView()

    const hint = wrapper.find('[data-test="target-env-hint"]')
    expect(hint.text()).toContain('本番')
    expect(hint.text()).toContain('接続情報（SSM）が未登録')
  })

  it('全環境が登録済みなら集計対象環境の注意書きは出ない', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="target-env-hint"]').exists()).toBe(false)
  })

  it('単価表の版一覧が effective_from 降順', async () => {
    holder.settings.prices = [
      makePriceVersion('2025-09-01'),
      makePriceVersion('2026-04-01'),
      makePriceVersion('2025-12-01'),
    ]
    const wrapper = await mountView()

    const rows = wrapper.findAll('[data-test="price-version-row"]')
    expect(rows).toHaveLength(3)
    expect(rows.map((row) => row.text())).toEqual([
      expect.stringContaining('2026-04-01'),
      expect.stringContaining('2025-12-01'),
      expect.stringContaining('2025-09-01'),
    ])
  })

  it('現在の単価が円表記の表で表示される', async () => {
    holder.settings.prices = [makePriceVersion('2025-09-01')]
    const wrapper = await mountView()

    const current = wrapper.find('[data-test="price-current"]')
    expect(current.text()).toContain('2025-09-01')
    expect(current.text()).toContain('650 円')
    expect(current.text()).toContain('カメラ')
  })

  it('改定フォームはボタンを押すまで出ない', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="price-effective-from"]').exists()).toBe(false)

    await wrapper.find('[data-test="price-revise-open"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="price-effective-from"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="price-add"]').exists()).toBe(true)
  })

  it('新版追加で帯が昇順でないとエラー表示', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="price-revise-open"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="price-error"]').exists()).toBe(false)

    await wrapper.find('[data-test="band-max-items-0"] input').setValue('900')
    await flushPromises()

    expect(wrapper.find('[data-test="price-error"]').text()).toContain('昇順')
    expect(wrapper.find('[data-test="price-add"]').attributes('disabled')).toBeDefined()
  })

  it('監査履歴が表示される', async () => {
    holder.settings.audits = [
      makeAudit('2026-11-03T10:00:00+09:00'),
      makeAudit('2026-11-02T10:00:00+09:00', 'price'),
      makeAudit('2026-11-01T10:00:00+09:00'),
    ]
    const wrapper = await mountView()

    expect(holder.settings.fetchAudits).toHaveBeenCalledTimes(1)
    const rows = wrapper.findAll('[data-test="audit-row"]')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('2026/11/03')
    expect(rows[1].text()).toContain('単価表')
    expect(rows[2].text()).toContain('2026/11/01')
  })

  it('admin 以外には変更履歴ブロックが出ない', async () => {
    holder.settings.audits = [makeAudit('2026-11-03T10:00:00+09:00')]
    const wrapper = await mountView([])

    expect(wrapper.find('[data-test="audit-block"]').exists()).toBe(false)
    expect(holder.settings.fetchAudits).not.toHaveBeenCalled()
  })
})
