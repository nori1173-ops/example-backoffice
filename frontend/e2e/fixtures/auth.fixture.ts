import { test as base, type Page } from '@playwright/test'
import { E2E_EMAIL, seedAmplifyAuth, stubCognitoEndpoints, type E2ERole } from '../stubs/auth'

/**
 * Cognito App Client ID。
 *
 * SPA の client id はビルド時に `VITE_COGNITO_CLIENT_ID` から焼き込まれる。スタブ認証は
 * その client id を含むキーで localStorage を書くため、値が違うと Amplify が別のキーを読み、
 * `getCurrentUser()` が失敗して「ログインできない」だけの分かりにくいエラーになる。
 * 既定値でごまかさず、未設定なら理由の分かる例外で止める。
 */
function requireClientId(): string {
  const clientId = process.env.E2E_COGNITO_CLIENT_ID ?? process.env.VITE_COGNITO_CLIENT_ID
  if (!clientId) {
    throw new Error(
      'E2E_COGNITO_CLIENT_ID（または VITE_COGNITO_CLIENT_ID）が未設定です。' +
        'スタブ認証は SPA に焼き込まれた Cognito App Client ID と同じ値で localStorage を書く必要があります。' +
        'aws ssm get-parameter --name /example-backoffice/dev/cognito/client-id --query Parameter.Value --output text ' +
        'の値を設定してください。',
    )
  }
  return clientId
}

export async function signInAs(page: Page, role: E2ERole): Promise<Page> {
  const clientId = requireClientId()
  await stubCognitoEndpoints(page, role, clientId)
  await seedAmplifyAuth(page, { clientId, email: E2E_EMAIL[role], role })
  return page
}

interface AuthFixtures {
  userPage: Page
  adminPage: Page
}

/** ロール別の認証済み Page。API 応答はテスト側で `page.route()` して組み立てる。 */
export const test = base.extend<AuthFixtures>({
  userPage: async ({ page }, use) => {
    await use(await signInAs(page, 'user'))
  },
  adminPage: async ({ page }, use) => {
    await use(await signInAs(page, 'admin'))
  },
})

export { expect } from '@playwright/test'
