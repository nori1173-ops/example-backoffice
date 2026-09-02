import { Amplify } from 'aws-amplify'

export function configureAmplify(): void {
  const userPoolId = import.meta.env.VITE_COGNITO_USER_POOL_ID
  const userPoolClientId = import.meta.env.VITE_COGNITO_CLIENT_ID

  if (!userPoolId || !userPoolClientId) {
    console.error(
      'Cognito の環境変数が未設定のため Amplify を初期化しませんでした（VITE_COGNITO_USER_POOL_ID / VITE_COGNITO_CLIENT_ID）',
    )
    return
  }

  Amplify.configure({
    Auth: {
      Cognito: {
        userPoolId,
        userPoolClientId,
        loginWith: { email: true },
        signUpVerificationMethod: 'code',
        userAttributes: { email: { required: true } },
      },
    },
  })
}
