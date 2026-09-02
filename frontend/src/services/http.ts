import axios, { type AxiosInstance } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import type { ApiError } from '@/types'

const REQUEST_TIMEOUT_MS = 30000

function statusOf(error: unknown): number | undefined {
  if (error && typeof error === 'object' && 'response' in error) {
    return (error as { response?: { status?: number } }).response?.status
  }
  return undefined
}

/** ストアの `logout()`（`signOut()` + `clear()`）。失敗してもリダイレクトは止めない。 */
async function logoutQuietly(): Promise<void> {
  try {
    const { useAuthStore } = await import('@/stores/auth')
    await useAuthStore().logout()
  } catch {
    return
  }
}

async function redirectToLoginQuietly(): Promise<void> {
  try {
    const { default: router } = await import('@/router')
    const current = router.currentRoute.value
    if (current.name !== 'login') {
      await router.push({ name: 'login', query: { redirect: current.fullPath } })
    }
  } catch {
    return
  }
}

/**
 * 401 の後始末。元の 401 を呼び出し元へ返すため、ここでの失敗は握りつぶす。
 * ストアを未認証化してからでないと、ガードの「認証済み × login」分岐で
 * ホームへ跳ね返されてログイン画面に到達できない。
 */
async function handleUnauthorized(): Promise<void> {
  await logoutQuietly()
  await redirectToLoginQuietly()
}

/** アプリ専用の axios インスタンスを作る（`/api/{appId}`。設計 9.7） */
export function createAppApi(appId: string): AxiosInstance {
  const instance = axios.create({
    baseURL: `/api/${appId}`,
    timeout: REQUEST_TIMEOUT_MS,
    headers: { 'Content-Type': 'application/json' },
  })

  instance.interceptors.request.use(async (config) => {
    const session = await fetchAuthSession()
    const idToken = session.tokens?.idToken?.toString()
    if (idToken) {
      config.headers.Authorization = `Bearer ${idToken}`
    }
    return config
  })

  instance.interceptors.response.use(
    (response) => response,
    async (error: unknown) => {
      if (statusOf(error) === 401) {
        await handleUnauthorized()
      }
      return Promise.reject(error)
    },
  )

  return instance
}

/** axios エラーから API エンベロープの `error` を取り出す。無ければ null。 */
export function extractApiError(error: unknown): ApiError | null {
  if (!error || typeof error !== 'object' || !('response' in error)) {
    return null
  }
  const data = (error as { response?: { data?: unknown } }).response?.data
  if (!data || typeof data !== 'object') {
    return null
  }
  const envelopeError = (data as { error?: unknown }).error
  if (
    envelopeError &&
    typeof envelopeError === 'object' &&
    typeof (envelopeError as ApiError).code === 'string' &&
    typeof (envelopeError as ApiError).message === 'string'
  ) {
    return envelopeError as ApiError
  }
  return null
}
