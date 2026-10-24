/** Cognito の 1 ユーザー（`GET /api/admin-users/users` の 1 件）。 */
export interface AdminUser {
  /** Cognito の `sub`。操作の宛先はメールではなくこれ */
  sub: string
  email: string
  /** Cognito の `UserStatus`（CONFIRMED / UNCONFIRMED / FORCE_CHANGE_PASSWORD / RESET_REQUIRED …） */
  status: string
  enabled: boolean
  is_admin: boolean
  /** UTC の ISO 8601。画面では JST に直して表示する */
  created_at: string | null
  updated_at: string | null
}

export interface UsersResponse {
  users: AdminUser[]
  /** 呼び出し元自身の `sub`。自分の行の操作を止めるのに使う */
  self_sub: string
}

export interface AdminUpdated {
  sub: string
  is_admin: boolean
}

export interface EnabledUpdated {
  sub: string
  enabled: boolean
}

export interface PasswordResetSent {
  sub: string
  email: string
}
