import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import {
  confirmResetPassword,
  confirmSignIn,
  fetchAuthSession,
  getCurrentUser,
  resendSignUpCode,
  resetPassword,
  signIn,
  signUp,
  type SignInOutput,
} from 'aws-amplify/auth'
import { Hub } from 'aws-amplify/utils'
import { useAuthStore } from './auth'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

const EMAIL = 'user@example.co.jp'
const SETUP_URI = 'otpauth://totp/example-backoffice:user@example.co.jp?secret=JBSWY3DPEHPK3PXP'
const SHARED_SECRET = 'JBSWY3DPEHPK3PXP'

function outputOf(nextStep: unknown, isSignedIn = false): SignInOutput {
  return { isSignedIn, nextStep } as unknown as SignInOutput
}

function totpSetupStep() {
  return {
    signInStep: 'CONTINUE_SIGN_IN_WITH_TOTP_SETUP',
    totpSetupDetails: {
      sharedSecret: SHARED_SECRET,
      getSetupUri: vi.fn(() => new URL(SETUP_URI)),
    },
  }
}

function mockSession(groups?: unknown): void {
  vi.mocked(getCurrentUser).mockResolvedValue({
    username: EMAIL,
    userId: 'sub-1234',
    signInDetails: { loginId: EMAIL },
  } as unknown as Awaited<ReturnType<typeof getCurrentUser>>)
  const payload: Record<string, unknown> = {}
  if (groups !== undefined) {
    payload['cognito:groups'] = groups
  }
  vi.mocked(fetchAuthSession).mockResolvedValue({
    tokens: { idToken: { payload } },
  } as unknown as Awaited<ReturnType<typeof fetchAuthSession>>)
}

