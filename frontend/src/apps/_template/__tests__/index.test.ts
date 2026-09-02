import { describe, expect, it } from 'vitest'
import { apps } from '@/apps/registry'
import { RESERVED_PATHS } from '@/apps/types'
import manifest from '../index'
import { routes } from '../routes'

const ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/

describe('_template のアプリマニフェスト', () => {
  it('id がアプリ id 規約（kebab-case 3〜30 文字・予約パス以外）を満たす', () => {
    expect(manifest.id).toMatch(ID_PATTERN)
    expect(manifest.id.length).toBeGreaterThanOrEqual(3)
    expect(manifest.id.length).toBeLessThanOrEqual(30)
    expect(RESERVED_PATHS).not.toContain(manifest.id)
  })

  it('title / icon が空でない', () => {
    expect(manifest.title).not.toBe('')
    expect(manifest.icon).toMatch(/^mdi-/)
  })

  it('routes が routes.ts の定義そのもので、空でない', () => {
    expect(manifest.routes).toBe(routes)
    expect(manifest.routes.length).toBeGreaterThan(0)
  })

  it('全ルートがトップレベルの component（遅延ロード関数）を持つ', () => {
    // レジストリ検査（registry.test.ts）が component 関数を要求するため、
    // redirect のみ・children のみのルートは雛形に置かない。
    for (const route of manifest.routes) {
      expect(typeof route.component).toBe('function')
    }
  })

  it("routes[].path が '/' で始まらない（親 /{id} の子ルート）", () => {
    for (const route of manifest.routes) {
      expect(route.path.startsWith('/')).toBe(false)
    }
  })

  it('nav[].to がすべて /{id} 配下を指す', () => {
    expect(manifest.nav.length).toBeGreaterThan(0)
    for (const item of manifest.nav) {
      expect(item.to === `/${manifest.id}` || item.to.startsWith(`/${manifest.id}/`)).toBe(true)
    }
  })

  it('雛形なのでレジストリに登録されない', () => {
    expect(apps.map((app) => app.id)).not.toContain(manifest.id)
  })
})
