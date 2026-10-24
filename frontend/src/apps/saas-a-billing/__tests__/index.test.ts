import { describe, expect, it } from 'vitest'
import manifest from '../index'
import { routes } from '../routes'

const APP_ID = 'saas-a-billing'
const SETTINGS_PATH = 'settings'
const LOGS_PATH = 'logs'
const ADMIN_PATHS = [SETTINGS_PATH, LOGS_PATH]

describe('saas-a-billing のアプリマニフェスト', () => {
  it('マニフェストの id が saas-a-billing', () => {
    expect(manifest.id).toBe(APP_ID)
    expect(manifest.title).toBe('SaaS_A 月額課金レポート')
    expect(manifest.icon).toBe('mdi-receipt-text-outline')
    expect(manifest.order).toBe(10)
    expect(manifest.requiredGroups).toEqual([])
  })

  it("設定とログの nav に requiredGroups: ['admin'] がある", () => {
    const settings = manifest.nav.find((item) => item.to === `/${APP_ID}/${SETTINGS_PATH}`)
    expect(settings?.requiredGroups).toEqual(['admin'])
    const logs = manifest.nav.find((item) => item.to === `/${APP_ID}/${LOGS_PATH}`)
    expect(logs?.title).toBe('ログ')
    expect(logs?.requiredGroups).toEqual(['admin'])
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
      LOGS_PATH,
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

  it("settings / logs ルートの meta.requiredGroups が ['admin']", () => {
    for (const path of ADMIN_PATHS) {
      expect(routes.find((route) => route.path === path)?.meta?.requiredGroups).toEqual(['admin'])
    }
    const others = routes.filter((route) => !ADMIN_PATHS.includes(route.path))
    expect(others.map((route) => route.meta?.requiredGroups)).toEqual([undefined, undefined])
  })
})
