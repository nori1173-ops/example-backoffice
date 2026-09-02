import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import EnvBadge from '../components/EnvBadge.vue'
import type { AuditEntry, PriceVersion, SettingsView, SlackMode, TargetEnv } from '../types'
import SettingsViewComponent from '../views/SettingsView.vue'

interface SettingsStoreMock {
  settings: SettingsView | null
  prices: PriceVersion[]
  audits: AuditEntry[]
  slackMode: SlackMode | null
  targets: Partial<Record<TargetEnv, boolean>>
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
let router: Router

const StubView = { template: '<div />' }

/** onActivated / onBeforeRouteLeave は実際の配置（router-view + KeepAlive）でだけ働く */
const Host = {
  template:
    '<router-view v-slot="{ Component }"><KeepAlive><component :is="Component" /></KeepAlive></router-view>',
}

async function mountView(groups: string[] = ['admin']): Promise<VueWrapper> {
  const auth = useAuthStore()
  auth.email = 'devA@example.co.jp'
  auth.groups = groups

  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/settings', name: 'settings', component: SettingsViewComponent },
      { path: '/other', name: 'other', component: StubView },
    ],
  })
  await router.push('/settings')
  await router.isReady()

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(Host, { global: { plugins: [pinia, vuetify, router] } })
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

function dialogEl(testId: string): HTMLElement | null {
  return document.body.querySelector(`[data-test="${testId}"]`)
}

