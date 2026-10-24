import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { reportApi } from '../api'
import { jstToday, shiftDate, useRunNow } from '../composables/useRunNow'
import { makeDetail } from './fixtures'

vi.mock('../api', () => ({ reportApi: { runDay: vi.fn(), getDay: vi.fn() } }))
const DATE = '2026-10-15'
const ACCEPTED = '2026-10-16T09:30:00+09:00'
const AFTER = '2026-10-16T09:30:02+09:00'
const apiError = (status: number, code: string, message: string) => ({ response: { status, data: { ok: false, data: null, error: { code, message } } } })
let scope: ReturnType<typeof effectScope>
function setup() { scope = effectScope(); return scope.run(() => useRunNow())! }
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  vi.mocked(reportApi.runDay).mockResolvedValue({ date: DATE, accepted_at: ACCEPTED })
})
afterEach(() => { scope?.stop(); vi.useRealTimers() })
describe('今すぐ実行のポーリング', () => {
  it('受付後 5 秒ごとに GET し、受付より前の結果と 404 を無視して完了で止まる', async () => {
    const run = setup()
    vi.mocked(reportApi.getDay)
      .mockRejectedValueOnce(apiError(404, 'NOT_FOUND', '指定日の集計がありません'))
      .mockResolvedValueOnce(makeDetail({ date: DATE, status: 'succeeded', started_at: '2026-10-16T07:00:01+09:00' }))
      .mockResolvedValueOnce(makeDetail({ date: DATE, status: 'running', started_at: AFTER }))
      .mockResolvedValueOnce(makeDetail({ date: DATE, status: 'succeeded', level: 'critical', started_at: AFTER }))
    expect(await run.start(DATE)).toBe(true)
    expect(reportApi.runDay).toHaveBeenCalledWith(DATE)
    expect(await run.start(DATE)).toBe(false)
    expect(reportApi.runDay).toHaveBeenCalledTimes(1)
    expect(reportApi.getDay).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(4999)
    expect(reportApi.getDay).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(15001)
    expect(reportApi.getDay).toHaveBeenCalledTimes(4)
    expect(reportApi.getDay).toHaveBeenCalledWith(DATE)
    expect(run.result.value?.level).toBe('critical')
    expect(run.busy.value).toBe(false)
    expect(run.error.value).toBeNull()
    await vi.advanceTimersByTimeAsync(60000)
    expect(reportApi.getDay).toHaveBeenCalledTimes(4)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('失敗した実行も結果として受け取り、理由を出す', async () => {
    const run = setup()
    vi.mocked(reportApi.getDay).mockResolvedValue(makeDetail({ date: DATE, status: 'failed', error: 'AccessDenied', started_at: AFTER }))
    await run.start(DATE)
    await vi.advanceTimersByTimeAsync(5000)
    expect(run.result.value?.status).toBe('failed')
    expect(run.error.value).toContain('AccessDenied')
    expect(run.busy.value).toBe(false)
  })
  it('409 は「同じ日を実行中です」と出し、ポーリングしない', async () => {
    const run = setup()
    vi.mocked(reportApi.runDay).mockRejectedValue(apiError(409, 'RUN_IN_PROGRESS', '同じ日を実行中です。完了してから実行してください'))
    expect(await run.start(DATE)).toBe(false)
    expect(run.error.value).toBe('同じ日を実行中です')
    expect(run.busy.value).toBe(false)
    await vi.advanceTimersByTimeAsync(10000)
    expect(reportApi.getDay).not.toHaveBeenCalled()
  })
  it('受付の失敗と空の受付を成功にしない', async () => {
    const run = setup()
    vi.mocked(reportApi.runDay).mockRejectedValueOnce(apiError(502, 'RUN_UNAVAILABLE', '実行を開始できませんでした。再試行してください'))
    expect(await run.start(DATE)).toBe(false)
    expect(run.error.value).toBe('実行を開始できませんでした。再試行してください')
    vi.mocked(reportApi.runDay).mockResolvedValueOnce(null)
    expect(await run.start(DATE)).toBe(false)
    expect(run.error.value).toBeTruthy()
    expect(run.busy.value).toBe(false)
  })
  it('15 分で止める（GET の応答が戻らない場合も同じ）', async () => {
    const run = setup()
    vi.mocked(reportApi.getDay).mockReturnValue(new Promise(() => {}))
    await run.start(DATE)
    await vi.advanceTimersByTimeAsync(899999)
    expect(run.busy.value).toBe(true)
    await vi.advanceTimersByTimeAsync(1)
    expect(run.busy.value).toBe(false)
    expect(run.error.value).toContain('15 分')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('取得エラーで止め、停止後に届いた応答を無視する', async () => {
    const run = setup()
    vi.mocked(reportApi.getDay).mockRejectedValueOnce(apiError(500, 'INTERNAL_ERROR', '内部エラー'))
    await run.start(DATE)
    await vi.advanceTimersByTimeAsync(5000)
    expect(run.error.value).toContain('内部エラー')
    expect(run.busy.value).toBe(false)
    let resolve!: (value: Awaited<ReturnType<typeof reportApi.getDay>>) => void
    vi.mocked(reportApi.getDay).mockReturnValue(new Promise(done => { resolve = done }))
    await run.start(DATE)
    await vi.advanceTimersByTimeAsync(5000)
    run.stop()
    resolve(makeDetail({ date: DATE, status: 'succeeded', started_at: AFTER }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(run.result.value).toBeNull()
    expect(vi.getTimerCount()).toBe(0)
  })
})
describe('JST の日付', () => {
  it('UTC の前日でも JST の今日を返し、日付をずらせる', () => {
    vi.setSystemTime(new Date('2026-10-15T15:30:00Z'))
    expect(jstToday()).toBe('2026-10-16')
    expect(shiftDate('2026-03-01', -1)).toBe('2026-02-28')
    expect(shiftDate('2026-10-16', -400)).toBe('2025-09-11')
  })
})
