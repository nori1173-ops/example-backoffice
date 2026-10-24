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
import type { PriceVersion, SettingsView, SlackDirectory, SlackMode, TargetEnv } from '../types'
import SettingsViewComponent from '../views/SettingsView.vue'

interface SettingsStoreMock {
  settings: SettingsView | null
  prices: PriceVersion[]
  slackMode: SlackMode | null
  targets: Partial<Record<TargetEnv, boolean>>
  saasaHost: string | null
  loading: boolean
  error: string | null
  directory: SlackDirectory | null
  directoryLoading: boolean
  directoryError: string | null
  fetch: Mock
  save: Mock
  addPrice: Mock
  loadSlackDirectory: Mock
}

const holder = vi.hoisted(() => ({ settings: null as unknown as SettingsStoreMock }))

vi.mock('../stores/settings', async () => {
  const { reactive } = await import('vue')
  holder.settings = reactive({
    settings: null,
    prices: [],
    slackMode: null,
    targets: { verification: false, production: false },
    saasaHost: null,
    loading: false,
    error: null,
    directory: null,
    directoryLoading: false,
    directoryError: null,
    fetch: vi.fn(),
    save: vi.fn().mockResolvedValue(true),
    addPrice: vi.fn().mockResolvedValue(true),
    loadSlackDirectory: vi.fn().mockResolvedValue(undefined),
  }) as SettingsStoreMock
  return { useSettingsStore: () => holder.settings }
})

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
    targets: { verification: true, production: true },
    slack_mode: 'bot',
    ...overrides,
  }
}

function makeDirectory(): SlackDirectory {
  return {
    channels: [
      { id: 'C0123ABCD', name: 'proj-saas-a-billing', is_private: false, is_member: false },
      { id: 'G0999ZZZZ', name: 'secret', is_private: true, is_member: true },
    ],
    users: [{ id: 'U0000DEVA1', handle: 'devA', real_name: 'DevA', display_name: '開発者本人' }],
    fetched_at: '2026-09-04T02:30:00Z',
  }
}

function suggestOptions(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll('[data-test="suggest-option"]'))
}

