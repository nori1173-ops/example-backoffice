import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useAuthStore } from '@/stores/auth'
import { useUiStore } from '@/stores/ui'
import ShellLayout from './ShellLayout.vue'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

// 実レジストリはアプリ実装が入るまで空なので、表示制御の検証には見本マニフェストを使う
const { mockApps } = vi.hoisted(() => ({
  mockApps: [
    {
      id: 'sample-billing',
      title: 'サンプル請求',
      icon: 'mdi-file-chart-outline',
      requiredGroups: ['billing'],
      helpMode: true,
      routes: [],
      nav: [
        { title: 'レポート一覧', to: '/sample-billing' },
        { title: '設定', to: '/sample-billing/settings' },
      ],
    },
    {
      id: 'device-master',
      title: '機器マスタ',
      icon: 'mdi-database-outline',
      routes: [],
      nav: [],
    },
    {
      id: 'secret-tool',
      title: '機密ツール',
      icon: 'mdi-lock-outline',
      requiredGroups: ['secret'],
      routes: [],
      nav: [],
    },
  ],
}))

vi.mock('@/apps/registry', () => ({
  apps: mockApps,
  findApp: (id: string) => mockApps.find((app) => app.id === id),
}))

const StubView = { template: '<div />' }

function createTestRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StubView },
      { path: '/login', name: 'login', component: StubView },
      {
        path: '/sample-billing',
        name: 'sample-billing',
        component: StubView,
        meta: { appId: 'sample-billing' },
      },
      {
        path: '/sample-billing/settings',
        name: 'sample-billing-settings',
        component: StubView,
        meta: { appId: 'sample-billing' },
      },
      {
        path: '/device-master',
        name: 'device-master',
        component: StubView,
        meta: { appId: 'device-master' },
      },
    ],
  })
}

let pinia: Pinia

const DEFAULT_WIDTH = window.innerWidth

function setWidth(width: number): void {
  Object.defineProperty(window, 'innerWidth', { value: width, writable: true, configurable: true })
}

async function mountShell(path = '/'): Promise<VueWrapper> {
  const router = createTestRouter()
  await router.push(path)
  await router.isReady()

  const auth = useAuthStore()
  auth.email = 'user@example.co.jp'
  auth.groups = ['billing']

  const vuetify = createVuetify({ components, directives })
  return mount(ShellLayout, { global: { plugins: [pinia, vuetify, router] } })
}

function navTitles(wrapper: VueWrapper): string[] {
  return wrapper.findAll('.v-list-item-title').map((item) => item.text())
}

