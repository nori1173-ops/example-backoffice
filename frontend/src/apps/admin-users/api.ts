import { createAppApi } from '@/services/http'
import type { ApiEnvelope } from '@/types'
import type { AdminUpdated, EnabledUpdated, PasswordResetSent, UsersResponse } from './types'

// baseURL は /api/admin-users。Bearer 付与と 401 の後始末は createAppApi が行う（設計 9.7）。
const api = createAppApi('admin-users')

function path(sub: string, suffix = ''): string {
  return `/users/${encodeURIComponent(sub)}${suffix}`
}

export const adminUsersApi = {
  async listUsers(): Promise<UsersResponse | null> {
    const response = await api.get<ApiEnvelope<UsersResponse>>('/users')
    return response.data.data
  },

  async setAdmin(sub: string, admin: boolean): Promise<AdminUpdated | null> {
    const response = await api.put<ApiEnvelope<AdminUpdated>>(path(sub, '/admin'), { admin })
    return response.data.data
  },

  async setEnabled(sub: string, enabled: boolean): Promise<EnabledUpdated | null> {
    const response = await api.put<ApiEnvelope<EnabledUpdated>>(path(sub, '/enabled'), { enabled })
    return response.data.data
  },

  /** 本人にリセットコードがメールされる。新しいパスワードは本人が設定する。 */
  async resetPassword(sub: string): Promise<PasswordResetSent | null> {
    const response = await api.post<ApiEnvelope<PasswordResetSent>>(path(sub, '/password-reset'))
    return response.data.data
  },

  /** 成功時は 204（本文なし）。 */
  async removeUser(sub: string): Promise<void> {
    await api.delete(path(sub))
  },
}
