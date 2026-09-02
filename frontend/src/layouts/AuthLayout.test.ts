import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { SITE_TITLE } from '@/config/site'
import AuthLayout from './AuthLayout.vue'

let pinia: Pinia

function mountLayout(): VueWrapper {
  const vuetify = createVuetify({ components, directives })
  return mount(AuthLayout, {
    global: { plugins: [pinia, vuetify] },
    slots: { default: '<div data-test="slot-content" />' },
  })
}

describe('AuthLayout', () => {
  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    document.body.innerHTML = ''
  })

  // サインイン前は ShellLayout が無く、どちらのサイトか手掛かりが無かった（UI-12 / R7）
  it('VITE_SITE_ENV 未設定なら検証環境チップが出る', () => {
    const chip = mountLayout().find('[data-test="site-env"]')

    expect(chip.exists()).toBe(true)
    expect(chip.text()).toBe('検証環境')
    expect(chip.classes()).toContain('bg-orange')
  })

  it('VITE_SITE_ENV=prod なら本番チップが出る', () => {
    vi.stubEnv('VITE_SITE_ENV', 'prod')

    const chip = mountLayout().find('[data-test="site-env"]')

    expect(chip.text()).toBe('本番')
    expect(chip.classes()).toContain('bg-green')
  })

  it('サイト名は site.ts の 1 箇所から引く', () => {
    expect(mountLayout().text()).toContain(SITE_TITLE)
  })

  it('スロットの内容を描画する', () => {
    expect(mountLayout().find('[data-test="slot-content"]').exists()).toBe(true)
  })
})
