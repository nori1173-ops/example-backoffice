import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useUiStore } from '@/stores/ui'
import type { AdminUser } from '../types'
import UsersView from '../views/UsersView.vue'

interface UsersStoreMock {
  users: AdminUser[]
  selfSub: string
  loading: boolean
  error: string | null
  fetch: Mock
  setAdmin: Mock
  setEnabled: Mock
  resetPassword: Mock
  remove: Mock
}

const holder = vi.hoisted(() => ({ users: null as unknown as UsersStoreMock }))

vi.mock('../stores/users', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../stores/users')>()
  const { reactive } = await import('vue')
  holder.users = reactive({
    users: [],
    selfSub: '',
    loading: false,
    error: null,
    fetch: vi.fn(),
    setAdmin: vi.fn(),
    setEnabled: vi.fn(),
    resetPassword: vi.fn(),
    remove: vi.fn(),
  }) as UsersStoreMock
  return { ...actual, useUsersStore: () => holder.users }
})

const SELF_SUB = '00000000-0000-0000-0000-000000000001'
const OTHER_SUB = '00000000-0000-0000-0000-000000000002'

/** onActivated は KeepAlive 配下でのみ呼ばれるため、実際の配置に合わせて包む */
const Host = { components: { UsersView }, template: '<KeepAlive><UsersView /></KeepAlive>' }

function makeUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    sub: OTHER_SUB,
    email: 'target@example.co.jp',
    status: 'CONFIRMED',
    enabled: true,
    is_admin: false,
    created_at: '2026-08-01T01:23:00+00:00',
    updated_at: '2026-08-02T03:45:00+00:00',
    ...overrides,
  }
}

function makeSelf(overrides: Partial<AdminUser> = {}): AdminUser {
  return makeUser({ sub: SELF_SUB, email: 'me@example.co.jp', is_admin: true, ...overrides })
}

async function mountView(): Promise<VueWrapper> {
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(Host, { global: { plugins: [vuetify] }, attachTo: document.body })
  await flushPromises()
  return wrapper as unknown as VueWrapper
}

/** 確認ダイアログは teleport されるため、本文は document から拾う */
function dialogText(): string {
  return document.body.textContent ?? ''
}

async function clickAndConfirm(wrapper: VueWrapper, testId: string): Promise<void> {
  await wrapper.find(`[data-test="${testId}"]`).trigger('click')
  await flushPromises()
  const ok = document.querySelector('[data-test="confirm-ok"]') as HTMLElement | null
  expect(ok, '確認ダイアログの実行ボタンが出ていない').not.toBeNull()
  ok?.click()
  await flushPromises()
}

beforeEach(() => {
  document.body.innerHTML = ''
  setActivePinia(createPinia())
  holder.users.users = []
  holder.users.selfSub = SELF_SUB
  holder.users.loading = false
  holder.users.error = null
  holder.users.fetch.mockReset()
  holder.users.setAdmin.mockReset().mockResolvedValue(true)
  holder.users.setEnabled.mockReset().mockResolvedValue(true)
  holder.users.resetPassword.mockReset().mockResolvedValue(true)
  holder.users.remove.mockReset().mockResolvedValue(true)
})

