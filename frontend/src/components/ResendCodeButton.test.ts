import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import ResendCodeButton from './ResendCodeButton.vue'

function mountButton() {
  const vuetify = createVuetify({ components, directives })
  return mount(ResendCodeButton, { global: { plugins: [vuetify] } })
}

describe('ResendCodeButton', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('押すと resend を発火し、抑止時間の残りを表示する', async () => {
    const wrapper = mountButton()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('resend')).toHaveLength(1)
    expect(wrapper.text()).toContain('30')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  })

  it('抑止中の連打では resend を発火しない', async () => {
    const wrapper = mountButton()

    await wrapper.find('button').trigger('click')
    await wrapper.find('button').trigger('click')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('resend')).toHaveLength(1)
  })

  it('抑止時間が過ぎると再び押せる', async () => {
    const wrapper = mountButton()

    await wrapper.find('button').trigger('click')
    vi.advanceTimersByTime(30_000)
    await flushPromises()

    expect(wrapper.find('button').attributes('disabled')).toBeUndefined()

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('resend')).toHaveLength(2)
  })
})
