import { describe, expect, it, vi } from 'vitest'
import router from './index'

vi.mock('@/apps/registry', () => ({
  apps: [
    {
      id: 'demo-app',
      title: 'デモ',
      icon: 'mdi-cog',
      requiredGroups: ['demo-admin'],
      routes: [{ path: 'items', name: 'demo-items', component: { template: '<div />' } }],
      nav: [],
    },
  ],
  findApp: () => undefined,
}))

describe('router', () => {
  it('アプリのルートが /{app}/... で解決され meta にアプリ情報が乗る', () => {
    const resolved = router.resolve('/demo-app/items')

    expect(resolved.name).toBe('demo-items')
    expect(resolved.meta.appId).toBe('demo-app')
    expect(resolved.meta.requiredGroups).toEqual(['demo-admin'])
    expect(router.resolve('/apps/demo-app/items').name).toBe('not-found')
  })

  it('/login と /signup は requiresAuth: false', () => {
    expect(router.resolve('/login').meta.requiresAuth).toBe(false)
    expect(router.resolve('/signup').meta.requiresAuth).toBe(false)
    expect(router.resolve('/').meta.requiresAuth).toBeUndefined()
  })
})
