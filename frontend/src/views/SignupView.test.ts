import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { Pending } from '@/stores/auth'
import SignupView from './SignupView.vue'

interface AuthMock {
  isLoading: boolean
  error: string | null
  pending: Pending
  register: Mock
  confirmRegistration: Mock
  resendCode: Mock
}

const holder = vi.hoisted(() => ({ auth: null as unknown as AuthMock }))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({
    isLoading: false,
    error: null,
    pending: null,
    register: vi.fn(),
    confirmRegistration: vi.fn(),
    resendCode: vi.fn(),
  }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

const EMAIL = 'newbie@example.co.jp'
const StubView = { template: '<div />' }

let router: Router

async function mountView(): Promise<VueWrapper> {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', name: 'home', component: StubView },
      { path: '/login', name: 'login', component: StubView },
    ],
  })
  await router.push('/')
  await router.isReady()

  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(SignupView, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

function formContaining(wrapper: VueWrapper, selector: string): DOMWrapper<Element> {
  const form = wrapper.findAll('form').find((candidate) => candidate.find(selector).exists())
  if (!form) {
    throw new Error(`${selector} を含む form が見つかりません`)
  }
  return form
}

async function register(wrapper: VueWrapper): Promise<void> {
  holder.auth.register.mockImplementation(async () => {
    holder.auth.pending = { kind: 'confirm-signup', username: EMAIL }
  })
  await wrapper.find('input[type="email"]').setValue(EMAIL)
  await wrapper.find('input[autocomplete="new-password"]').setValue('Passw0rd2345')
  await formContaining(wrapper, 'input[type="email"]').trigger('submit')
  await flushPromises()
}

describe('SignupView', () => {
  beforeEach(() => {
    holder.auth.isLoading = false
    holder.auth.error = null
    holder.auth.pending = null
    holder.auth.register.mockReset()
    holder.auth.confirmRegistration.mockReset()
    holder.auth.resendCode.mockReset()
  })

  it('登録すると確認コード入力ステップへ進む', async () => {
    const wrapper = await mountView()
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)

    await register(wrapper)

    expect(holder.auth.register).toHaveBeenCalledWith(EMAIL, 'Passw0rd2345')
    expect(wrapper.find('input[autocomplete="one-time-code"]').isVisible()).toBe(true)
  })

  it('確認ステップで確認コードを再送できる', async () => {
    const wrapper = await mountView()
    await register(wrapper)

    const resend = wrapper.find('[data-test="resend-code"]')
    expect(resend.exists()).toBe(true)

    await resend.trigger('click')
    await flushPromises()

    expect(holder.auth.resendCode).toHaveBeenCalledWith(EMAIL)
    expect(wrapper.text()).toContain('確認コードを再送しました')
  })

  it('再送に失敗したら成功メッセージを出さない', async () => {
    const wrapper = await mountView()
    await register(wrapper)
    holder.auth.resendCode.mockRejectedValue(new Error('boom'))

    await wrapper.find('[data-test="resend-code"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('確認コードを再送しました')
  })
})
