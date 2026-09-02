import { defineConfig, devices } from '@playwright/test'

// E2E は dev の CloudFront（社内 IP 許可リスト内から）に対して実行する。
// ローカルの vite dev サーバーへ向ける場合は E2E_BASE_URL=http://127.0.0.1:5173 を指定する。
//
// 環境変数:
//   E2E_BASE_URL             既定 https://backoffice-dev.example-cloud.com
//   E2E_COGNITO_CLIENT_ID    必須（VITE_COGNITO_CLIENT_ID でも可）。スタブ認証が使う Cognito App Client ID。
//                            /example-backoffice/dev/cognito/client-id の値。未設定なら auth.setup が明示的に失敗する
//   E2E_EMAIL / E2E_PASSWORD 任意。両方あれば dev の Cognito へ実ログインして storageState を作る
const baseURL = process.env.E2E_BASE_URL ?? 'https://backoffice-dev.example-cloud.com'

// e2e/auth.setup.ts が書き出す storageState。パスは setup 側の STORAGE_STATE と揃える。
const storageState = 'e2e/.auth/user.json'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: [['html', { open: 'never' }], ['list']],
  use: {
    baseURL,
    locale: 'ja-JP',
    timezoneId: 'Asia/Tokyo',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'setup',
      testMatch: /auth\.setup\.ts/,
    },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], storageState },
      dependencies: ['setup'],
    },
  ],
})
