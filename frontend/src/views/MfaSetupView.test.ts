import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { fetchMFAPreference, setUpTOTP } from 'aws-amplify/auth'
import TotpEnroll from '@/components/TotpEnroll.vue'
import MfaSetupView from './MfaSetupView.vue'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

const SETUP_URI = 'otpauth://totp/example-backoffice:user@example.co.jp?secret=JBSWY3DPEHPK3PXP'
const SHARED_SECRET = 'JBSWY3DPEHPK3PXP'

function mockSetup(): void {
  vi.mocked(setUpTOTP).mockResolvedValue({
    sharedSecret: SHARED_SECRET,
    getSetupUri: () => new URL(SETUP_URI),
  } as unknown as Awaited<ReturnType<typeof setUpTOTP>>)
}

function mockPreference(enabled: string[]): void {
  vi.mocked(fetchMFAPreference).mockResolvedValue({
    enabled,
  } as unknown as Awaited<ReturnType<typeof fetchMFAPreference>>)
}

/** onActivated は KeepAlive 配下でのみ呼ばれるため、実際の配置に合わせて包む */
const Host = {
  components: { MfaSetupView },
  template: '<KeepAlive><MfaSetupView /></KeepAlive>',
}

async function mountView(): Promise<VueWrapper> {
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(Host, { global: { plugins: [vuetify] } })
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

describe('MfaSetupView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    mockSetup()
  })

  it('TOTP が未登録なら登録を開始して QR を出す', async () => {
    mockPreference([])

    const wrapper = await mountView()

    expect(fetchMFAPreference).toHaveBeenCalledTimes(1)
    expect(setUpTOTP).toHaveBeenCalledTimes(1)
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(true)
  })

  it('TOTP が登録済みなら QR を出さず状態と再登録ボタンを出す', async () => {
    mockPreference(['TOTP'])

    const wrapper = await mountView()

    expect(setUpTOTP).not.toHaveBeenCalled()
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(false)
    expect(wrapper.text()).toContain('登録済み')
    expect(wrapper.find('[data-test="reenroll"]').exists()).toBe(true)
  })

  it('再登録ボタンを押し、確認したときだけ新しい QR を出す', async () => {
    mockPreference(['TOTP'])
    const wrapper = await mountView()

    await wrapper.find('[data-test="reenroll"]').trigger('click')
    await flushPromises()
    expect(setUpTOTP).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('既存の認証アプリ')

    await wrapper.find('[data-test="reenroll-confirm"]').trigger('click')
    await flushPromises()

    expect(setUpTOTP).toHaveBeenCalledTimes(1)
    expect(wrapper.findComponent(TotpEnroll).exists()).toBe(true)
  })

  it('登録状況の取得に失敗したら登録を開始せずエラーを出す', async () => {
    vi.mocked(fetchMFAPreference).mockRejectedValue(new Error('boom'))

    const wrapper = await mountView()

    expect(setUpTOTP).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('登録状況を取得できませんでした')
  })
})