describe('UsersView', () => {
  it('開いたときに一覧を取り直す', async () => {
    await mountView()

    expect(holder.users.fetch).toHaveBeenCalled()
  })

  it('メール・状態・権限・日時を描画する', async () => {
    holder.users.users = [makeUser(), makeSelf()]
    const wrapper = await mountView()

    expect(wrapper.findAll('tbody tr')).toHaveLength(2)
    const row = wrapper.find(`[data-test="row-${OTHER_SUB}"]`)
    expect(row.text()).toContain('target@example.co.jp')
    expect(row.text()).toContain('有効')
    expect(row.text()).toContain('確認済み')
    expect(row.text()).toContain('一般')
    // UTC の ISO を JST（+9h）に直して表示する
    expect(row.text()).toContain('2026-08-01 10:23')
    expect(row.text()).toContain('2026-08-02 12:45')
  })

  it('未確認・無効のユーザーが状態から読み取れる', async () => {
    holder.users.users = [makeUser({ enabled: false, status: 'UNCONFIRMED' })]
    const wrapper = await mountView()

    const row = wrapper.find(`[data-test="row-${OTHER_SUB}"]`)
    expect(row.text()).toContain('無効')
    expect(row.text()).toContain('未確認')
  })

  it('自分の行に「自分」チップが出て、解除・無効化・削除が押せない', async () => {
    holder.users.users = [makeSelf(), makeUser()]
    const wrapper = await mountView()

    const self = wrapper.find(`[data-test="row-${SELF_SUB}"]`)
    expect(self.find('[data-test="self-chip"]').exists()).toBe(true)
    expect(wrapper.find(`[data-test="admin-${SELF_SUB}"]`).attributes('disabled')).toBeDefined()
    expect(wrapper.find(`[data-test="enabled-${SELF_SUB}"]`).attributes('disabled')).toBeDefined()
    expect(wrapper.find(`[data-test="delete-${SELF_SUB}"]`).attributes('disabled')).toBeDefined()
    // パスワードリセットは自分にも行える（自分宛にコードが届くだけ）
    expect(wrapper.find(`[data-test="reset-${SELF_SUB}"]`).attributes('disabled')).toBeUndefined()

    expect(wrapper.find(`[data-test="admin-${OTHER_SUB}"]`).attributes('disabled')).toBeUndefined()
    expect(wrapper.find(`[data-test="delete-${OTHER_SUB}"]`).attributes('disabled')).toBeUndefined()
  })

  // 自分へ admin を戻せると、剥奪された本人がトークンの残り時間で権限を復元できてしまう
  it.each([true, false])('自分の行の admin ボタンは is_admin=%s でも押せない', async (isAdmin) => {
    holder.users.users = [makeSelf({ is_admin: isAdmin })]
    const wrapper = await mountView()

    expect(wrapper.find(`[data-test="admin-${SELF_SUB}"]`).attributes('disabled')).toBeDefined()
  })

  it('確認ダイアログで実行するまで admin を付与しない', async () => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    await wrapper.find(`[data-test="admin-${OTHER_SUB}"]`).trigger('click')
    await flushPromises()

    expect(holder.users.setAdmin).not.toHaveBeenCalled()
    expect(dialogText()).toContain('admin を付与しますか?')

    const ok = document.querySelector('[data-test="confirm-ok"]') as HTMLElement
    ok.click()
    await flushPromises()

    expect(holder.users.setAdmin).toHaveBeenCalledWith(OTHER_SUB, true)
    expect(useUiStore().snackbar).toMatchObject({ visible: true, color: 'success' })
  })

  it('キャンセルすると何もしない', async () => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    await wrapper.find(`[data-test="delete-${OTHER_SUB}"]`).trigger('click')
    await flushPromises()
    const cancel = document.querySelector('[data-test="confirm-cancel"]') as HTMLElement
    cancel.click()
    await flushPromises()

    expect(holder.users.remove).not.toHaveBeenCalled()
    expect(useUiStore().snackbar.visible).toBe(false)
  })

  it('削除は取り消せないことを明記する', async () => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    await wrapper.find(`[data-test="delete-${OTHER_SUB}"]`).trigger('click')
    await flushPromises()

    expect(dialogText()).toContain('この操作は取り消せません')
  })

  // 無効化・削除しても発行済みトークンは最大 60 分生きる（この API だけは即時に拒否する）
  it.each([['enabled'], ['delete']])('%s の確認ダイアログにトークン寿命の注意を出す', async (testId) => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    await wrapper.find(`[data-test="${testId}-${OTHER_SUB}"]`).trigger('click')
    await flushPromises()

    expect(dialogText()).toContain('最大 60 分残ります')
  })

  it('有効化の確認ダイアログにはトークン寿命の注意を出さない', async () => {
    holder.users.users = [makeUser({ enabled: false })]
    const wrapper = await mountView()

    await wrapper.find(`[data-test="enabled-${OTHER_SUB}"]`).trigger('click')
    await flushPromises()

    expect(dialogText()).not.toContain('最大 60 分残ります')
  })

  it.each([
    ['enabled', () => holder.users.setEnabled, [OTHER_SUB, false]],
    ['reset', () => holder.users.resetPassword, [OTHER_SUB]],
    ['delete', () => holder.users.remove, [OTHER_SUB]],
  ])('%s の操作は確認後にストアを呼ぶ', async (testId, endpoint, args) => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    await clickAndConfirm(wrapper, `${testId}-${OTHER_SUB}`)

    expect(endpoint()).toHaveBeenCalledWith(...args)
  })

  it('失敗するとストアのメッセージをスナックバーに出す', async () => {
    holder.users.users = [makeUser()]
    holder.users.remove.mockImplementation(async () => {
      holder.users.error = '最後の管理者から admin を外すことはできません。'
      return false
    })
    const wrapper = await mountView()

    await clickAndConfirm(wrapper, `delete-${OTHER_SUB}`)

    expect(useUiStore().snackbar).toMatchObject({
      visible: true,
      color: 'error',
      text: '最後の管理者から admin を外すことはできません。',
    })
  })

  it('取得に失敗したらエラーを表示する', async () => {
    holder.users.error = 'ユーザー一覧の取得に失敗しました'
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="error"]').text()).toContain('ユーザー一覧の取得に失敗しました')
  })

  it('0 件のときは案内を出す', async () => {
    const wrapper = await mountView()

    expect(wrapper.find('[data-test="empty"]').exists()).toBe(true)
  })

  it('パスワードリセットの扱いを注記する', async () => {
    holder.users.users = [makeUser()]
    const wrapper = await mountView()

    const note = wrapper.find('[data-test="table-note"]').text()
    expect(note).toContain('リセットコード')
    expect(note).toContain('CloudWatch Logs')
  })

  it('更新ボタンで取り直す', async () => {
    const wrapper = await mountView()
    holder.users.fetch.mockClear()

    await wrapper.find('[data-test="refresh"]').trigger('click')

    expect(holder.users.fetch).toHaveBeenCalled()
  })
})
