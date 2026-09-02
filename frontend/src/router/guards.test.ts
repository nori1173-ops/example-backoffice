import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { fetchAuthSession, getCurrentUser } from 'aws-amplify/auth'
import { installGuards } from './guards'

vi.mock('aws-amplify/utils', () => ({ Hub: { listen: vi.fn() } }))

const EMAIL = 'user@example.co.jp'
const Stub = { template: '<div />' }

function makeRouter(): Router {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/login',
        name: 'login',
        component: Stub,
        meta: { requiresAuth: false, layout: 'auth' },
      },
      {
        path: '/signup',
        name: 'signup',
        component: Stub,
        meta: { requiresAuth: false, layout: 'auth' },
      },
      {
        path: '/',
        component: Stub,
        children: [
          { path: '', name: 'home', component: Stub, meta: { title: 'ホーム' } },
          { path: 'mfa', name: 'mfa', component: Stub },
          { path: 'forbidden', name: 'forbidden', component: Stub },
          { path: 'admin', name: 'admin', component: Stub, meta: { requiredGroups: ['admin'] } },
          {
            path: '/billing',
            meta: { appId: 'billing', requiredGroups: ['billing-admin'] },
            children: [{ path: 'settings', name: 'billing-settings', component: Stub }],
          },
        ],
      },
    ],
  })
  installGuards(router)
  return router
}

function signedOut(): void {
  vi.mocked(getCurrentUser).mockRejectedValue(new Error('No user'))
}

function signedIn(groups: string[] = []): void {
  vi.mocked(getCurrentUser).mockResolvedValue({
    username: EMAIL,
    userId: 'sub-1234',
    signInDetails: { loginId: EMAIL },
  } as unknown as Awaited<ReturnType<typeof getCurrentUser>>)
  vi.mocked(fetchAuthSession).mockResolvedValue({
    tokens: { idToken: { payload: { 'cognito:groups': groups } } },
  } as unknown as Awaited<ReturnType<typeof fetchAuthSession>>)
}

describe('installGuards', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.resetAllMocks()
    document.title = ''
  })

  it('未認証で保護ルートへ行くと /login?redirect= になる', async () => {
    signedOut()
    const router = makeRouter()

    await router.push('/mfa')

    expect(router.currentRoute.value.name).toBe('login')
    expect(router.currentRoute.value.query.redirect).toBe('/mfa')
  })

  it('認証済みで /login へ行くと / へリダイレクトされる', async () => {
    signedIn()
    const router = makeRouter()

    await router.push('/login')

    expect(router.currentRoute.value.name).toBe('home')
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('requiresAuth: false のルートは未認証でも通る', async () => {
    signedOut()
    const router = makeRouter()

    await router.push('/signup')

    expect(router.currentRoute.value.name).toBe('signup')
  })

  it('requiredGroups に所属していないと /forbidden', async () => {
    signedIn(['viewer'])
    const router = makeRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('requiredGroups に所属していれば通る', async () => {
    signedIn(['admin'])
    const router = makeRouter()

    await router.push('/admin')

    expect(router.currentRoute.value.name).toBe('admin')
  })

  it('親ルートの requiredGroups が子ルートにも効く', async () => {
    signedIn(['viewer'])
    const router = makeRouter()

    await router.push('/billing/settings')

    expect(router.currentRoute.value.name).toBe('forbidden')
  })

  it('afterEach で document.title が「{title} | example-backoffice」になる', async () => {
    signedIn()
    const router = makeRouter()

    await router.push('/')
    expect(document.title).toBe('ホーム | example-backoffice')

    await router.push('/mfa')
    expect(document.title).toBe('example-backoffice')
  })

  it('checkAuth は初回だけ呼ばれる', async () => {
    signedIn()
    const router = makeRouter()

    await router.push('/')
    await router.push('/mfa')

    expect(getCurrentUser).toHaveBeenCalledTimes(1)
  })
})
