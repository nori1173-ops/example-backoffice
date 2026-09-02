import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type DOMWrapper, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import ForgotPasswordView from './ForgotPasswordView.vue'

interface AuthMock {
  isLoading: boolean
  error: string | null
  requestPasswordReset: Mock
  submitPasswordReset: Mock
}

const holder = vi.hoisted(() => ({ auth: null as unknown as AuthMock }))

vi.mock('@/stores/auth', async () => {
  const { reactive } = await import('vue')
  holder.auth = reactive({
    isLoading: false,
    error: null,
    requestPasswordReset: vi.fn(),
    submitPasswordReset: vi.fn(),
  }) as AuthMock
  return { useAuthStore: () => holder.auth }
})

const EMAIL = 'user@example.co.jp'
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
  const wrapper = mount(ForgotPasswordView, { global: { plugins: [vuetify, router] } })
  await flushPromises()
  return wrapper
}

/** v-window-item は描画済みステップを残すため、目的の入力欄を持つ form を選ぶ */
function formContaining(wrapper: VueWrapper, selector: string): DOMWrapper<Element> {
  const form = wrapper.findAll('form').find((candidate) => candidate.find(selector).exists())
  if (!form) {
    throw new Error(`${selector} を含む form が見つかりません`)
  }
  return form
}

async function goToConfirmStep(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('input[type="email"]').setValue(EMAIL)
  await formContaining(wrapper, 'input[type="email"]').trigger('submit')
  await flushPromises()
}

describe('ForgotPasswordView', () => {
  beforeEach(() => {
    holder.auth.isLoading = false
    holder.auth.error = null
    holder.auth.requestPasswordReset.mockReset()
    holder.auth.submitPasswordReset.mockReset()
  })

  it('初期状態はメールアドレス入力ステップ', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
  })

  it('メールアドレスを送ると requestPasswordReset を呼びコード入力へ進む', async () => {
    const wrapper = await mountView()

    await goToConfirmStep(wrapper)

    expect(holder.auth.requestPasswordReset).toHaveBeenCalledWith(EMAIL)
    expect(wrapper.find('input[autocomplete="one-time-code"]').isVisible()).toBe(true)
    expect(wrapper.find('input[autocomplete="new-password"]').isVisible()).toBe(true)
  })

  it('requestPasswordReset が失敗したらコード入力へ進まない', async () => {
    const wrapper = await mountView()
    holder.auth.requestPasswordReset.mockRejectedValue(new Error('boom'))

    await goToConfirmStep(wrapper)

    expect(wrapper.find('input[autocomplete="one-time-code"]').exists()).toBe(false)
  })

  it('コードと新パスワードで submitPasswordReset を呼び、完了後はサインイン導線を出す', async () => {
    const wrapper = await mountView()
    await goToConfirmStep(wrapper)

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
    await wrapper.find('input[autocomplete="new-password"]').setValue('NewPassw0rd')
    await formContaining(wrapper, 'input[autocomplete="one-time-code"]').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitPasswordReset).toHaveBeenCalledWith(EMAIL, '123456', 'NewPassw0rd')
    expect(wrapper.text()).toContain('パスワードを再設定しました')
  })

  it('パスワードポリシーを満たさない場合は送信しない', async () => {
    const wrapper = await mountView()
    await goToConfirmStep(wrapper)

    await wrapper.find('input[autocomplete="one-time-code"]').setValue('123456')
    await wrapper.find('input[autocomplete="new-password"]').setValue('short')
    await formContaining(wrapper, 'input[autocomplete="one-time-code"]').trigger('submit')
    await flushPromises()

    expect(holder.auth.submitPasswordReset).not.toHaveBeenCalled()
  })

  it('コード入力ステップから確認コードを再送できる', async () => {
    const wrapper = await mountView()
    await goToConfirmStep(wrapper)
    expect(holder.auth.requestPasswordReset).toHaveBeenCalledTimes(1)

    const resend = wrapper.find('[data-test="resend-code"]')
    await resend.trigger('click')
    await flushPromises()

    expect(holder.auth.requestPasswordReset).toHaveBeenCalledTimes(2)
  })
})
