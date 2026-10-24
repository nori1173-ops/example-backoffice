import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { reportApi } from '../api'
import { useSettingsStore } from '../stores/settings'
import { useDaysStore } from '../stores/days'
import { makeDay, makeDetail, makeDirectory, makeSettings } from './fixtures'

vi.mock('../api', () => ({ reportApi: {
  getSettings: vi.fn(), updateSettings: vi.fn(), getSlackDirectory: vi.fn(), listDays: vi.fn(), getDay: vi.fn(),
} }))
const failure = (code: string, message: string) => ({ response: { data: { error: { code, message } } } })
beforeEach(() => {
  setActivePinia(createPinia())
  vi.resetAllMocks()
})
describe('settings store', () => {
  it('専用 ID で設定・固定対象・Slack モードを取得する', async () => {
    vi.mocked(reportApi.getSettings).mockResolvedValue(makeSettings())
    const store = useSettingsStore()
    expect(store.$id).toBe('daily-security-report/settings')
    await store.fetch()
    expect(store.settings).toEqual(makeSettings())
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })
  it('取得失敗の理由と空応答を扱う', async () => {
    const store = useSettingsStore()
    vi.mocked(reportApi.getSettings).mockRejectedValue(failure('UNAVAILABLE', '読み出し失敗'))
    await store.fetch()
    expect(store.error).toBe('読み出し失敗')
    vi.mocked(reportApi.getSettings).mockResolvedValue(null)
    await store.fetch()
    expect(store.error).toBe('設定の取得に失敗しました')
  })
  it('未取得なら保存しない', async () => {
    expect(await useSettingsStore().save({})).toBe(false)
    expect(reportApi.updateSettings).not.toHaveBeenCalled()
  })
  it('現在の version で保存して応答を反映し、表示専用値を保持する', async () => {
    const store = useSettingsStore()
    store.settings = makeSettings()
    vi.mocked(reportApi.updateSettings).mockResolvedValue(makeSettings({ version: 3, alert_enabled: true }))
    expect(await store.save({ alert_enabled: true })).toBe(true)
    expect(reportApi.updateSettings).toHaveBeenCalledWith({ version: 2, alert_enabled: true })
    expect(store.settings?.version).toBe(3)
    expect(store.settings?.target_account).toBe('210000000001')
    expect(store.loading).toBe(false)
  })
  it.each([
    ['VERSION_CONFLICT', '競合', '他の人が更新'],
    ['VALIDATION_ERROR', '除外リスト 2 行目 reason が必要です', '除外リスト 2 行目 reason が必要です'],
    ['MAIL_DOMAIN_NOT_ALLOWED', '許可ドメイン外です', '許可ドメイン外です'],
  ])('保存失敗 %s の理由を保持し編集値を失わない', async (code, message, expected) => {
    const store = useSettingsStore()
    store.settings = makeSettings()
    vi.mocked(reportApi.updateSettings).mockRejectedValue(failure(code, message))
    expect(await store.save({ alert_enabled: true })).toBe(false)
    expect(store.error).toContain(expected)
    expect(store.settings.version).toBe(2)
    expect(store.loading).toBe(false)
  })
  it('保存の空応答と通信エラーを成功としない', async () => {
    const store = useSettingsStore()
    store.settings = makeSettings()
    vi.mocked(reportApi.updateSettings).mockResolvedValue(null)
    expect(await store.save({})).toBe(false)
    vi.mocked(reportApi.updateSettings).mockRejectedValue(new Error('offline'))
    expect(await store.save({})).toBe(false)
    expect(store.error).toBe('設定の保存に失敗しました')
  })
  it('候補の失敗を設定の保存とは独立に扱い、再取得できる', async () => {
    const store = useSettingsStore()
    store.settings = makeSettings()
    vi.mocked(reportApi.getSlackDirectory).mockRejectedValue(failure('SLACK_NOT_CONFIGURED', 'Bot 未登録'))
    await store.loadSlackDirectory()
    expect(store.directoryError).toBe('Bot 未登録')
    expect(store.error).toBeNull()
    vi.mocked(reportApi.getSlackDirectory).mockResolvedValue(makeDirectory())
    await store.loadSlackDirectory(true)
    expect(reportApi.getSlackDirectory).toHaveBeenLastCalledWith(true)
    expect(store.directory).toEqual(makeDirectory())
    expect(store.directoryLoading).toBe(false)
    expect(store.directoryError).toBeNull()
  })
  it('候補の通信エラーの既定文言を出す', async () => {
    const store = useSettingsStore()
    vi.mocked(reportApi.getSlackDirectory).mockRejectedValue(new Error('offline'))
    await store.loadSlackDirectory()
    expect(store.directoryError).toBe('候補の取得に失敗しました')
  })
})
describe('days store', () => {
  it('専用 ID と指定件数で一覧を読む', async () => {
    const days = [makeDay()]
    vi.mocked(reportApi.listDays).mockResolvedValue(days)
    const store = useDaysStore()
    expect(store.$id).toBe('daily-security-report/days')
    await store.fetch(90)
    expect(reportApi.listDays).toHaveBeenCalledWith(90)
    expect(store.days).toEqual(days)
    expect(store.loading).toBe(false)
  })
  it('空応答・API エラー・通信エラーを扱う', async () => {
    const store = useDaysStore()
    vi.mocked(reportApi.listDays).mockResolvedValue(null)
    await store.fetch()
    expect(store.days).toEqual([])
    vi.mocked(reportApi.listDays).mockRejectedValue(failure('UNAVAILABLE', 'レポートを取得できません'))
    await store.fetch()
    expect(store.error).toBe('レポートを取得できません')
    vi.mocked(reportApi.listDays).mockRejectedValue(new Error('offline'))
    await store.fetch()
    expect(store.error).toBe('レポート一覧の取得に失敗しました')
    expect(store.loading).toBe(false)
  })
})


