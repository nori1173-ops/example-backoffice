import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useAuthStore } from '@/stores/auth'
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
      { path: '/mfa', name: 'mfa', component: StubView },
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
    ],
  })
}

let pinia: Pinia

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

  it('セキュリティとログアウトが末尾にある', async () => {
    const wrapper = await mountShell()

    expect(navTitles(wrapper).slice(-2)).toEqual(['セキュリティ', 'ログアウト'])
  })
})
