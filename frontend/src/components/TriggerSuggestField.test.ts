import { beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import TriggerSuggestField, { type SuggestItem } from './TriggerSuggestField.vue'

const USERS: SuggestItem[] = [
  { value: '<!here>', label: '@here', hint: 'オンライン全員' },
  { value: '<!channel>', label: '@channel', hint: 'チャンネル全員' },
  {
    value: '<@U0000DEVA1>',
    label: '@開発者本人',
    hint: 'DevA · @devA',
    keywords: ['devA', 'DevA', '開発者本人'],
  },
  {
    value: '<@U0000002>',
    label: '@関係者B',
    hint: 'ColleagueB · @colleagueb',
    keywords: ['colleagueb', 'ColleagueB', '関係者B'],
  },
]

const CHANNELS: SuggestItem[] = [
  { value: 'C0123ABCD', label: '#proj-saas-a-billing', keywords: ['proj-saas-a-billing'] },
  { value: 'G0999ZZZZ', label: '#secret', hint: '非公開', keywords: ['secret'] },
]

function mountField(props: Record<string, unknown> = {}): VueWrapper {
  const vuetify = createVuetify({ components, directives })
  return mount(TriggerSuggestField, {
    attachTo: document.body,
    props: {
      trigger: '@',
      items: USERS,
      modelValue: [],
      multiple: true,
      label: 'Slack メンション',
      ...props,
    },
    global: { plugins: [vuetify] },
  }) as unknown as VueWrapper
}

async function type(wrapper: VueWrapper, text: string): Promise<void> {
  await wrapper.find('input').setValue(text)
  await flushPromises()
}

function options(): HTMLElement[] {
  return Array.from(document.body.querySelectorAll('[data-test="suggest-option"]'))
}

function labels(): string[] {
  return options().map(
    (option) => option.querySelector('[data-test="suggest-label"]')?.textContent?.trim() ?? '',
  )
}

beforeEach(() => {
  document.body.innerHTML = ''
})

describe('TriggerSuggestField', () => {
  it('トリガーを打つまで候補は出ない', async () => {
    const wrapper = mountField()

    await type(wrapper, 'かいはつ')

    expect(options()).toHaveLength(0)
  })

  // 語の先頭のトリガーだけを見る。メールアドレスの貼り付けで開かない
  it('語の途中の @ では開かない', async () => {
    const wrapper = mountField()

    await type(wrapper, 'a@b.com')

    expect(options()).toHaveLength(0)
    expect(document.body.querySelector('[data-test="suggest-list"]')).toBeNull()
  })

  it('候補の読み込み中はその旨を出す', async () => {
    const wrapper = mountField({ items: [], loading: true })

    await type(wrapper, '@')

    expect(document.body.querySelector('[data-test="suggest-empty"]')?.textContent).toContain(
      '候補を読み込んでいます',
    )
  })

  it('@ を打つと候補が開く', async () => {
    const wrapper = mountField()

    await type(wrapper, '@')

    expect(labels()).toEqual(['@here', '@channel', '@開発者本人', '@関係者B'])
  })

  it('@開発 で候補が絞り込まれる', async () => {
    const wrapper = mountField()

    await type(wrapper, '@開発')

    expect(labels()).toEqual(['@開発者本人'])
  })

  it('英字のハンドルでも大文字小文字を無視して絞り込む', async () => {
    const wrapper = mountField()

    await type(wrapper, '@DEVA')

    expect(labels()).toEqual(['@開発者本人'])
  })

  it('該当が無いと知らせる', async () => {
    const wrapper = mountField()

    await type(wrapper, '@そんな人はいない')

    expect(options()).toHaveLength(0)
    expect(document.body.querySelector('[data-test="suggest-empty"]')?.textContent).toContain(
      '該当する候補がありません',
    )
  })

  it('クリックで選択して emit する', async () => {
    const wrapper = mountField()
    await type(wrapper, '@開発')

    options()[0].click()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<@U0000DEVA1>']])
    expect(wrapper.find('input').element.value).toBe('')
  })

  it('Enter で先頭の候補を選ぶ', async () => {
    const wrapper = mountField()
    await type(wrapper, '@開発')

    await wrapper.find('input').trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<@U0000DEVA1>']])
  })

  it('↓ で候補を移動してから選ぶ', async () => {
    const wrapper = mountField()
    await type(wrapper, '@')

    await wrapper.find('input').trigger('keydown', { key: 'ArrowDown' })
    await wrapper.find('input').trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<!channel>']])
  })

  it('↑ で末尾の候補へ回り込む', async () => {
    const wrapper = mountField()
    await type(wrapper, '@')

    await wrapper.find('input').trigger('keydown', { key: 'ArrowUp' })
    await wrapper.find('input').trigger('keydown', { key: 'Enter' })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<@U0000002>']])
  })

  it('Esc で閉じる', async () => {
    const wrapper = mountField()
    await type(wrapper, '@開発')

    await wrapper.find('input').trigger('keydown', { key: 'Escape' })
    await flushPromises()

    expect(options()).toHaveLength(0)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('multiple では末尾に足し、重複は足さない', async () => {
    const wrapper = mountField({ modelValue: ['<!here>'] })
    await type(wrapper, '@開発')

    options()[0].click()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<!here>', '<@U0000DEVA1>']])

    await wrapper.setProps({ modelValue: ['<!here>', '<@U0000DEVA1>'] })
    await type(wrapper, '@開発')
    options()[0].click()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<!here>', '<@U0000DEVA1>']])
  })

  it('multiple でないときは置き換える', async () => {
    const wrapper = mountField({
      trigger: '#',
      items: CHANNELS,
      multiple: false,
      modelValue: ['C0123ABCD'],
      label: 'Slack チャンネル',
    })

    await type(wrapper, '#sec')
    options()[0].click()
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['G0999ZZZZ']])
  })

  it('# でチャンネル候補が開く', async () => {
    const wrapper = mountField({ trigger: '#', items: CHANNELS, multiple: false })

    await type(wrapper, '#saas-a')

    expect(labels()).toEqual(['#proj-saas-a-billing'])
  })

  it('選択済みはラベルのチップで出し、ID は出さない', () => {
    const wrapper = mountField({
      trigger: '#',
      items: CHANNELS,
      multiple: false,
      modelValue: ['C0123ABCD'],
    })

    expect(wrapper.text()).toContain('#proj-saas-a-billing')
    expect(wrapper.html()).not.toContain('C0123ABCD')
  })

  it('候補に無い値は fallbackLabel で出す', () => {
    const wrapper = mountField({
      modelValue: ['<@U9999999>'],
      fallbackLabel: () => '@不明なユーザー',
    })

    expect(wrapper.text()).toContain('@不明なユーザー')
    expect(wrapper.html()).not.toContain('U9999999')
  })

  it('空入力の Backspace で最後のチップを外す', async () => {
    const wrapper = mountField({ modelValue: ['<!here>', '<@U0000DEVA1>'] })

    await wrapper.find('input').trigger('keydown', { key: 'Backspace' })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<!here>']])
  })

  it('入力中の Backspace ではチップを外さない', async () => {
    const wrapper = mountField({ modelValue: ['<!here>'] })
    await type(wrapper, '@開発')

    await wrapper.find('input').trigger('keydown', { key: 'Backspace' })
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('チップの × で外せる', async () => {
    const wrapper = mountField({ modelValue: ['<!here>', '<@U0000DEVA1>'] })

    await wrapper.find('.v-chip__close').trigger('click')
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<@U0000DEVA1>']])
  })

  // 保存済みの値は重複しうる（例 `<!here> <!here>`）。片方だけ外せること
  it('同じ値が 2 つ並んでも片方だけ外せる', async () => {
    const wrapper = mountField({ modelValue: ['<!here>', '<!here>'] })

    expect(wrapper.findAll('.v-chip')).toHaveLength(2)

    await wrapper.findAll('.v-chip__close')[0].trigger('click')
    await flushPromises()

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([['<!here>']])
  })

  it('チップは折り返して並べる（入力欄を潰さない）', () => {
    const wrapper = mountField({ modelValue: ['<!here>', '<!channel>'] })

    expect(wrapper.find('[data-test="suggest-chips"]').classes()).toContain('flex-wrap')
  })

  it('無効なら候補を開かず入力もできない', async () => {
    const wrapper = mountField({ disabled: true })

    expect(wrapper.find('input').attributes('disabled')).toBeDefined()

    await type(wrapper, '@')

    expect(options()).toHaveLength(0)
  })

  it('候補のリストに listbox / option の役割と aria の対応を持たせる', async () => {
    const wrapper = mountField()

    expect(wrapper.find('input').attributes('aria-autocomplete')).toBe('list')
    expect(wrapper.find('input').attributes('aria-expanded')).toBe('false')

    await type(wrapper, '@')

    const list = document.body.querySelector('[role="listbox"]')
    expect(list).not.toBeNull()
    expect(options()[0].getAttribute('role')).toBe('option')
    expect(wrapper.find('input').attributes('aria-expanded')).toBe('true')
    expect(wrapper.find('input').attributes('aria-controls')).toBe(list?.id)
    expect(wrapper.find('input').attributes('aria-activedescendant')).toBe(options()[0].id)

    await wrapper.find('input').trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()

    expect(wrapper.find('input').attributes('aria-activedescendant')).toBe(options()[1].id)
  })
})
