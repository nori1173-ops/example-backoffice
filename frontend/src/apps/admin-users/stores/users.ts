import { defineStore } from 'pinia'
import { ref } from 'vue'
import { extractApiError } from '@/services/http'
import { adminUsersApi } from '../api'
import type { AdminUser } from '../types'

/**
 * サーバのエラーコードを画面向けの文言に置き換える。
 * 未知のコードは API のメッセージ（日本語）をそのまま出す。
 */
const ERROR_MESSAGES: Record<string, string> = {
  SELF_OPERATION_FORBIDDEN: '自分自身には操作できません。',
  LAST_ADMIN:
    '最後の管理者から admin を外すことはできません。先に別の管理者を追加してください。',
  USER_NOT_FOUND: 'ユーザーが見つかりません。一覧を更新してください。',
  PASSWORD_RESET_NOT_ALLOWED:
    'このユーザーはパスワードをリセットできない状態です（未確認・初期パスワード・無効化など）。',
  FORBIDDEN: 'ユーザー管理には admin 権限が必要です。',
}

function messageOf(error: unknown, fallback: string): string {
  const api = extractApiError(error)
  if (!api) {
    return fallback
  }
  return ERROR_MESSAGES[api.code] ?? api.message
}

export const useUsersStore = defineStore('admin-users/users', () => {
  const users = ref<AdminUser[]>([])
  /** 呼び出し元自身の `sub`。一覧を取るまでは空 */
  const selfSub = ref('')
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetch(): Promise<void> {
    loading.value = true
    error.value = null
    try {
      const response = await adminUsersApi.listUsers()
      users.value = response?.users ?? []
      selfSub.value = response?.self_sub ?? ''
    } catch (e: unknown) {
      error.value = messageOf(e, 'ユーザー一覧の取得に失敗しました')
    } finally {
      loading.value = false
    }
  }

  /**
   * 更新系は結果の真偽だけを返し、成功なら一覧を取り直す。
   * Cognito 側の状態（`status` や `updated_at`）も併せて変わるため、
   * 手元の 1 行だけ書き換えず全体を読み直す。
   */
  async function run(action: () => Promise<unknown>, fallback: string): Promise<boolean> {
    loading.value = true
    error.value = null
    try {
      await action()
    } catch (e: unknown) {
      error.value = messageOf(e, fallback)
      return false
    } finally {
      loading.value = false
    }
    await fetch()
    return true
  }

  function setAdmin(sub: string, admin: boolean): Promise<boolean> {
    return run(
      () => adminUsersApi.setAdmin(sub, admin),
      admin ? 'admin の付与に失敗しました' : 'admin の解除に失敗しました',
    )
  }

  function setEnabled(sub: string, enabled: boolean): Promise<boolean> {
    return run(
      () => adminUsersApi.setEnabled(sub, enabled),
      enabled ? '有効化に失敗しました' : '無効化に失敗しました',
    )
  }

  function resetPassword(sub: string): Promise<boolean> {
    return run(() => adminUsersApi.resetPassword(sub), 'パスワードリセットに失敗しました')
  }

  function remove(sub: string): Promise<boolean> {
    return run(() => adminUsersApi.removeUser(sub), 'ユーザーの削除に失敗しました')
  }

  return { users, selfSub, loading, error, fetch, setAdmin, setEnabled, resetPassword, remove }
})