describe('day detail cache', () => {
  it('日付ごとにキャッシュし、明示的に更新できる', async () => {
    const store = useDaysStore()
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail())
    await store.fetchDay('2026-10-14')
    await store.fetchDay('2026-10-14')
    expect(reportApi.getDay).toHaveBeenCalledTimes(1)
    expect(store.details['2026-10-14']).toEqual(makeDetail())
    await store.fetchDay('2026-10-14', true)
    expect(reportApi.getDay).toHaveBeenCalledTimes(2)
    expect(store.detailLoading['2026-10-14']).toBe(false)
  })
  it('同じ日の取得を重複させず、別の日は独立して読み込む', async () => {
    const store = useDaysStore()
    let resolve!: (value: ReturnType<typeof makeDetail>) => void
    vi.mocked(reportApi.getDay).mockReturnValueOnce(new Promise(done => { resolve = done }))
      .mockResolvedValueOnce(makeDetail({ date: '2026-10-13' }))
    const first = store.fetchDay('2026-10-14')
    const repeated = store.fetchDay('2026-10-14')
    await store.fetchDay('2026-10-13')
    expect(store.detailLoading['2026-10-14']).toBe(true)
    expect(store.details['2026-10-13'].date).toBe('2026-10-13')
    expect(reportApi.getDay).toHaveBeenCalledTimes(2)
    resolve(makeDetail())
    await Promise.all([first, repeated])
    expect(store.detailLoading['2026-10-14']).toBe(false)
  })
  it('404 と通信エラーを区別し、再取得で回復する', async () => {
    const store = useDaysStore()
    vi.mocked(reportApi.getDay).mockRejectedValueOnce(failure('NOT_FOUND', 'なし'))
    await store.fetchDay('2026-10-14')
    expect(store.detailNotFound['2026-10-14']).toBe(true)
    expect(store.detailErrors['2026-10-14']).toBe('レポートがありません')
    vi.mocked(reportApi.getDay).mockRejectedValueOnce(new Error('offline'))
    await store.fetchDay('2026-10-13')
    expect(store.detailNotFound['2026-10-13']).toBe(false)
    expect(store.detailErrors['2026-10-13']).toContain('取得に失敗')
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail())
    await store.fetchDay('2026-10-14')
    expect(store.detailErrors['2026-10-14']).toBeNull()
    expect(store.detailNotFound['2026-10-14']).toBe(false)
  })
  it('詳細の空応答を取得エラーとして扱う', async () => {
    vi.mocked(reportApi.getDay).mockResolvedValue(null)
    const store = useDaysStore()
    await store.fetchDay('2026-10-14')
    expect(store.detailErrors['2026-10-14']).toContain('取得に失敗')
  })
})


it('実行中の詳細をキャッシュに固定せず、次の入場時に成功結果を取得する', async () => {
  const store = useDaysStore()
  vi.mocked(reportApi.getDay).mockResolvedValueOnce(makeDetail({ status: 'running', summary: null, ai: null }))
    .mockResolvedValueOnce(makeDetail())
  await store.fetchDay('2026-10-14')
  expect(store.details['2026-10-14'].status).toBe('running')
  await store.fetchDay('2026-10-14')
  expect(store.details['2026-10-14'].status).toBe('succeeded')
})
