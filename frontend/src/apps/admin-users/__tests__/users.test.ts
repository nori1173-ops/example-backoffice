import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { adminUsersApi } from '../api'
import { useUsersStore } from '../stores/users'
import type { AdminUser } from '../types'

vi.mock('../api', () => ({
  adminUsersApi: {
    listUsers: vi.fn(),
    setAdmin: vi.fn(),
    setEnabled: vi.fn(),
    resetPassword: vi.fn(),
    removeUser: vi.fn(),
  },
}))

const SELF_SUB = '00000000-0000-0000-0000-000000000001'
const OTHER_SUB = '00000000-0000-0000-0000-000000000002'

function makeUser(overrides: Partial<AdminUser> = {}): AdminUser {
  return {
    sub: OTHER_SUB,
    email: 'target@example.co.jp',
    status: 'CONFIRMED',
    enabled: true,
    is_admin: false,
    created_at: '2026-08-01T01:00:00+00:00',
    updated_at: '2026-08-02T02:00:00+00:00',
    ...overrides,
  }
}

function apiError(code: string, message: string) {
  return { response: { status: 409, data: { ok: false, data: null, error: { code, message }, meta: null } } }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.mocked(adminUsersApi.listUsers).mockResolvedValue({ users: [makeUser()], self_sub: SELF_SUB })
  vi.mocked(adminUsersApi.setAdmin).mockResolvedValue({ sub: OTHER_SUB, is_admin: true })
  vi.mocked(adminUsersApi.setEnabled).mockResolvedValue({ sub: OTHER_SUB, enabled: false })
  vi.mocked(adminUsersApi.resetPassword).mockResolvedValue({ sub: OTHER_SUB, email: 'target@example.co.jp' })
  vi.mocked(adminUsersApi.removeUser).mockResolvedValue(undefined)
})

describe('useUsersStore', () => {
  it('fetch が一覧と自分の sub を保持する', async () => {
    const store = useUsersStore()

    await store.fetch()

    expect(store.users).toHaveLength(1)
    expect(store.selfSub).toBe(SELF_SUB)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('fetch の失敗はエラーメッセージになり一覧は空になる', async () => {
    vi.mocked(adminUsersApi.listUsers).mockRejectedValue(new Error('boom'))
    const store = useUsersStore()

    await store.fetch()

    expect(store.users).toEqual([])
    expect(store.error).toBe('ユーザー一覧の取得に失敗しました')
  })

  it.each([
    ['setAdmin', () => useUsersStore().setAdmin(OTHER_SUB, true), () => adminUsersApi.setAdmin],
    ['setEnabled', () => useUsersStore().setEnabled(OTHER_SUB, false), () => adminUsersApi.setEnabled],
    ['resetPassword', () => useUsersStore().resetPassword(OTHER_SUB), () => adminUsersApi.resetPassword],
    ['remove', () => useUsersStore().remove(OTHER_SUB), () => adminUsersApi.removeUser],
  ])('%s は成功したら一覧を取り直す', async (_name, action, endpoint) => {
    const succeeded = await action()

    expect(succeeded).toBe(true)
    expect(endpoint()).toHaveBeenCalled()
    expect(adminUsersApi.listUsers).toHaveBeenCalledTimes(1)
  })

  // コードだけの本文をそのまま出すと利用者に伝わらないため辞書で置き換える
  it.each([
    ['SELF_OPERATION_FORBIDDEN', '自分自身には操作できません。'],
    ['LAST_ADMIN', '最後の管理者から admin を外すことはできません。先に別の管理者を追加してください。'],
    ['USER_NOT_FOUND', 'ユーザーが見つかりません。一覧を更新してください。'],
    [
      'PASSWORD_RESET_NOT_ALLOWED',
      'このユーザーはパスワードをリセットできない状態です（未確認・初期パスワード・無効化など）。',
    ],
    ['FORBIDDEN', 'ユーザー管理には admin 権限が必要です。'],
  ])('%s は画面向けの文言に置き換える', async (code, message) => {
    vi.mocked(adminUsersApi.setAdmin).mockRejectedValue(apiError(code, 'サーバ側の文言'))
    const store = useUsersStore()

    const succeeded = await store.setAdmin(OTHER_SUB, false)

    expect(succeeded).toBe(false)
    expect(store.error).toBe(message)
    expect(adminUsersApi.listUsers).not.toHaveBeenCalled()
  })

  it('未知のコードは API のメッセージをそのまま出す', async () => {
    vi.mocked(adminUsersApi.removeUser).mockRejectedValue(apiError('SOMETHING_NEW', 'サーバ側の日本語'))
    const store = useUsersStore()

    expect(await store.remove(OTHER_SUB)).toBe(false)
    expect(store.error).toBe('サーバ側の日本語')
  })

  it('エンベロープでないエラーは操作ごとの既定文言になる', async () => {
    vi.mocked(adminUsersApi.setEnabled).mockRejectedValue(new Error('network'))
    const store = useUsersStore()

    expect(await store.setEnabled(OTHER_SUB, false)).toBe(false)
    expect(store.error).toBe('無効化に失敗しました')
  })
})
