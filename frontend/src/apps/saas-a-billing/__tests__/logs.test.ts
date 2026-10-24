import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { billingApi } from '../api'
import { useLogsStore } from '../stores/logs'
import type { AuditEntry } from '../types'

vi.mock('../api', () => ({ billingApi: { listAudits: vi.fn() } }))

const NOW = new Date('2026-11-10T09:00:00+09:00')
const LIST_LIMIT = 200

function makeAudit(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    target: 'settings',
    actor: 'sub',
    actor_email: 'devA@example.co.jp',
    before: { cutoff_day: 20 },
    after: { cutoff_day: 25 },
    at: '2026-11-01T10:00:00+09:00',
    ...overrides,
  }
}

function makeRunAudit(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return makeAudit({
    target: 'run',
    actor_email: 'colleagued@example.co.jp',
    before: null,
    after: { month: '2026-10', target_env: 'verification', distribute: false, force: false },
    ...overrides,
  })
}

function daysBefore(days: number): string {
  return new Date(NOW.getTime() - days * 24 * 60 * 60 * 1000).toISOString()
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(billingApi.listAudits).mockReset().mockResolvedValue([])
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useLogsStore', () => {
  it('既定は直近 30 日で、since を UTC の ISO にして送る', async () => {
    const store = useLogsStore()

    await store.fetch()

    expect(store.range).toBe('30d')
    expect(billingApi.listAudits).toHaveBeenCalledWith({ limit: LIST_LIMIT, since: daysBefore(30) })
  })

  it('期間を変えると since も変わる', async () => {
    const store = useLogsStore()
    store.range = '7d'

    await store.fetch()

    expect(billingApi.listAudits).toHaveBeenCalledWith({ limit: LIST_LIMIT, since: daysBefore(7) })
  })

  it('すべての期間なら since を送らない', async () => {
    const store = useLogsStore()
    store.range = 'all'

    await store.fetch()

    expect(billingApi.listAudits).toHaveBeenCalledWith({ limit: LIST_LIMIT })
  })

  it('種別を選ぶと target を送る', async () => {
    const store = useLogsStore()
    store.target = 'run'

    await store.fetch()

    expect(billingApi.listAudits).toHaveBeenCalledWith({
      limit: LIST_LIMIT,
      target: 'run',
      since: daysBefore(30),
    })
  })

  it('取得した監査を entries に入れる', async () => {
    const entries = [makeRunAudit(), makeAudit()]
    vi.mocked(billingApi.listAudits).mockResolvedValue(entries)
    const store = useLogsStore()

    await store.fetch()

    expect(store.entries).toEqual(entries)
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('取得に失敗すると error が入り entries は空になる', async () => {
    vi.mocked(billingApi.listAudits).mockRejectedValue(new Error('boom'))
    const store = useLogsStore()

    await store.fetch()

    expect(store.entries).toEqual([])
    expect(store.error).toContain('操作ログの取得に失敗しました')
    expect(store.loading).toBe(false)
  })

  it('検索は実行者と内容の部分一致で絞る（大文字小文字を無視）', async () => {
    vi.mocked(billingApi.listAudits).mockResolvedValue([makeRunAudit(), makeAudit()])
    const store = useLogsStore()
    await store.fetch()

    store.query = 'COLLEAGUED'
    expect(store.filtered.map((entry) => entry.target)).toEqual(['run'])

    store.query = '締め日'
    expect(store.filtered.map((entry) => entry.target)).toEqual(['settings'])

    store.query = '2026-10'
    expect(store.filtered.map((entry) => entry.target)).toEqual(['run'])

    store.query = '   '
    expect(store.filtered).toHaveLength(2)
  })
})