describe('useAuthStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
  })

  it('DONE のとき pending が null になり checkAuth が呼ばれる', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession(['admin'])
    const store = useAuthStore()

    await store.login(EMAIL, 'Passw0rd!')

    expect(store.pending).toBeNull()
    expect(getCurrentUser).toHaveBeenCalledTimes(1)
    expect(store.email).toBe(EMAIL)
    expect(store.isAuthenticated).toBe(true)
    expect(store.isLoading).toBe(false)
  })

  it("CONFIRM_SIGN_IN_WITH_TOTP_CODE で pending.kind が 'totp-code'", async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }),
    )
    const store = useAuthStore()

    await store.login(EMAIL, 'Passw0rd!')

    expect(store.pending).toEqual({ kind: 'totp-code' })
    expect(getCurrentUser).not.toHaveBeenCalled()
  })

  it('CONTINUE_SIGN_IN_WITH_TOTP_SETUP で setupUri と sharedSecret が入る', async () => {
    const step = totpSetupStep()
    vi.mocked(signIn).mockResolvedValue(outputOf(step))
    const store = useAuthStore()

    await store.login(EMAIL, 'Passw0rd!')

    expect(step.totpSetupDetails.getSetupUri).toHaveBeenCalledWith('example-backoffice', EMAIL)
    expect(store.pending).toEqual({
      kind: 'totp-setup',
      setupUri: SETUP_URI,
      sharedSecret: SHARED_SECRET,
    })
  })

  it("CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED で pending.kind が 'new-password'", async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED' }),
    )
    const store = useAuthStore()

    await store.login(EMAIL, 'Passw0rd!')

    expect(store.pending).toEqual({ kind: 'new-password' })
  })

  it("CONFIRM_SIGN_UP で pending.kind が 'confirm-signup' と username", async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'CONFIRM_SIGN_UP' }))
    const store = useAuthStore()

    await store.login(EMAIL, 'Passw0rd!')

    expect(store.pending).toEqual({ kind: 'confirm-signup', username: EMAIL })
  })

  it('CONTINUE_SIGN_IN_WITH_MFA_SELECTION で TOTP を自動選択して再分岐する', async () => {
    vi.mocked(signIn).mockResolvedValue(
      outputOf({ signInStep: 'CONTINUE_SIGN_IN_WITH_MFA_SELECTION' }),
    )
    vi.mocked(confirmSignIn).mockResolvedValue(
      outputOf({ signInStep: 'CONFIRM_SIGN_IN_WITH_TOTP_CODE' }),
    )
    const store = useAuthStore()

    await store.login(EMAIL, 'Passw0rd!')

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: 'TOTP' })
    expect(store.pending).toEqual({ kind: 'totp-code' })
  })

  it('未対応の signInStep は例外', async () => {
    vi.mocked(signIn).mockResolvedValue(outputOf({ signInStep: 'RESET_PASSWORD' }))
    const store = useAuthStore()

    await expect(store.login(EMAIL, 'Passw0rd!')).rejects.toThrow(
      '未対応のサインインステップ: RESET_PASSWORD',
    )
    expect(store.isLoading).toBe(false)
  })

  it('submitChallenge が confirmSignIn を呼び結果を再分岐する', async () => {
    vi.mocked(confirmSignIn).mockResolvedValue(outputOf({ signInStep: 'DONE' }, true))
    mockSession([])
    const store = useAuthStore()

    await store.submitChallenge('123456', EMAIL)

    expect(confirmSignIn).toHaveBeenCalledWith({ challengeResponse: '123456' })
    expect(store.pending).toBeNull()
    expect(store.email).toBe(EMAIL)
  })

  it('checkAuth が cognito:groups（配列）から groups を取る', async () => {
    mockSession(['admin', 'ops'])
    const store = useAuthStore()

    await store.checkAuth()

    expect(store.groups).toEqual(['admin', 'ops'])
    expect(store.initialized).toBe(true)
  })

  it('checkAuth が cognito:groups（文字列 "[admin]"）でも配列にする', async () => {
    mockSession('[admin]')
    const store = useAuthStore()

    await store.checkAuth()

    expect(store.groups).toEqual(['admin'])
  })

  it('isAdmin が admin グループの有無で切り替わる', async () => {
    mockSession(['ops'])
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.isAdmin).toBe(false)

    mockSession(['ops', 'admin'])
    await store.checkAuth()
    expect(store.isAdmin).toBe(true)
  })

  it('NotAuthorizedException が日本語メッセージになる', async () => {
    const error = new Error('Incorrect username or password.')
    error.name = 'NotAuthorizedException'
    vi.mocked(signIn).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.login(EMAIL, 'wrong')).rejects.toThrow(error)

    expect(store.error).toBe('メールアドレスまたはパスワードが正しくありません')
    expect(store.isLoading).toBe(false)
  })

  it('register の UsernameExistsException が日本語メッセージになる', async () => {
    const error = new Error('An account with the given email already exists.')
    error.name = 'UsernameExistsException'
    vi.mocked(signUp).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.register(EMAIL, 'Passw0rd!2345')).rejects.toThrow(error)

    expect(store.error).toBe('このメールアドレスは既に登録されています')
    expect(store.pending).toBeNull()
    expect(store.isLoading).toBe(false)
  })

  it('checkAuth 失敗で isAuthenticated が false かつ initialized が true', async () => {
    vi.mocked(getCurrentUser).mockRejectedValue(new Error('not signed in'))
    const store = useAuthStore()
    store.email = EMAIL
    store.groups = ['admin']

    await store.checkAuth()

    expect(store.isAuthenticated).toBe(false)
    expect(store.groups).toEqual([])
    expect(store.initialized).toBe(true)
  })

  it('tokenRefresh_failure でストアがクリアされる', async () => {
    mockSession(['admin'])
    const store = useAuthStore()
    await store.checkAuth()
    expect(store.isAuthenticated).toBe(true)

    const [channel, listener] = vi.mocked(Hub.listen).mock.calls[0]
    expect(channel).toBe('auth')
    listener({ payload: { event: 'tokenRefresh_failure' } } as never)

    expect(store.isAuthenticated).toBe(false)
    expect(store.groups).toEqual([])
    expect(store.pending).toBeNull()
  })

  it('requestPasswordReset が resetPassword を呼ぶ', async () => {
    vi.mocked(resetPassword).mockResolvedValue({
      isPasswordReset: false,
      nextStep: { resetPasswordStep: 'CONFIRM_RESET_PASSWORD_WITH_CODE' },
    } as unknown as Awaited<ReturnType<typeof resetPassword>>)
    const store = useAuthStore()

    await store.requestPasswordReset(EMAIL)

    expect(resetPassword).toHaveBeenCalledWith({ username: EMAIL })
    expect(store.error).toBeNull()
    expect(store.isLoading).toBe(false)
  })

  it('submitPasswordReset が confirmResetPassword を呼ぶ', async () => {
    const store = useAuthStore()

    await store.submitPasswordReset(EMAIL, '123456', 'NewPassw0rd')

    expect(confirmResetPassword).toHaveBeenCalledWith({
      username: EMAIL,
      confirmationCode: '123456',
      newPassword: 'NewPassw0rd',
    })
    expect(store.isLoading).toBe(false)
  })

  it('resendCode が resendSignUpCode を呼ぶ', async () => {
    const store = useAuthStore()

    await store.resendCode(EMAIL)

    expect(resendSignUpCode).toHaveBeenCalledWith({ username: EMAIL })
    expect(store.isLoading).toBe(false)
  })

  it('CodeMismatchException が日本語メッセージになる', async () => {
    const error = new Error('Invalid verification code provided.')
    error.name = 'CodeMismatchException'
    vi.mocked(confirmResetPassword).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.submitPasswordReset(EMAIL, '000000', 'NewPassw0rd')).rejects.toThrow(error)

    expect(store.error).toBe('確認コードが正しくありません')
  })

  it('LimitExceededException が日本語メッセージになる', async () => {
    const error = new Error('Attempt limit exceeded, please try after some time.')
    error.name = 'LimitExceededException'
    vi.mocked(resendSignUpCode).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.resendCode(EMAIL)).rejects.toThrow(error)

    expect(store.error).toBe('試行回数の上限に達しました。しばらく時間をおいてから再試行してください')
  })

  it('UserLambdaValidationException は社内メールアドレスの案内になる', async () => {
    const error = new Error('PreSignUp failed with error 社外ドメイン.')
    error.name = 'UserLambdaValidationException'
    vi.mocked(signUp).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.register('outside@example.com', 'Passw0rd!2345')).rejects.toThrow(error)

    expect(store.error).toContain('@example.co.jp')
  })

  it('辞書に無い例外は汎用文言に例外名を添える', async () => {
    const error = new Error('Something went wrong')
    error.name = 'UnknownCognitoException'
    vi.mocked(signIn).mockRejectedValue(error)
    const store = useAuthStore()

    await expect(store.login(EMAIL, 'Passw0rd!')).rejects.toThrow(error)

    expect(store.error).toBe('予期しないエラーが発生しました（UnknownCognitoException）')
  })
})
