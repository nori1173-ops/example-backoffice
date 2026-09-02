import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Amplify } from 'aws-amplify'
import { configureAmplify } from './amplify'

vi.mock('aws-amplify', () => ({ Amplify: { configure: vi.fn() } }))

const USER_POOL_ID = 'ap-northeast-1_ABCdef123'
const CLIENT_ID = '1a2b3c4d5e6f7g8h9i0jkl'

describe('configureAmplify', () => {
  beforeEach(() => {
    vi.mocked(Amplify.configure).mockClear()
    vi.stubEnv('VITE_COGNITO_USER_POOL_ID', USER_POOL_ID)
    vi.stubEnv('VITE_COGNITO_CLIENT_ID', CLIENT_ID)
  })

  afterEach(() => {
    vi.unstubAllEnvs()
    vi.restoreAllMocks()
  })

  it('Amplify.configure に userPoolId と userPoolClientId が渡る', () => {
    configureAmplify()

    expect(Amplify.configure).toHaveBeenCalledTimes(1)
    expect(Amplify.configure).toHaveBeenCalledWith({
      Auth: {
        Cognito: expect.objectContaining({
          userPoolId: USER_POOL_ID,
          userPoolClientId: CLIENT_ID,
        }),
      },
    })
  })

  it('loginWith.email が true', () => {
    configureAmplify()

    expect(Amplify.configure).toHaveBeenCalledWith(
      expect.objectContaining({
        Auth: { Cognito: expect.objectContaining({ loginWith: { email: true } }) },
      }),
    )
  })

  it("signUpVerificationMethod が 'code'", () => {
    configureAmplify()

    expect(Amplify.configure).toHaveBeenCalledWith(
      expect.objectContaining({
        Auth: { Cognito: expect.objectContaining({ signUpVerificationMethod: 'code' }) },
      }),
    )
  })

  it('環境変数が無いとき configure を呼ばずエラーログを出す', () => {
    vi.stubEnv('VITE_COGNITO_USER_POOL_ID', undefined)
    vi.stubEnv('VITE_COGNITO_CLIENT_ID', undefined)
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => {})

    configureAmplify()

    expect(Amplify.configure).not.toHaveBeenCalled()
    expect(consoleError).toHaveBeenCalledTimes(1)
  })
})
