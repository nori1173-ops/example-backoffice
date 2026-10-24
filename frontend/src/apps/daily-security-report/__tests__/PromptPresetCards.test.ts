import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import PromptPresetCards from '../components/PromptPresetCards.vue'
import type { PromptPreset } from '../types'

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
const NOW = new Date('2026-10-16T13:50:00+09:00')
const DEFAULT = '既定の本文です。'
function freeze(presets: PromptPreset[]): PromptPreset[] {
  return Object.freeze(presets.map(preset => Object.freeze({ ...preset }))) as PromptPreset[]
}
const PRESETS = freeze([
  { id: '11111111-1111-4111-8111-111111111111', name: '本番用', prompt: '本番用の文面', updated_at: '2026-10-15T23:30:00.000Z' },
  { id: '22222222-2222-4222-8222-222222222222', name: '検証用', prompt: 'あ'.repeat(130), updated_at: '2026-10-16T09:15:00+09:00' },
])
const sized = (index: number, chars: number): PromptPreset => ({
  id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, name: `案 ${index}`,
  prompt: ` ${'あ'.repeat(chars)}\n`, updated_at: NOW.toISOString(),
})
let wrapper: VueWrapper
function mountCards(props: Partial<{ presets: PromptPreset[]; currentPrompt: string; defaultPrompt: string; editable: boolean }> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  wrapper = mount(PromptPresetCards, {
    props: { presets: PRESETS, currentPrompt: '今の文面', defaultPrompt: DEFAULT, editable: true, ...props },
    global: { plugins: [pinia, createVuetify({ components, directives })] },
  })
  return wrapper
}
const body = (id: string) => document.body.querySelector(`[data-test="${id}"]`) as HTMLElement
const cards = () => wrapper.findAll('[data-test="preset-card"]')
const emittedPresets = () => wrapper.emitted('update:presets') as PromptPreset[][][] | undefined
async function click(target: { trigger: (event: string) => Promise<void> } | HTMLElement): Promise<void> {
  if (target instanceof HTMLElement) target.click()
  else await target.trigger('click')
  await flushPromises()
}
async function typeName(value: string): Promise<void> {
  const field = document.body.querySelector('[data-test="preset-name-input"] input') as HTMLInputElement
  field.value = value
  field.dispatchEvent(new Event('input'))
  await flushPromises()
}
const nameInput = () => document.body.querySelector('[data-test="preset-name-input"] input') as HTMLInputElement
const disabled = (element: HTMLElement) => element.hasAttribute('disabled')

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'], now: NOW })
})
afterEach(() => {
  wrapper?.unmount()
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('PromptPresetCards', () => {
  it('既定のカードを先頭に固定し、適用だけを持つ', () => {
    mountCards()
    const first = wrapper.get('[data-test="preset-default"]')
    expect(wrapper.find('[data-test="preset-default"], [data-test="preset-card"]').attributes('data-test')).toBe('preset-default')
    expect(first.text()).toContain('既定')
    expect(first.text()).toContain(DEFAULT)
    expect(first.find('[data-test="preset-apply"]').exists()).toBe(true)
    for (const action of ['overwrite', 'rename', 'delete']) expect(first.find(`[data-test="preset-${action}"]`).exists()).toBe(false)
  })
  it('ユーザーのカードを配列順に、名前・更新日時（JST）・本文の先頭 120 字で出す', () => {
    mountCards()
    expect(cards().map(card => card.get('[data-test="preset-name"]').text())).toEqual(['本番用', '検証用'])
    expect(cards()[0].text()).toContain('2026-10-16 08:30')
    expect(cards()[1].text()).toContain('2026-10-16 09:15')
    expect(cards()[1].text()).toContain(`${'あ'.repeat(120)}…`)
    expect(cards()[1].text()).not.toContain('あ'.repeat(121))
    for (const action of ['apply', 'overwrite', 'rename', 'delete']) expect(cards()[0].find(`[data-test="preset-${action}"]`).exists()).toBe(true)
    expect(wrapper.text()).toContain('カードの変更は設定の「保存」で反映されます')
  })
  it('trim 後に現在の文面と一致するカードだけに「使用中」を付ける', async () => {
    mountCards({ currentPrompt: '  本番用の文面\n' })
    expect(cards()[0].find('[data-test="preset-active"]').exists()).toBe(true)
    expect(cards()[1].find('[data-test="preset-active"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="preset-default"]').find('[data-test="preset-active"]').exists()).toBe(false)
    await wrapper.setProps({ currentPrompt: ` ${DEFAULT} ` })
    expect(wrapper.get('[data-test="preset-default"]').find('[data-test="preset-active"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-test="preset-active"]')).toHaveLength(1)
  })
  it('適用でそのカードの本文を emit し、配列は変えない', async () => {
    mountCards()
    await click(cards()[1].get('[data-test="preset-apply"]'))
    await click(wrapper.get('[data-test="preset-default"] [data-test="preset-apply"]'))
    expect(wrapper.emitted('apply')).toEqual([['あ'.repeat(130)], [DEFAULT]])
    expect(emittedPresets()).toBeUndefined()
  })
  it('新規保存は名前を検証し、確認するまで配列を変えず、末尾に足す', async () => {
    mountCards({ currentPrompt: ' 新しい文面\n' })
    await click(wrapper.get('[data-test="preset-new"]'))
    expect(body('preset-name-dialog')).toBeTruthy()
    expect(disabled(body('preset-name-confirm'))).toBe(true)
    await typeName(' 本番用 ')
    expect(body('preset-name-dialog').textContent).toContain('同じ名前のカードがあります')
    expect(disabled(body('preset-name-confirm'))).toBe(true)
    await typeName('名'.repeat(41))
    expect(body('preset-name-dialog').textContent).toContain('名前は 40 字以内にしてください')
    expect(disabled(body('preset-name-confirm'))).toBe(true)
    await typeName('   ')
    expect(body('preset-name-dialog').textContent).toContain('名前を入力してください')
    await typeName(' 新しい案 ')
    expect(disabled(body('preset-name-confirm'))).toBe(false)
    expect(emittedPresets()).toBeUndefined()
    await click(body('preset-name-confirm'))
    const next = emittedPresets()![0][0]
    expect(next).toHaveLength(3)
    expect(next.slice(0, 2)).toEqual(PRESETS)
    expect(next[2]).toEqual({ id: expect.stringMatching(UUID_V4), name: '新しい案', prompt: ' 新しい文面\n', updated_at: NOW.toISOString() })
    expect(PRESETS).toHaveLength(2)
  })
  it('新規保存をキャンセルすると配列を変えない', async () => {
    mountCards()
    await click(wrapper.get('[data-test="preset-new"]'))
    await typeName('取り消す案')
    await click(body('preset-name-cancel'))
    expect(emittedPresets()).toBeUndefined()
  })
  it('空白だけの文面・20 件・編集不可では新規保存できず、20 件ではヒントを出す', async () => {
    mountCards({ currentPrompt: ' \n ' })
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeDefined()
    const twenty = freeze(Array.from({ length: 20 }, (_, index) => ({
      id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, name: `案 ${index}`, prompt: `本文 ${index}`, updated_at: NOW.toISOString(),
    })))
    await wrapper.setProps({ currentPrompt: '今の文面', presets: twenty })
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('20 件まで')
    await wrapper.setProps({ presets: PRESETS })
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.text()).not.toContain('20 件まで')
    await wrapper.setProps({ editable: false })
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeDefined()
  })
  it('見出しに本文の合計字数（trim 後）を常に出し、20,000 字を超えると赤字にする', async () => {
    mountCards()
    const total = () => wrapper.get('[data-test="preset-total"]')
    expect(total().text()).toBe('合計 136 / 20,000 字')
    expect(total().classes()).not.toContain('text-error')
    await wrapper.setProps({ presets: [] })
    expect(total().text()).toBe('合計 0 / 20,000 字')
    await wrapper.setProps({ presets: freeze([sized(1, 8000), sized(2, 8000), sized(3, 4000)]) })
    expect(total().text()).toBe('合計 20,000 / 20,000 字')
    expect(total().classes()).not.toContain('text-error')
    await wrapper.setProps({ presets: freeze([sized(1, 8000), sized(2, 8000), sized(3, 4001)]) })
    expect(total().text()).toBe('合計 20,001 / 20,000 字')
    expect(total().classes()).toContain('text-error')
  })
  it('新規保存は確定後の合計が 20,000 字を超えるなら無効にし、「合計 20,000 字まで」を出す', async () => {
    mountCards({ presets: freeze([sized(1, 8000), sized(2, 8000), sized(3, 3990)]), currentPrompt: ` ${'い'.repeat(10)}\n` })
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[data-test="preset-total-limit"]').exists()).toBe(false)
    await wrapper.setProps({ currentPrompt: 'い'.repeat(11) })
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-test="preset-total-limit"]').text()).toBe('合計 20,000 字まで')
    expect(wrapper.find('[data-test="preset-limit"]').exists()).toBe(false)
  })
  it('上書き保存は確定後の合計が 20,000 字を超えるカードだけ無効にし、「合計 20,000 字まで」を出す', async () => {
    const presets = freeze([sized(1, 8000), sized(2, 8000), sized(3, 4000)])
    mountCards({ presets, currentPrompt: 'う'.repeat(4000) })
    for (const card of cards()) {
      expect(card.get('[data-test="preset-overwrite"]').attributes('disabled')).toBeUndefined()
      expect(card.find('[data-test="preset-overwrite-limit"]').exists()).toBe(false)
    }
    await wrapper.setProps({ currentPrompt: 'う'.repeat(4001) })
    expect(cards()[2].get('[data-test="preset-overwrite"]').attributes('disabled')).toBeDefined()
    expect(cards()[2].get('[data-test="preset-overwrite-limit"]').text()).toBe('合計 20,000 字まで')
    expect(cards()[0].get('[data-test="preset-overwrite"]').attributes('disabled')).toBeUndefined()
    expect(cards()[0].find('[data-test="preset-overwrite-limit"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="preset-new"]').attributes('disabled')).toBeDefined()
    await click(cards()[0].get('[data-test="preset-overwrite"]'))
    await click(body('preset-confirm'))
    expect(emittedPresets()![0][0]).toEqual([{ ...presets[0], prompt: 'う'.repeat(4001), updated_at: NOW.toISOString() }, presets[1], presets[2]])
  })
  it('確認の間に文面が伸びて合計を超えたら上書きしない', async () => {
    mountCards({ presets: freeze([sized(1, 8000), sized(2, 8000), sized(3, 4000)]), currentPrompt: 'う'.repeat(4000) })
    await click(cards()[2].get('[data-test="preset-overwrite"]'))
    await wrapper.setProps({ currentPrompt: 'う'.repeat(4001) })
    await click(body('preset-confirm'))
    expect(emittedPresets()).toBeUndefined()
  })
  it('上書き保存は確認ダイアログの後にだけ本文と更新日時を置換する', async () => {
    mountCards({ currentPrompt: '上書きする文面' })
    await click(cards()[1].get('[data-test="preset-overwrite"]'))
    expect(body('preset-confirm-dialog').textContent).toContain('検証用 を現在の文面で上書きします')
    expect(body('preset-confirm').textContent).toContain('上書きする')
    expect(emittedPresets()).toBeUndefined()
    await click(body('preset-confirm-cancel'))
    expect(emittedPresets()).toBeUndefined()
    await click(cards()[1].get('[data-test="preset-overwrite"]'))
    await click(body('preset-confirm'))
    expect(emittedPresets()![0][0]).toEqual([PRESETS[0], { ...PRESETS[1], prompt: '上書きする文面', updated_at: NOW.toISOString() }])
  })
  it('名前変更は同じ検証で、確認した後にだけ名前を変える', async () => {
    mountCards()
    await click(cards()[0].get('[data-test="preset-rename"]'))
    expect(nameInput().value).toBe('本番用')
    expect(disabled(body('preset-name-confirm'))).toBe(false)
    await typeName('検証用')
    expect(body('preset-name-dialog').textContent).toContain('同じ名前のカードがあります')
    expect(disabled(body('preset-name-confirm'))).toBe(true)
    await typeName(' 改名した案 ')
    expect(emittedPresets()).toBeUndefined()
    await click(body('preset-name-confirm'))
    expect(emittedPresets()![0][0]).toEqual([{ ...PRESETS[0], name: '改名した案' }, PRESETS[1]])
  })
  it('削除は確認ダイアログの後にだけ取り除く', async () => {
    mountCards()
    await click(cards()[0].get('[data-test="preset-delete"]'))
    expect(body('preset-confirm-dialog').textContent).toContain('本番用 を削除します')
    expect(body('preset-confirm').textContent).toContain('削除する')
    await click(body('preset-confirm-cancel'))
    expect(emittedPresets()).toBeUndefined()
    await click(cards()[0].get('[data-test="preset-delete"]'))
    await click(body('preset-confirm'))
    expect(emittedPresets()![0][0]).toEqual([PRESETS[1]])
  })
  it('空白だけの文面では上書き保存できず、既定（保存すると既定になる）を使用中とする', () => {
    mountCards({ currentPrompt: '  ' })
    expect(cards()[0].get('[data-test="preset-overwrite"]').attributes('disabled')).toBeDefined()
    expect(cards()[0].get('[data-test="preset-rename"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.get('[data-test="preset-default"]').find('[data-test="preset-active"]').exists()).toBe(true)
  })
  it('編集不可ならカードを見せたままボタンをすべて無効にする', async () => {
    mountCards({ editable: false })
    expect(cards()).toHaveLength(2)
    const buttons = wrapper.findAll('button')
    expect(buttons.length).toBeGreaterThanOrEqual(10)
    for (const button of buttons) expect(button.attributes('disabled')).toBeDefined()
    await click(cards()[0].get('[data-test="preset-delete"]'))
    expect(body('preset-confirm-dialog')).toBeNull()
  })
})
