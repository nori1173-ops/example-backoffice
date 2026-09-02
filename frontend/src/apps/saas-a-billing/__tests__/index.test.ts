import { describe, expect, it } from 'vitest'
import manifest from '../index'
import { routes } from '../routes'

const APP_ID = 'saas-a-billing'
const SETTINGS_PATH = 'settings'

describe('saas-a-billing のアプリマニフェスト', () => {
  it('マニフェストの id が saas-a-billing', () => {
    expect(manifest.id).toBe(APP_ID)
    expect(manifest.title).toBe('SaaS_A 月額課金レポート')
    expect(manifest.icon).toBe('mdi-receipt-text-outline')
    expect(manifest.order).toBe(10)
    expect(manifest.requiredGroups).toEqual([])
  })

  it("設定の nav に requiredGroups: ['admin'] がある", () => {
    const settings = manifest.nav.find((item) => item.to === `/${APP_ID}/${SETTINGS_PATH}`)
    expect(settings?.requiredGroups).toEqual(['admin'])
    const history = manifest.nav.find((item) => item.to === `/${APP_ID}`)
    expect(history?.title).toBe('実行履歴')
    expect(history?.requiredGroups).toBeUndefined()
  })

  it("routes の path がすべて相対（'/' で始まらない）", () => {
    expect(manifest.routes).toBe(routes)
    expect(routes.map((route) => route.path)).toEqual([
      '',
      'runs/:targetEnv/:month',
      SETTINGS_PATH,
    ])
    for (const route of routes) {
      expect(route.path.startsWith('/')).toBe(false)
    }
  })

  it('routes の component がすべて関数（遅延ロード）', () => {
    for (const route of routes) {
      expect(typeof route.component).toBe('function')
    }
  })

  it("settings ルートの meta.requiredGroups が ['admin']", () => {
    const settings = routes.find((route) => route.path === SETTINGS_PATH)
    expect(settings?.meta?.requiredGroups).toEqual(['admin'])
    const others = routes.filter((route) => route.path !== SETTINGS_PATH)
    expect(others.map((route) => route.meta?.requiredGroups)).toEqual([undefined, undefined])
  })
})
