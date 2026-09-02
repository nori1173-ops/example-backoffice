import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  confirmResetPassword,
  confirmSignIn,
  confirmSignUp,
  fetchAuthSession,
  getCurrentUser,
  resendSignUpCode,
  resetPassword,
  signIn,
  signOut,
  signUp,
  type SignInOutput,
} from 'aws-amplify/auth'
import { Hub } from 'aws-amplify/utils'
import { PASSWORD_POLICY_MESSAGE } from '@/utils/password'

const TOTP_ISSUER = 'example-backoffice'

const GENERIC_ERROR = '予期しないエラーが発生しました'

const AMPLIFY_ERROR_MESSAGES: Record<string, string> = {
  NotAuthorizedException: 'メールアドレスまたはパスワードが正しくありません',
  UserNotFoundException: 'メールアドレスまたはパスワードが正しくありません',
  UserNotConfirmedException: 'アカウントが確認されていません。確認コードを入力してください',
  PasswordResetRequiredException:
    'パスワードの再設定が必要です。「パスワードをお忘れの方」から手続きしてください',
  TooManyRequestsException: 'アクセスが集中しています。しばらく時間をおいてから再試行してください',
  TooManyFailedAttemptsException:
    '失敗が続いたため一時的に制限されています。しばらく時間をおいてから再試行してください',
  LimitExceededException: '試行回数の上限に達しました。しばらく時間をおいてから再試行してください',
  CodeMismatchException: '確認コードが正しくありません',
  ExpiredCodeException: '確認コードの有効期限が切れています。コードを再送してください',
  CodeDeliveryFailureException: '確認コードを送信できませんでした。メールアドレスを確認してください',
  EnableSoftwareTokenMFAException: '認証コードが正しくありません。認証アプリの表示を確認してください',
  SoftwareTokenMFANotFoundException:
    '認証アプリが登録されていません。サインインし直して登録してください',
  InvalidPasswordException: PASSWORD_POLICY_MESSAGE,
  InvalidParameterException: '入力内容が正しくありません',
  UsernameExistsException: 'このメールアドレスは既に登録されています',
  UserLambdaValidationException: '社内メールアドレス（@example.co.jp）でのみ登録できます',
  UserAlreadyAuthenticatedException:
    '既にサインインしています。サインアウトしてからやり直してください',
  NetworkError: 'ネットワークに接続できませんでした。通信環境を確認してください',
}

export type Pending =
  | { kind: 'totp-setup'; setupUri: string; sharedSecret: string }
  | { kind: 'totp-code' }
  | { kind: 'new-password' }
  | { kind: 'confirm-signup'; username: string }
  | null

/** 辞書に無い Cognito 例外は、原因追跡のために例外名だけ控えめに添える */
function toJaMessage(error: unknown): string {
  if (!(error instanceof Error)) {
    return GENERIC_ERROR
  }
  const known = AMPLIFY_ERROR_MESSAGES[error.name]
  if (known) {
    return known
  }
  if (!error.name || error.name === 'Error') {
    return error.message || GENERIC_ERROR
  }
  return `${GENERIC_ERROR}（${error.name}）`
}

/** ID トークンの `cognito:groups` は配列でも `"[admin, ops]"` 形式の文字列でも来る */
function toGroups(raw: unknown): string[] {
  if (Array.isArray(raw)) {
    return raw.filter((group): group is string => typeof group === 'string')
  }
  if (typeof raw === 'string') {
    return raw
      .replace(/^\[/, '')
      .replace(/\]$/, '')
      .split(/[\s,]+/)
      .filter((group) => group.length > 0)
  }
  return []
}

export const useAuthStore = defineStore('auth', () => {
  const email = ref<string | null>(null)
  const groups = ref<string[]>([])
  const initialized = ref(false)
  const isLoading = ref(false)
  const error = ref<string | null>(null)
  const pending = ref<Pending>(null)

  const isAuthenticated = computed(() => email.value !== null)
  const isAdmin = computed(() => groups.value.includes('admin'))

  function clear(): void {
    email.value = null
    groups.value = []
    pending.value = null
  }

  async function checkAuth(): Promise<void> {
    try {
      const user = await getCurrentUser()
      const session = await fetchAuthSession()
      email.value = user.signInDetails?.loginId ?? user.username
      groups.value = toGroups(session.tokens?.idToken?.payload['cognito:groups'])
    } catch {
      clear()
    } finally {
      initialized.value = true
    }
  }

  async function route(result: SignInOutput, username: string): Promise<void> {
    const step = result.nextStep
    switch (step.signInStep) {
      case 'DONE':
        pending.value = null
        await checkAuth()
        return
      case 'CONTINUE_SIGN_IN_WITH_TOTP_SETUP':
        pending.value = {
          kind: 'totp-setup',
          setupUri: step.totpSetupDetails.getSetupUri(TOTP_ISSUER, username).toString(),
          sharedSecret: step.totpSetupDetails.sharedSecret,
        }
        return
      case 'CONFIRM_SIGN_IN_WITH_TOTP_CODE':
        pending.value = { kind: 'totp-code' }
        return
      case 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED':
        pending.value = { kind: 'new-password' }
        return
      case 'CONFIRM_SIGN_UP':
        pending.value = { kind: 'confirm-signup', username }
        return
      case 'CONTINUE_SIGN_IN_WITH_MFA_SELECTION': {
        const next = await confirmSignIn({ challengeResponse: 'TOTP' })
        await route(next, username)
        return
      }
      default:
        throw new Error(`未対応のサインインステップ: ${step.signInStep}`)
    }
  }

  async function login(username: string, password: string): Promise<void> {
    isLoading.value = true
    error.value = null
    pending.value = null
    try {
      const result = await signIn({ username, password })
      await route(result, username)
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function submitChallenge(response: string, username: string): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const result = await confirmSignIn({ challengeResponse: response })
      await route(result, username)
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function register(username: string, password: string): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      const result = await signUp({
        username,
        password,
        options: { userAttributes: { email: username } },
      })
      pending.value = result.isSignUpComplete ? null : { kind: 'confirm-signup', username }
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function confirmRegistration(username: string, code: string): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      await confirmSignUp({ username, confirmationCode: code })
      pending.value = null
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function resendCode(username: string): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      await resendSignUpCode({ username })
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function requestPasswordReset(username: string): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      await resetPassword({ username })
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function submitPasswordReset(
    username: string,
    code: string,
    newPassword: string,
  ): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      await confirmResetPassword({ username, confirmationCode: code, newPassword })
    } catch (e: unknown) {
      error.value = toJaMessage(e)
      throw e
    } finally {
      isLoading.value = false
    }
  }

  async function logout(): Promise<void> {
    isLoading.value = true
    error.value = null
    try {
      await signOut()
    } finally {
      clear()
      isLoading.value = false
    }
  }

  Hub.listen('auth', ({ payload }) => {
    if (payload.event === 'tokenRefresh_failure') {
      clear()
    }
  })

  return {
    email,
    groups,
    initialized,
    isLoading,
    error,
    pending,
    isAuthenticated,
    isAdmin,
    checkAuth,
    login,
    submitChallenge,
    register,
    confirmRegistration,
    resendCode,
    requestPasswordReset,
    submitPasswordReset,
    logout,
  }
})
