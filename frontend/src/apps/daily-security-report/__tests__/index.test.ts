import { describe, expect, it } from 'vitest'
import app from '../index'

describe('daily-security-report manifest', () => {
  it('全員向けのレポート一覧と admin 向け設定を遅延ロードで登録する', () => {
    expect(app).toMatchObject({ id: 'daily-security-report', title: 'AWS日次セキュリティレポート',
      icon: 'mdi-shield-search', order: 30, requiredGroups: [], helpMode: true })
    expect(app.nav).toEqual([
      expect.objectContaining({ title: 'レポート一覧', to: '/daily-security-report' }),
      expect.objectContaining({ title: '設定', to: '/daily-security-report/settings', requiredGroups: ['admin'] }),
    ])
    expect(app.routes.map(route => route.path)).toEqual(['', 'days/:date', 'settings'])
    expect(app.routes[1]).toMatchObject({ name: 'daily-security-report.day', meta: { title: 'レポートの詳細' } })
    expect(app.routes[1].meta?.requiredGroups).toBeUndefined()
    expect(app.routes.every(route => 'component' in route && typeof route.component === 'function')).toBe(true)
    expect(app.routes[2].meta?.requiredGroups).toEqual(['admin'])
  })
})
