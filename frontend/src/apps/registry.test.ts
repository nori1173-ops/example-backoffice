import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'
import { apps, createRegistry, findApp } from './registry'
import { defineApp, RESERVED_PATHS, type AppManifest } from './types'

const DEFAULT_ORDER = 50
const ID_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/

const StubView = defineComponent({ name: 'StubView', render: () => null })
const lazyView = () => Promise.resolve(StubView)

function sampleApp(id: string, overrides: Partial<AppManifest> = {}): AppManifest {
  return defineApp({
    id,
    title: id,
    icon: 'mdi-application',
    routes: [{ path: '', component: lazyView }],
    nav: [{ title: '一覧', to: `/${id}` }],
    ...overrides,
  })
}

const samples = [
  sampleApp('sample-billing', { title: 'SaaS_A 請求', order: 10 }),
  sampleApp('device-master', { title: '機器マスタ' }),
]

// 実レジストリはアプリ実装（Task P4-6 以降）が入るまで空なので、
// 不変条件は「実レジストリ + 見本マニフェスト」に対して検証し、
// あわせて検査関数が違反を検出できることも確かめる。
const registered = [...apps, ...samples]

const duplicatedIds = (list: AppManifest[]) =>
  list.map((app) => app.id).filter((id, index, ids) => ids.indexOf(id) !== index)

const malformedIds = (list: AppManifest[]) =>
  list.map((app) => app.id).filter((id) => !ID_PATTERN.test(id) || id.length < 3 || id.length > 30)

const reservedIds = (list: AppManifest[]) =>
  list.map((app) => app.id).filter((id) => RESERVED_PATHS.includes(id))

const absoluteRoutePaths = (list: AppManifest[]) =>
  list
    .flatMap((app) => app.routes)
    .map((route) => route.path)
    .filter((path) => path.startsWith('/'))

const eagerRouteComponents = (list: AppManifest[]) =>
  list
    .flatMap((app) => app.routes)
    .filter((route) => !('component' in route) || typeof route.component !== 'function')

const strayNavTargets = (list: AppManifest[]) =>
  list.flatMap((app) =>
    app.nav
      .map((item) => item.to)
      .filter((to) => to !== `/${app.id}` && !to.startsWith(`/${app.id}/`)),
  )

describe('アプリレジストリ', () => {
  it('id が重複しない', () => {
    expect(duplicatedIds(registered)).toEqual([])
    expect(duplicatedIds([sampleApp('twin-app'), sampleApp('twin-app')])).toEqual(['twin-app'])
  })

  it('id が kebab-case（3〜30 文字）', () => {
    expect(malformedIds(registered)).toEqual([])
    const bad = ['SaasA', 'saas_a_billing', 'saasa--billing', 'ab', 'a'.repeat(31)]
    expect(malformedIds(bad.map((id) => sampleApp(id)))).toEqual(bad)
  })

  it('id が予約パスと衝突しない', () => {
    expect(reservedIds(registered)).toEqual([])
    expect(reservedIds(RESERVED_PATHS.map((path) => sampleApp(path)))).toEqual([...RESERVED_PATHS])
  })

  it("routes[].path が '/' で始まらない", () => {
    expect(absoluteRoutePaths(registered)).toEqual([])
    const bad = sampleApp('bad-app', { routes: [{ path: '/settings', component: lazyView }] })
    expect(absoluteRoutePaths([bad])).toEqual(['/settings'])
  })

  it('routes[].component がすべて関数（遅延ロード）', () => {
    expect(eagerRouteComponents(registered)).toEqual([])
    const bad = sampleApp('bad-app', { routes: [{ path: '', component: StubView }] })
    expect(eagerRouteComponents([bad])).toHaveLength(1)
  })

  it('_template がレジストリに含まれない', () => {
    // レジストリ側の除外パターンを介さずに全アプリディレクトリを数え、
    // 先頭 _ 以外がすべて登録され、先頭 _ が 1 つも登録されないことを確かめる。
    const indexFiles = Object.keys(import.meta.glob('./*/index.ts'))
    const registrable = indexFiles.filter((path) => !path.startsWith('./_'))
    expect(apps).toHaveLength(registrable.length)
    expect(apps.filter((app) => app.id.startsWith('_'))).toEqual([])
  })

  it('order 昇順に並ぶ', () => {
    const registry = createRegistry([
      sampleApp('zeta-app', { title: 'ゼータ', order: 60 }),
      sampleApp('annai-app', { title: 'あんない', order: 10 }),
      sampleApp('default-app', { title: 'デフォルト' }),
      sampleApp('fifty-app', { title: 'ごじゅう', order: DEFAULT_ORDER }),
      sampleApp('alert-app', { title: 'アラート', order: 10 }),
    ])
    expect(registry.apps.map((app) => app.title)).toEqual([
      'アラート',
      'あんない',
      'ごじゅう',
      'デフォルト',
      'ゼータ',
    ])
    const orders = apps.map((app) => app.order ?? DEFAULT_ORDER)
    expect(orders).toEqual([...orders].sort((a, b) => a - b))
  })

  it('nav[].to が /{id} で始まる', () => {
    expect(strayNavTargets(registered)).toEqual([])
    const bad = sampleApp('billing-app', { nav: [{ title: '設定', to: '/other-app/settings' }] })
    expect(strayNavTargets([bad])).toEqual(['/other-app/settings'])
  })

  it('findApp が id で引ける / 未知の id は undefined', () => {
    const registry = createRegistry(samples)
    expect(registry.findApp('device-master')).toBe(samples[1])
    expect(registry.findApp('unknown-app')).toBeUndefined()
    expect(findApp('unknown-app')).toBeUndefined()
    expect(apps.map((app) => findApp(app.id))).toEqual(apps)
  })
})