beforeEach(() => {
  document.body.innerHTML = ''
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
  // 「打てるのに打てない」を無くすため非 admin は disabled に統一する（UI-19）
  it('admin 以外は入力が無効化される', async () => {
    const wrapper = await mountView([])

    expect(wrapper.find('[data-test="cutoff-day"] input').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="slack-channel-id"] input').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="mail-to"] input').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-test="slack-template"] textarea').attributes('disabled')).toBeDefined()
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

  // 集計対象環境はサイト固定（設計 R1 / UI-08）。画面からは変更できない
  it('集計対象環境が表示のみで切り替えボタンが無い', async () => {
    holder.settings.settings = makeSettings({ target_env: 'production' })
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="target-env-verification"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="target-env-production"]').exists()).toBe(false)

    const fixed = wrapper.find('[data-test="target-env-fixed"]')
    expect(fixed.exists()).toBe(true)
    expect(fixed.text()).toContain('このサイトでは固定')
  })

  it('保存ペイロードに target_env を含めない', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.not.objectContaining({ target_env: expect.anything() }),
    )
  })

  it('固定環境の接続情報が未登録なら注意書きが出て保存できない', async () => {
    holder.settings.targets = { verification: false, production: true }
    const wrapper = await mountView()

    const hint = wrapper.find('[data-test="target-env-hint"]')
    expect(hint.text()).toContain('検証環境')
    expect(hint.text()).toContain('接続情報（SSM）が未登録')
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeDefined()
  })

  it('固定環境が登録済みなら注意書きは出ない', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="target-env-hint"]').exists()).toBe(false)
  })

  // A1 以降 `targets` は固定環境のキーだけを返す（設計 R1）
  it('targets が固定環境のキーだけでも保存できる', async () => {
    holder.settings.targets = { verification: true }
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="target-env-hint"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeUndefined()
  })

  it('固定環境のキーが無ければ保存できない', async () => {
    holder.settings.targets = {}
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="target-env-hint"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeDefined()
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
    expect(current.text()).toContain('¥650')
    expect(current.text()).toContain('カメラ')
  })

  it('改定履歴の単価も ¥ 表記で桁区切りされる', async () => {
    holder.settings.prices = [makePriceVersion('2025-09-01')]
    const wrapper = await mountView()

    const row = wrapper.find('[data-test="price-version-row"]').text()
    expect(row).toContain('¥1,000')
    expect(row).toContain('¥1,800')
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
    expect(rows[0].text()).toContain('2026-11-03')
    expect(rows[1].text()).toContain('単価表')
    expect(rows[2].text()).toContain('2026-11-01')
  })

  // 初期状態（ストアが空）でも取りに行く。未取得を未保存と誤判定して fetch を飛ばしていた
  it('ストアが空の初期状態でも設定と変更履歴を取りに行く', async () => {
    holder.settings.settings = null
    holder.settings.prices = []
    holder.settings.audits = []
    const wrapper = await mountView()

    expect(holder.settings.fetch).toHaveBeenCalledTimes(1)
    expect(holder.settings.fetchAudits).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-test="dirty-chip"]').exists()).toBe(false)
  })

  it('未取得のまま離脱しようとしても止めない', async () => {
    holder.settings.settings = null
    await mountView()

    await router.push('/other')
    await flushPromises()

    expect(dialogEl('leave-dialog')).toBeNull()
    expect(router.currentRoute.value.path).toBe('/other')
  })

  it('破棄して移動すると編集が捨てられ、戻ったときに取り直す', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="cutoff-day"] input').setValue('25')
    await flushPromises()

    const navigation = router.push('/other')
    await flushPromises()
    ;(dialogEl('leave-discard') as HTMLElement).click()
    await navigation
    await flushPromises()

    await router.push('/settings')
    await flushPromises()

    expect((wrapper.find('[data-test="cutoff-day"] input').element as HTMLInputElement).value).toBe(
      '20',
    )
    expect(wrapper.find('[data-test="dirty-chip"]').exists()).toBe(false)
    expect(holder.settings.fetch).toHaveBeenCalledTimes(2)
  })

  // 保存の結果がページ最上部のアラートだと視界の外だった（UI-10）
  it('保存の成否をスナックバーで知らせる', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    const ui = useUiStore()
    expect(ui.snackbar.visible).toBe(true)
    expect(ui.snackbar.text).toContain('保存しました')
    expect(ui.snackbar.color).toBe('success')
  })

  it('保存に失敗したらエラー色のスナックバーを出す', async () => {
    holder.settings.save.mockResolvedValue(false)
    holder.settings.error = '他の人が更新しました。'
    const wrapper = await mountView()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    const ui = useUiStore()
    expect(ui.snackbar.color).toBe('error')
    expect(ui.snackbar.text).toContain('他の人が更新しました')
  })

  it('単価の改定登録もスナックバーで知らせる', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="price-revise-open"]').trigger('click')
    await flushPromises()

    await wrapper.find('[data-test="price-add"]').trigger('click')
    await flushPromises()

    expect(useUiStore().snackbar.text).toContain('単価')
  })

  // 未保存のまま離れると黙って消えていた（UI-09）
  it('編集すると未保存の印が出て、破棄で消える', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('[data-test="dirty-chip"]').exists()).toBe(false)

    await wrapper.find('[data-test="cutoff-day"] input').setValue('25')
    await flushPromises()
    expect(wrapper.find('[data-test="dirty-chip"]').exists()).toBe(true)

    await wrapper.find('[data-test="discard-changes"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-test="dirty-chip"]').exists()).toBe(false)
    expect((wrapper.find('[data-test="cutoff-day"] input').element as HTMLInputElement).value).toBe(
      '20',
    )
  })

  it('未保存のまま離脱しようとすると確認して止める', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="cutoff-day"] input').setValue('25')
    await flushPromises()

    void router.push('/other')
    await flushPromises()

    expect(dialogEl('leave-dialog')).not.toBeNull()
    expect(router.currentRoute.value.path).toBe('/settings')

    ;(dialogEl('leave-stay') as HTMLElement).click()
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/settings')
  })

  it('確認ダイアログで破棄を選べば離脱できる', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-test="cutoff-day"] input').setValue('25')
    await flushPromises()

    const navigation = router.push('/other')
    await flushPromises()
    ;(dialogEl('leave-discard') as HTMLElement).click()
    await navigation
    await flushPromises()

    expect(router.currentRoute.value.path).toBe('/other')
  })

  it('未保存の変更が無ければ止めない', async () => {
    await mountView()

    await router.push('/other')
    await flushPromises()

    expect(dialogEl('leave-dialog')).toBeNull()
    expect(router.currentRoute.value.path).toBe('/other')
  })

  // 入力途中のまま保存すると黙って捨てられていた（UI-11）
  it('メール宛先の未確定入力は離脱時に確定する', async () => {
    const wrapper = await mountView()

    const input = wrapper.find('[data-test="mail-to"] input')
    await input.setValue('added@example.co.jp')
    await input.trigger('focusout')
    await flushPromises()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.objectContaining({ mail_to: ['devA@example.co.jp', 'added@example.co.jp'] }),
    )
  })

  it('打ちかけを消してから離れたときは何も足さない', async () => {
    const wrapper = await mountView()

    const input = wrapper.find('[data-test="mail-to"] input')
    await input.setValue('typo@example.co.jp')
    await input.setValue('')
    await input.trigger('focusout')
    await flushPromises()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.objectContaining({ mail_to: ['devA@example.co.jp'] }),
    )
  })

  it('除外キーワードの未確定入力も離脱時に確定する', async () => {
    const wrapper = await mountView()

    const input = wrapper.find('[data-test="exclude-keywords"] input')
    await input.setValue('テスト')
    await input.trigger('focusout')
    await flushPromises()

    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.objectContaining({ exclude_keywords: ['デモ', 'テスト'] }),
    )
  })

  // 監査表の生キーは非開発者に読めない（UI-17）
  it('変更履歴の項目名が日本語になる', async () => {
    holder.settings.audits = [makeAudit('2026-11-03T10:00:00+09:00')]
    const wrapper = await mountView()

    const row = wrapper.find('[data-test="audit-row"]').text()
    expect(row).toContain('締め日')
    expect(row).not.toContain('cutoff_day')
  })

  it('admin 以外には変更履歴ブロックが出ない', async () => {
    holder.settings.audits = [makeAudit('2026-11-03T10:00:00+09:00')]
    const wrapper = await mountView([])

    expect(wrapper.find('[data-test="audit-block"]').exists()).toBe(false)
    expect(holder.settings.fetchAudits).not.toHaveBeenCalled()
  })
})
