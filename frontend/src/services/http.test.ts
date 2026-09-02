import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosInstance, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { createPinia, setActivePinia } from 'pinia'
import { fetchAuthSession, getCurrentUser, signOut } from 'aws-amplify/auth'
import router from '@/router'
import { useAuthStore } from '@/stores/auth'
import { createAppApi, extractApiError } from './http'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

// 実ガードを載せたルーターを @/router の代わりに使い、401 → ストア未認証化 → /login
// 到達までの継ぎ目をそのまま検証する（push のスパイだけではガードを通らない）
vi.mock('@/router', async () => {
  const { createMemoryHistory, createRouter } = await import('vue-router')
  const { installGuards } = await import('@/router/guards')
  const Stub = { template: '<div />' }
  const testRouter = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: Stub, meta: { requiresAuth: false } },
      { path: '/', name: 'home', component: Stub },
      { path: '/billing/settings', name: 'billing-settings', component: Stub },
    ],
  })
  installGuards(testRouter)
  return { default: testRouter }
})

const APP_ID = 'saas-a-billing'
const EMAIL = 'user@example.co.jp'

function signedIn(idToken: string | null = 'id-token-1'): void {
  vi.mocked(getCurrentUser).mockResolvedValue({
    username: EMAIL,
    userId: 'sub-1234',
    signInDetails: { loginId: EMAIL },
  } as unknown as Awaited<ReturnType<typeof getCurrentUser>>)
  vi.mocked(fetchAuthSession).mockResolvedValue({
    tokens: idToken === null ? undefined : { idToken: { payload: {}, toString: () => idToken } },
  } as unknown as Awaited<ReturnType<typeof fetchAuthSession>>)
}

async function startAtBillingSettings(): Promise<void> {
  await router.push('/billing/settings')
  expect(router.currentRoute.value.name).toBe('billing-settings')
}

function captureRequests(api: AxiosInstance): InternalAxiosRequestConfig[] {
  const captured: InternalAxiosRequestConfig[] = []
  const adapter: AxiosAdapter = async (config) => {
    captured.push(config)
    return { data: null, status: 200, statusText: 'OK', headers: {}, config } as AxiosResponse
  }
  api.defaults.adapter = adapter
  return captured
}

function failWith(api: AxiosInstance, status: number, data: unknown = {}): void {
  const adapter: AxiosAdapter = async (config) => {
    throw Object.assign(new Error(`Request failed with status code ${status}`), {
      config,
      response: { status, data, statusText: '', headers: {}, config },
    })
  }
  api.defaults.adapter = adapter
}

describe('createAppApi', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    signedIn()
  })

  it('baseURL が /api/{appId} になる', () => {
    const api = createAppApi(APP_ID)

    expect(api.defaults.baseURL).toBe(`/api/${APP_ID}`)
    expect(api.defaults.timeout).toBe(30000)
  })

  it('リクエストに Authorization: Bearer <idToken> が付く', async () => {
    const api = createAppApi(APP_ID)
    const captured = captureRequests(api)

    await api.get('/invoices')

    expect(captured[0].headers.Authorization).toBe('Bearer id-token-1')
  })

  it('トークンが取れないときは Authorization を付けない', async () => {
    signedIn(null)
    const api = createAppApi(APP_ID)
    const captured = captureRequests(api)

    await api.get('/invoices')

    expect(captured[0].headers.Authorization).toBeUndefined()
  })

  it('401 応答で signOut が呼ばれ /login へ遷移する', async () => {
    await startAtBillingSettings()
    const api = createAppApi(APP_ID)
    failWith(api, 401)

    await expect(api.get('/invoices')).rejects.toThrow('Request failed with status code 401')

    expect(signOut).toHaveBeenCalledTimes(1)
    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/billing/settings')
  })

  it('401 後は auth ストアが未認証になりガードに跳ね返されない', async () => {
    await startAtBillingSettings()
    expect(useAuthStore().isAuthenticated).toBe(true)
    const api = createAppApi(APP_ID)
    failWith(api, 401)

    await expect(api.get('/invoices')).rejects.toThrow()

    expect(useAuthStore().isAuthenticated).toBe(false)
    expect(router.currentRoute.value.name).not.toBe('home')
  })

  it('401 以外はそのまま reject される', async () => {
    await startAtBillingSettings()
    const api = createAppApi(APP_ID)
    failWith(api, 500)

    await expect(api.get('/invoices')).rejects.toThrow('Request failed with status code 500')

    expect(signOut).not.toHaveBeenCalled()
    expect(router.currentRoute.value.name).toBe('billing-settings')
  })
})

describe('extractApiError', () => {
  it('エンベロープの error を取り出す', () => {
    const failure = {
      response: {
        status: 403,
        data: {
          ok: false,
          data: null,
          error: { code: 'BO-E003', message: '権限がありません' },
          meta: null,
        },
      },
    }

    expect(extractApiError(failure)).toEqual({ code: 'BO-E003', message: '権限がありません' })
    const noError = { response: { status: 500, data: { ok: false, error: null } } }
    expect(extractApiError(noError)).toBeNull()
    expect(extractApiError({ response: { data: { error: { detail: 'x' } } } })).toBeNull()
    expect(extractApiError(new Error('Network Error'))).toBeNull()
    expect(extractApiError(null)).toBeNull()
  })
})
