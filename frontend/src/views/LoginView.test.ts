import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Pending } from '@/stores/auth'
import TotpEnroll from '@/components/TotpEnroll.vue'
import LoginView from './LoginView.vue'

interface AuthMock {
  email: string | null
  groups: string[]
  isLoading: boolean
  error: string | null
  pending: Pending
  isAuthenticated: boolean
  login: Mock
  submitChallenge: Mock
  confirmRegistration: Mock
}

const holder = vi.hoisted(() => ({ auth: null as unknown as AuthMock }))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({
    email: null,
    groups: [],
    isLoading: false,
    error: null,
    pending: null,
    isAuthenticated: false,
    login: vi.fn(),
    submitChallenge: vi.fn(),
    confirmRegistration: vi.fn(),
  }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

const SETUP_URI = 'otpauth://totp/example-backoffice:user@example.co.jp?secret=JBSWY3DPEHPK3PXP'
const SHARED_SECRET = 'JBSWY3DPEHPK3PXP'
const StubView = { template: '<div />' }

let router: Router

async function mountLogin(fullPath = '/login'): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StubView },
      { path: '/login', name: 'login', component: StubView },
      { path: '/signup', name: 'signup', component: StubView },
      { path: '/sample-billing/settings', name: 'billing-settings', component: StubView },
    ],
  })
  await router.push(fullPath)
  await router.isReady()

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(LoginView, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

/** v-window-item は一度描画したステップを残すため、目的の入力欄を持つ form を選ぶ */
function formContaining(wrapper: VueWrapper, selector: string): DOMWrapper<Element> {
  const form = wrapper.findAll('form').find((candidate) => candidate.find(selector).exists())
  if (!form) {
    throw new Error(`${selector} を含む form が見つかりません`)
  }
  return form
}

describe('LoginView', () => {
  beforeEach(() => {
    holder.auth.email = null
    holder.auth.groups = []
    holder.auth.isLoading = false
    holder.auth.error = null
    holder.auth.pending = null
    holder.auth.isAuthenticated = false
    holder.auth.login.mockReset()
    holder.auth.submitChallenge.mockReset()
    holder.auth.confirmRegistration.mockReset()
  })

  it('初期状態はパスワード入力ステップ', async () => {
    const wrapper = await mountLogin()

    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[autocomplete="current-password"]').exists()).toBe(true)
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(false)
  })

  it("pending.kind='totp-code' でコード入力ステップに切り替わる", async () => {
    const wrapper = await mountLogin()
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)

    holder.auth.pending = { kind: 'totp-code' }
    await flushPromises()

    expect(wrapper.find('input[autocomplete="one-time-code"]').isVisible()).toBe(true)
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(false)
  })

  it("pending.kind='totp-setup' で QR（TotpEnroll）が出る", async () => {
    const wrapper = await mountLogin()
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(false)

    holder.auth.pending = {
      kind: 'totp-setup',
      setupUri: SETUP_URI,
      sharedSecret: SHARED_SECRET,
    }
    await flushPromises()

    const enroll = wrapper.findComponent(TotpEnroll)
    expect(enroll.exists()).toBe(true)
    expect(enroll.props('setupUri')).toBe(SETUP_URI)
    expect(enroll.text()).toContain(SHARED_SECRET)
    expect(enroll.find('img').attributes('src')).toContain('data:image/svg+xml')
  })

  it("pending.kind='new-password' で新パスワード入力が出る", async () => {
    const wrapper = await mountLogin()
    expect(wrapper.find('input[autocomplete="new-password"]').exists()).toBe(false)

    holder.auth.pending = { kind: 'new-password' }
    await flushPromises()

    expect(wrapper.find('input[autocomplete="new-password"]').isVisible()).toBe(true)
  })

  it('ログイン成功後 redirect クエリの先へ遷移する', async () => {
    const wrapper = await mountLogin('/login?redirect=%2Fsample-billing%2Fsettings')
    holder.auth.login.mockImplementation(async () => {
      holder.auth.pending = null
      holder.auth.email = 'user@example.co.jp'
      holder.auth.isAuthenticated = true
    })

    await wrapper.find('input[type="email"]').setValue('user@example.co.jp')
    await wrapper.find('input[autocomplete="current-password"]').setValue('Passw0rd!')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(holder.auth.login).toHaveBeenCalledWith('user@example.co.jp', 'Passw0rd!')
    expect(router.currentRoute.value.fullPath).toBe('/sample-billing/settings')
  })

  it('外部サイトを指す redirect クエリは無視してホームへ遷移する', async () => {
    const wrapper = await mountLogin('/login?redirect=%2F%2Fevil.example.com')
    holder.auth.login.mockImplementation(async () => {
      holder.auth.pending = null
      holder.auth.email = 'user@example.co.jp'
      holder.auth.isAuthenticated = true
    })

    await wrapper.find('input[type="email"]').setValue('user@example.co.jp')
    await wrapper.find('input[autocomplete="current-password"]').setValue('Passw0rd!')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(router.currentRoute.value.fullPath).toBe('/')
  })

  it("pending.kind='confirm-signup' は pending の username を使い、確認後はパスワード入力へ戻る", async () => {
    const wrapper = await mountLogin()

    holder.auth.pending = { kind: 'confirm-signup', username: 'newbie@example.co.jp' }
    await flushPromises()
    expect(wrapper.text()).toContain('newbie@example.co.jp')

    holder.auth.confirmRegistration.mockImplementation(async () => {
      holder.auth.pending = null
    })
    await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
    await formContaining(wrapper, 'input[autocomplete="one-time-code"]').trigger('submit')
    await flushPromises()

    expect(holder.auth.confirmRegistration).toHaveBeenCalledWith('newbie@example.co.jp', '123456')
    expect(holder.auth.login).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('サインインしてください')
  })

  it('チャレンジ中に「最初からやり直す」でパスワード入力へ戻れる', async () => {
    const wrapper = await mountLogin()

    holder.auth.pending = { kind: 'totp-code' }
    await flushPromises()

    const restart = wrapper.findAll('button').find((button) => button.text().includes('最初から'))
    await restart!.trigger('click')
    await flushPromises()

    expect(holder.auth.pending).toBeNull()
    expect(wrapper.find('input[type="email"]').isVisible()).toBe(true)
  })
})