/** トリガー文字から入力して候補を開く（Slack の入力欄と同じ操作）。 */
async function typeSuggest(wrapper: VueWrapper, testId: string, text: string): Promise<void> {
  await wrapper.find(`[data-test="${testId}"] input`).setValue(text)
  await flushPromises()
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

/** 開いているツールチップの文言。 */
function activeTooltips(): string {
  return Array.from(document.body.querySelectorAll('.v-overlay--active'))
    .map((overlay) => overlay.textContent ?? '')
    .join(' ')
}

beforeEach(() => {
  document.body.innerHTML = ''
  pinia = createPinia()
  setActivePinia(pinia)
  const store = holder.settings
  store.settings = makeSettings()
  store.prices = [makePriceVersion('2025-09-01')]
  store.slackMode = 'bot'
  store.targets = { verification: true, production: true }
  store.saasaHost = 'example.invalid'
  store.loading = false
  store.error = null
  store.directory = makeDirectory()
  store.directoryLoading = false
  store.directoryError = null
  store.loadSlackDirectory.mockReset().mockResolvedValue(undefined)
  store.fetch.mockReset()
  store.save.mockReset().mockResolvedValue(true)
  store.addPrice.mockReset().mockResolvedValue(true)
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

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).toContain('古い形式')
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

  // #1011: チャンネルとメンションは候補から選ぶ。画面に ID は出さない
  it('表示名の手入力フィールドは無い', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="slack-channel-label"]').exists()).toBe(false)
  })

  it('候補が未取得なら画面表示で読み込む', async () => {
    holder.settings.directory = null
    await mountView()

    expect(holder.settings.loadSlackDirectory).toHaveBeenCalled()
  })

  it('候補を取得済みなら画面に戻っても取り直さない', async () => {
    await mountView()

    expect(holder.settings.loadSlackDirectory).not.toHaveBeenCalled()
  })

  it('保存済みのチャンネルは名前のチップで出し、ID は画面に出さない', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).toContain('#proj-saas-a-billing')
    expect(wrapper.html()).not.toContain('C0123ABCD')
  })

  it('候補に無いチャンネルは保存済みの表示名で出す', async () => {
    holder.settings.directory = { ...makeDirectory(), channels: [] }
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="slack-channel-id"]').text()).toContain('#proj-saas-a-billing')
  })

  it('# で選ぶと ID と表示名の両方が更新される', async () => {
    const wrapper = await mountView()

    await typeSuggest(wrapper, 'slack-channel-id', '#sec')
    suggestOptions()[0].click()
    await flushPromises()
    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.objectContaining({ slack_channel_id: 'G0999ZZZZ', slack_channel_label: '#secret' }),
    )
  })

  it('@ で選ぶと保存値は Slack のメンション記法になる', async () => {
    const wrapper = await mountView()

    await typeSuggest(wrapper, 'slack-mention', '@開発')
    suggestOptions()[0].click()
    await flushPromises()
    await wrapper.find('[data-test="settings-save"]').trigger('click')
    await flushPromises()

    expect(holder.settings.save).toHaveBeenCalledWith(
      expect.objectContaining({ slack_mention: '<@U0000DEVA1>' }),
    )
  })

  it('保存済みのメンションは名前のチップで出し、ID は画面に出さない', async () => {
    holder.settings.settings = makeSettings({ slack_mention: '<@U0000DEVA1> <!here>' })
    const wrapper = await mountView()

    const field = wrapper.find('[data-test="slack-mention"]')
    expect(field.text()).toContain('@開発者本人')
    expect(field.text()).toContain('@here')
    expect(wrapper.html()).not.toContain('U0000DEVA1')
  })

  it('候補の取得に失敗しても警告と再取得ボタンを出すだけで保存はできる', async () => {
    holder.settings.directory = null
    holder.settings.directoryError = 'Slack の Bot token が登録されていません'
    const wrapper = await mountView()

    const alert = wrapper.find('[data-test="slack-directory-error"]')
    expect(alert.text()).toContain('Slack の候補を取得できませんでした')
    expect(alert.text()).toContain('Bot token')
    expect(wrapper.find('[data-test="settings-save"]').attributes('disabled')).toBeUndefined()

    holder.settings.loadSlackDirectory.mockClear()
    await wrapper.find('[data-test="slack-directory-retry"]').trigger('click')

    expect(holder.settings.loadSlackDirectory).toHaveBeenCalledWith(true)
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

  // 初期状態（ストアが空）でも取りに行く。未取得を未保存と誤判定して fetch を飛ばしていた
  it('ストアが空の初期状態でも設定を取りに行く', async () => {
    holder.settings.settings = null
    holder.settings.prices = []
    const wrapper = await mountView()

    expect(holder.settings.fetch).toHaveBeenCalledTimes(1)
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

  // 画面の語の意味が分からないという指摘への対応（#1012）
  it('ヘルプモード ON で締め日時と除外キーワードの説明が出る', async () => {
    useUiStore().toggleHelpMode()
    const wrapper = await mountView()

    await wrapper.find('[data-test="cutoff-day"]').trigger('mouseenter')
    await flushPromises()
    expect(activeTooltips()).toContain('毎月この日時で締めます')

    await wrapper.find('[data-test="exclude-keywords"]').trigger('mouseenter')
    await flushPromises()
    expect(activeTooltips()).toContain('0 円（除外）にします')
  })

  // 押せない理由を一番知りたいときに説明が出ないと困る。disabled な button はマウスイベントを受けない
  it('ヘルプモード ON なら無効な保存ボタンでも説明が出る', async () => {
    holder.settings.targets = {}
    useUiStore().toggleHelpMode()
    const wrapper = await mountView()

    const save = wrapper.find('[data-test="settings-save"]')
    expect(save.attributes('disabled')).toBeDefined()

    save.element.parentElement?.dispatchEvent(new MouseEvent('mouseenter'))
    await flushPromises()

    expect(activeTooltips()).toContain('入力した設定を保存します')
  })
})


describe('メール添付のシート範囲', () => {
  it('選択肢・未設定時の既定値・ヘルプを表示する', async () => {
    Reflect.deleteProperty(holder.settings.settings!, 'mail_attachment_sheets')
    const wrapper = await mountView()
    const field = wrapper.findComponent<typeof components.VSelect>('[data-test="mail-attachment-sheets"]')
    expect(field.props('label')).toBe('メールに添付する Excel')
    expect(field.props('modelValue')).toBe('billing_only')
    expect(field.props('items')).toEqual([
      { title: 'シート 1（請求照合）のみ', value: 'billing_only' },
      { title: '両方のシート（請求照合 + 全地点）', value: 'all' },
    ])
    const { HELP } = await import('../help')
    expect(HELP.settings.mailAttachmentSheets).toBe('メールに添付する Excel に含めるシートを選びます。「シート 1 のみ」にすると、全地点の一覧（管理用のシート）は添付に含まれません。画面からダウンロードする Excel は、この設定に関係なく常に両方のシートが入ります。Slack には Excel を添付しません')
    await wrapper.find('[data-test="settings-save"]').trigger('click')
    expect(holder.settings.save).toHaveBeenCalledWith(expect.objectContaining({ mail_attachment_sheets: 'billing_only' }))
  })

  it('変更ありを検出し、選択値を保存・再表示・破棄できる', async () => {
    const wrapper = await mountView()
    const field = wrapper.findComponent<typeof components.VSelect>('[data-test="mail-attachment-sheets"]')
    expect(wrapper.find('[data-test="discard-changes"]').exists()).toBe(false)
    field.vm.$emit('update:modelValue', 'all')
    await flushPromises()
    expect(wrapper.find('[data-test="discard-changes"]').exists()).toBe(true)
    await wrapper.find('[data-test="settings-save"]').trigger('click')
    expect(holder.settings.save).toHaveBeenCalledWith(expect.objectContaining({ mail_attachment_sheets: 'all' }))
    holder.settings.settings = makeSettings({ mail_attachment_sheets: 'all' })
    await flushPromises()
    expect(field.props('modelValue')).toBe('all')
    field.vm.$emit('update:modelValue', 'billing_only')
    await flushPromises()
    await wrapper.find('[data-test="discard-changes"]').trigger('click')
    expect(field.props('modelValue')).toBe('all')
  })

  it('管理者以外は変更できない', async () => {
    const wrapper = await mountView([])
    expect(wrapper.findComponent<typeof components.VSelect>('[data-test="mail-attachment-sheets"]').props('disabled')).toBe(true)
  })
})
