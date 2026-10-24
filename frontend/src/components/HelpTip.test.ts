import { beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useUiStore } from '@/stores/ui'
import HelpTip from './HelpTip.vue'

const TEXT = '実行履歴を取り直します'

const Host = {
  components: { HelpTip },
  template: `
    <HelpTip text="${TEXT}">
      <template #default="{ props }">
        <button
          v-bind="props"
          data-test="target"
        >更新</button>
      </template>
    </HelpTip>
  `,
}

let pinia: Pinia

function mountHost(): VueWrapper {
  const vuetify = createVuetify({ components, directives })
  return mount(Host, {
    attachTo: document.body,
    global: { plugins: [pinia, vuetify] },
  }) as unknown as VueWrapper
}

async function hover(wrapper: VueWrapper): Promise<void> {
  await wrapper.find('[data-test="target"]').trigger('mouseenter')
  await flushPromises()
}

function activeOverlays(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll('.v-overlay--active'))
}

describe('HelpTip', () => {
  beforeEach(() => {
    pinia = createPinia()
    setActivePinia(pinia)
    document.body.innerHTML = ''
  })

  // eager のままだと閉じたオーバーレイが地点の数だけ DOM と document リスナに積み上がる
  it('ヘルプモードが OFF ならオーバーレイを作らない', async () => {
    const wrapper = mountHost()

    await hover(wrapper)

    expect(activeOverlays()).toHaveLength(0)
    expect(document.body.querySelectorAll('.v-overlay')).toHaveLength(0)
  })

  it('ヘルプモードが ON ならホバーで説明が出る', async () => {
    useUiStore().toggleHelpMode()
    const wrapper = mountHost()

    await hover(wrapper)

    const overlays = activeOverlays()
    expect(overlays).toHaveLength(1)
    expect(overlays[0].textContent).toContain(TEXT)
    expect(document.body.querySelectorAll('.v-overlay')).toHaveLength(1)
  })

  // 子要素が activator になっていないとホバーの当たり判定がずれる
  it('activator の props がスロットへ渡る', () => {
    const wrapper = mountHost()

    expect(wrapper.find('[data-test="target"]').attributes('aria-describedby')).toBeTruthy()
  })
})
