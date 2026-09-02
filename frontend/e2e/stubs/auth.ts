import type { Page } from '@playwright/test'

/** dev の Cognito グループ（設計 6.3）。`user` は admin に属さないログインユーザー。 */
export type E2ERole = 'user' | 'admin'

export const E2E_EMAIL: Record<E2ERole, string> = {
  user: 'e2e-user@example.co.jp',
  admin: 'e2e-admin@example.co.jp',
}

const TOKEN_PREFIX = 'CognitoIdentityServiceProvider'
const ISSUER = 'https://cognito-idp.ap-northeast-1.amazonaws.com/e2e'
const TOKEN_TTL_SECONDS = 60 * 60

interface SeedAuthOptions {
  clientId: string
  email: string
  role: E2ERole
}

function base64UrlEncode(value: unknown): string {
  return btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value))))
    .replaceAll('+', '-')
    .replaceAll('/', '_')
    .replace(/=+$/, '')
}

function createDummyJwt(payload: Record<string, unknown>): string {
  return [base64UrlEncode({ alg: 'none', typ: 'JWT' }), base64UrlEncode(payload), 'e2e-signature'].join(
    '.',
  )
}

function groupsOf(role: E2ERole): string[] {
  return role === 'admin' ? ['admin'] : []
}

function subOf(role: E2ERole): string {
  return `e2e-${role}-sub`
}

/** トークンリフレッシュのスタブが返す新しいトークン（発行時刻は読み込み時点）。 */
function freshTokens(clientId: string, role: E2ERole) {
  const now = Math.floor(Date.now() / 1000)
  const exp = now + TOKEN_TTL_SECONDS
  const email = E2E_EMAIL[role]
  const groups = groupsOf(role)
  const sub = subOf(role)
  return {
    IdToken: createDummyJwt({
      aud: clientId,
      auth_time: now,
      'cognito:groups': groups,
      'cognito:username': email,
      email,
      exp,
      iat: now,
      iss: ISSUER,
      sub,
      token_use: 'id',
    }),
    AccessToken: createDummyJwt({
      auth_time: now,
      client_id: clientId,
      'cognito:groups': groups,
      'cognito:username': email,
      exp,
      iat: now,
      iss: ISSUER,
      jti: `e2e-${role}-access-jti`,
      scope: 'aws.cognito.signin.user.admin',
      sub,
      token_use: 'access',
      username: email,
    }),
  }
}

/**
 * Cognito を叩かずに Amplify v6 形式のトークンを localStorage へ注入する。
 * キー形式 `CognitoIdentityServiceProvider.{clientId}.{email}.{idToken|accessToken|...}` +
 * `...LastAuthUser`。`addInitScript` なのでページ遷移のたびに再適用される。
 */
export async function seedAmplifyAuth(
  page: Page,
  { clientId, email, role }: SeedAuthOptions,
): Promise<void> {
  await page.addInitScript(
    ({ tokenPrefix, clientId, email, groups, issuer, ttl, sub }) => {
      const encode = (value: unknown): string =>
        btoa(String.fromCharCode(...new TextEncoder().encode(JSON.stringify(value))))
          .replaceAll('+', '-')
          .replaceAll('/', '_')
          .replace(/=+$/, '')
      const jwt = (payload: Record<string, unknown>): string =>
        [encode({ alg: 'none', typ: 'JWT' }), encode(payload), 'e2e-signature'].join('.')

      const now = Math.floor(Date.now() / 1000)
      const exp = now + ttl
      const keyPrefix = `${tokenPrefix}.${clientId}.${email}`

      localStorage.setItem(`${tokenPrefix}.${clientId}.LastAuthUser`, email)
      localStorage.setItem(
        `${keyPrefix}.accessToken`,
        jwt({
          auth_time: now,
          client_id: clientId,
          'cognito:groups': groups,
          'cognito:username': email,
          exp,
          iat: now,
          iss: issuer,
          scope: 'aws.cognito.signin.user.admin',
          sub,
          token_use: 'access',
          username: email,
        }),
      )
      localStorage.setItem(
        `${keyPrefix}.idToken`,
        jwt({
          aud: clientId,
          auth_time: now,
          'cognito:groups': groups,
          'cognito:username': email,
          email,
          exp,
          iat: now,
          iss: issuer,
          sub,
          token_use: 'id',
        }),
      )
      localStorage.setItem(`${keyPrefix}.refreshToken`, `e2e-refresh-${email}`)
      localStorage.setItem(`${keyPrefix}.clockDrift`, '0')
      localStorage.setItem(
        `${keyPrefix}.signInDetails`,
        JSON.stringify({ authFlowType: 'USER_SRP_AUTH', loginId: email }),
      )
    },
    {
      tokenPrefix: TOKEN_PREFIX,
      clientId,
      email,
      groups: groupsOf(role),
      issuer: ISSUER,
      ttl: TOKEN_TTL_SECONDS,
      sub: subOf(role),
    },
  )
}

/**
 * Cognito のエンドポイントを塞ぐ。Amplify v6 のリフレッシュは
 * `GetTokensFromRefreshToken`（`X-Amz-Target` で識別）。旧 `InitiateAuth` も受理する。
 */
export async function stubCognitoEndpoints(page: Page, role: E2ERole, clientId: string): Promise<void> {
  await page.route('**/cognito-idp.*.amazonaws.com/**', async (route) => {
    const request = route.request()
    const target = request.headers()['x-amz-target'] ?? ''
    let authFlow: string | undefined
    try {
      authFlow = (request.postDataJSON() as { AuthFlow?: string }).AuthFlow
    } catch {
      // Cognito 以外の空リクエストは空レスポンスにする
    }
    const isRefresh =
      target.includes('GetTokensFromRefreshToken') || authFlow === 'REFRESH_TOKEN_AUTH'
    await route.fulfill({
      status: 200,
      contentType: 'application/x-amz-json-1.1',
      body: JSON.stringify(
        isRefresh
          ? {
              AuthenticationResult: {
                ...freshTokens(clientId, role),
                ExpiresIn: TOKEN_TTL_SECONDS,
                TokenType: 'Bearer',
              },
            }
          : {},
      ),
    })
  })
}
