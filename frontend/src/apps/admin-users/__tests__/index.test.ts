import { describe, expect, it } from 'vitest'
import { apps, findApp } from '@/apps/registry'
import { RESERVED_PATHS } from '@/apps/types'
import manifest from '../index'
import { routes } from '../routes'

describe('admin-users のアプリマニフェスト', () => {
  it('id が予約パスと衝突せず、レジストリに登録される', () => {
    expect(RESERVED_PATHS).not.toContain(manifest.id)
    expect(apps.map((app) => app.id)).toContain('admin-users')
    expect(findApp('admin-users')).toBe(manifest)
  })

  // 権限の実体はバックエンドだが、admin 以外にメニューを見せない（設計 6.3 / 9.6）
  it('admin だけに見える', () => {
    expect(manifest.requiredGroups).toEqual(['admin'])
  })

  it('routes が routes.ts の定義そのもので、遅延ロードの子ルート 1 本', () => {
    expect(manifest.routes).toBe(routes)
    expect(manifest.routes).toHaveLength(1)
    expect(manifest.routes[0].name).toBe('admin-users.users')
    expect(manifest.routes[0].path).toBe('')
    expect(typeof manifest.routes[0].component).toBe('function')
  })

  it('nav が /admin-users を指す', () => {
    expect(manifest.nav).toHaveLength(1)
    expect(manifest.nav[0].to).toBe('/admin-users')
  })

  it('サイドバーでは業務アプリより下に並ぶ', () => {
    expect(manifest.order).toBe(90)
    expect(manifest.icon).toMatch(/^mdi-/)
  })
})
