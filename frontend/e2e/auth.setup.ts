import { expect, test as setup } from '@playwright/test'
import { signInAs } from './fixtures/auth.fixture'
import { E2E_EMAIL } from './stubs/auth'

export const STORAGE_STATE = 'e2e/.auth/user.json'

const email = process.env.E2E_EMAIL
const password = process.env.E2E_PASSWORD

/**
 * 認証済みの `storageState` を 1 回だけ作る（設計 15）。
 *
 * `E2E_EMAIL` / `E2E_PASSWORD` があれば dev の Cognito へ実ログインする（E2E ユーザーは
 * TOTP 未登録。設計 6.5）。無ければ Cognito を叩かず Amplify 形式のトークンを注入する。
 */
setup('認証済みの storageState を作る', async ({ page, context }) => {
  if (email && password) {
    await page.goto('/login')
    await page.getByLabel('メールアドレス').fill(email)
    await page.getByLabel('パスワード').fill(password)
    await page.getByRole('button', { name: 'サインイン' }).click()
  } else {
    await signInAs(page, 'user')
    await page.goto('/')
  }

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByText(email ?? E2E_EMAIL.user)).toBeVisible()

  await context.storageState({ path: STORAGE_STATE })
})