describe('ShellLayout', () => {
  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    setWidth(DEFAULT_WIDTH)
    document.body.innerHTML = ''
  })

  // 業務の主操作の結果を見落とさないようにする（UI-10）
  it('エラーのスナックバーは自動で消えず閉じるボタンが出る', async () => {
    const wrapper = await mountShell()
    const ui = useUiStore()

    ui.notify('保存に失敗しました', 'error')
    await nextTick()

    expect(wrapper.findComponent({ name: 'VSnackbar' }).props('timeout')).toBe(-1)
    const close = document.body.querySelector('[data-test="snackbar-close"]') as HTMLElement
    expect(close).not.toBeNull()

    close.click()
    await nextTick()
    expect(ui.snackbar.visible).toBe(false)
  })

  it('成功のスナックバーは自動で消える', async () => {
    const wrapper = await mountShell()

    useUiStore().notify('保存しました', 'success')
    await nextTick()

    expect(wrapper.findComponent({ name: 'VSnackbar' }).props('timeout')).toBe(5000)
  })

  // 幅の狭い端末で開くたびにドロワーが画面を覆っていた（UI-02）
  it('狭い画面ではドロワーが閉じた一時表示で始まる', async () => {
    setWidth(375)
    const wrapper = await mountShell()

    expect(useUiStore().drawer).toBe(false)
    expect(wrapper.find('.v-navigation-drawer').classes()).toContain(
      'v-navigation-drawer--temporary',
    )
  })

  it('広い画面ではドロワーが開いた常設表示で始まる', async () => {
    setWidth(1280)
    const wrapper = await mountShell()

    expect(useUiStore().drawer).toBe(true)
    expect(wrapper.find('.v-navigation-drawer').classes()).not.toContain(
      'v-navigation-drawer--temporary',
    )
  })

  it('狭い画面ではナビを選ぶとドロワーが閉じる', async () => {
    setWidth(375)
    const wrapper = await mountShell()
    const ui = useUiStore()
    ui.drawer = true

    await wrapper.findAll('.v-list-item')[0].trigger('click')

    expect(ui.drawer).toBe(false)
  })

  it('requiredGroups を満たすアプリだけサイドバーに出る', async () => {
    const wrapper = await mountShell()

    const titles = navTitles(wrapper)
    expect(titles).toContain('サンプル請求')
    expect(titles).toContain('機器マスタ')
    expect(titles).not.toContain('機密ツール')
  })

  it('現在のアプリの nav が app-bar のタブに出る', async () => {
    const wrapper = await mountShell('/sample-billing/settings')

    const tabs = wrapper.findAll('.v-tab').map((tab) => tab.text())
    expect(tabs).toEqual(['レポート一覧', '設定'])
  })

  it('ログアウトが末尾にある', async () => {
    const wrapper = await mountShell()

    expect(navTitles(wrapper).slice(-1)).toEqual(['ログアウト'])
  })

  // dev サイトと本番サイトの取り違えを防ぐ常時表示（UI-12 / R7）
  it('VITE_SITE_ENV 未設定なら検証環境チップが出る', async () => {
    const wrapper = await mountShell()

    const chip = wrapper.find('[data-test="site-env"]')
    expect(chip.exists()).toBe(true)
    expect(chip.text()).toBe('検証環境')
    expect(chip.classes()).toContain('bg-orange')
  })

  // 画面の語の意味が分からないという指摘への対応（#1012）
  it('？トグルでヘルプモードが反転する', async () => {
    const wrapper = await mountShell('/sample-billing')
    const ui = useUiStore()

    const toggle = wrapper.find('[data-test="help-toggle"]')
    expect(toggle.attributes('aria-label')).toBe('操作説明の表示切替')
    expect(toggle.attributes('aria-pressed')).toBe('false')

    await toggle.trigger('click')
    expect(ui.helpMode).toBe(true)
    await nextTick()
    expect(toggle.attributes('aria-pressed')).toBe('true')
    expect(toggle.classes()).toContain('text-primary')

    await toggle.trigger('click')
    expect(ui.helpMode).toBe(false)
  })

  it('？トグルはメールアドレスの前に出る', async () => {
    const wrapper = await mountShell('/sample-billing')

    const html = wrapper.html()
    const toggleAt = html.indexOf('help-toggle')
    expect(toggleAt).toBeGreaterThan(-1)
    expect(toggleAt).toBeLessThan(html.indexOf('user@example.co.jp'))
  })

  // HelpTip を持たないアプリでは ON にしても何も起きない「死んだトグル」になる
  it('helpMode を宣言していないアプリでは？トグルが出ない', async () => {
    expect((await mountShell('/device-master')).find('[data-test="help-toggle"]').exists()).toBe(
      false,
    )
    expect((await mountShell('/')).find('[data-test="help-toggle"]').exists()).toBe(false)
  })

  it('VITE_SITE_ENV=prod なら本番チップが出る', async () => {
    vi.stubEnv('VITE_SITE_ENV', 'prod')
    const wrapper = await mountShell()

    const chip = wrapper.find('[data-test="site-env"]')
    expect(chip.text()).toBe('本番')
    expect(chip.classes()).toContain('bg-green')
  })
})
