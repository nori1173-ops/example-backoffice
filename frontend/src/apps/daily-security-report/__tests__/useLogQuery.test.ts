import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { reportApi } from '../api'
import { useLogQuery } from '../composables/useLogQuery'
import type { LogQueryResult, LogRef } from '../types'

vi.mock('../api', () => ({ reportApi: { startLogs: vi.fn(), getLogs: vi.fn() } }))
const DATE = '2026-10-14'
const REF: LogRef = { kind: 'alarmhuba', ip: '198.51.100.7', path: '/api/admin' }
const RESULT: LogQueryResult = {
  source: 'CloudWatch Logs /aws/lambda/example-resolver',
  range: { from: '2026-10-14T00:00:00+09:00', to: '2026-10-15T00:00:00+09:00' },
  rows: [{ time: '2026-10-14T09:12:03+09:00', method: 'GET', path: '/api/admin', status: 403 }],
  truncated: false,
  scanned_bytes: 1048576,
}
let scope: ReturnType<typeof effectScope>
function setup() { scope = effectScope(); return scope.run(() => useLogQuery())! }
beforeEach(() => {
  vi.useFakeTimers()
  vi.resetAllMocks()
  vi.mocked(reportApi.startLogs).mockResolvedValue({ query_id: 'q1', status: 'running' })
})
afterEach(() => { scope?.stop(); vi.useRealTimers() })

describe('useLogQuery', () => {
  it('202 を受けたら 2 秒ごとに GET し、succeeded で止まる', async () => {
    const query = setup()
    vi.mocked(reportApi.getLogs).mockResolvedValueOnce({ status: 'running', requested_at: '2026-10-16T09:00:00+09:00' })
      .mockResolvedValueOnce({ status: 'succeeded', requested_at: '2026-10-16T09:00:00+09:00', result: RESULT })
    await query.open(DATE, REF)
    expect(reportApi.startLogs).toHaveBeenCalledWith(DATE, REF)
    expect(query.status.value).toBe('running')
    expect(reportApi.getLogs).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2000)
    expect(reportApi.getLogs).toHaveBeenCalledWith(DATE, 'q1')
    expect(query.status.value).toBe('running')
    await vi.advanceTimersByTimeAsync(2000)
    expect(query.status.value).toBe('succeeded')
    expect(query.result.value).toEqual(RESULT)
    expect(query.error.value).toBeNull()
    await vi.advanceTimersByTimeAsync(20000)
    expect(reportApi.getLogs).toHaveBeenCalledTimes(2)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('24 時間以内の同じ問い合わせ（200）は GET せずに結果を出す', async () => {
    const query = setup()
    vi.mocked(reportApi.startLogs).mockResolvedValue({ query_id: 'q1', status: 'succeeded', result: RESULT })
    await query.open(DATE, REF)
    expect(query.status.value).toBe('succeeded')
    expect(query.result.value).toEqual(RESULT)
    await vi.advanceTimersByTimeAsync(10000)
    expect(reportApi.getLogs).not.toHaveBeenCalled()
  })
  it('running のまま 90 秒で TIMEOUT にして止まる（GET が戻らなくても同じ）', async () => {
    const query = setup()
    vi.mocked(reportApi.getLogs).mockResolvedValueOnce({ status: 'running' }).mockReturnValue(new Promise(() => {}))
    await query.open(DATE, REF)
    await vi.advanceTimersByTimeAsync(89999)
    expect(query.status.value).toBe('running')
    await vi.advanceTimersByTimeAsync(1)
    expect(query.status.value).toBe('failed')
    expect(query.errorCode.value).toBe('TIMEOUT')
    expect(query.error.value).toContain('90 秒')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('429 は「同時に見られるのは 3 件まで」と出す', async () => {
    const query = setup()
    vi.mocked(reportApi.startLogs).mockRejectedValue({ response: { status: 429, data: { error: { code: 'TOO_MANY_QUERIES', message: 'too many' } } } })
    await query.open(DATE, REF)
    expect(query.status.value).toBe('failed')
    expect(query.errorCode.value).toBe('TOO_MANY_QUERIES')
    expect(query.error.value).toBe('同時に見られるのは 3 件まで')
    vi.mocked(reportApi.startLogs).mockRejectedValue({ response: { status: 429, data: {} } })
    await query.open(DATE, REF)
    expect(query.error.value).toBe('同時に見られるのは 3 件まで')
  })
  it('サーバ側の失敗はコードから理由を出す（TIMEOUT は 60 秒で打ち切り）', async () => {
    const query = setup()
    vi.mocked(reportApi.getLogs).mockResolvedValue({ status: 'failed', error: 'TIMEOUT' })
    await query.open(DATE, REF)
    await vi.advanceTimersByTimeAsync(2000)
    expect(query.status.value).toBe('failed')
    expect(query.errorCode.value).toBe('TIMEOUT')
    expect(query.error.value).toBe('60 秒で打ち切り')
    vi.mocked(reportApi.getLogs).mockResolvedValue({ status: 'failed', error: { code: 'ACCESS_DENIED', message: '' } })
    await query.open(DATE, REF)
    await vi.advanceTimersByTimeAsync(2000)
    expect(query.error.value).toBe('ACCESS_DENIED')
  })
  it('受付の 400 は API の理由とコードを出す', async () => {
    const query = setup()
    vi.mocked(reportApi.startLogs).mockRejectedValue({ response: { status: 400, data: { error: { code: 'INVALID_QUERY', message: '問い合わせの内容が不正です' } } } })
    await query.open(DATE, REF)
    expect(query.status.value).toBe('failed')
    expect(query.error.value).toBe('問い合わせの内容が不正です（INVALID_QUERY）')
  })
  it('閉じたらポーリングを止め、遅れて届いた応答を無視する', async () => {
    const query = setup()
    let resolve!: (value: Awaited<ReturnType<typeof reportApi.getLogs>>) => void
    vi.mocked(reportApi.getLogs).mockReturnValue(new Promise(done => { resolve = done }))
    await query.open(DATE, REF)
    await vi.advanceTimersByTimeAsync(2000)
    query.close()
    resolve({ status: 'succeeded', result: RESULT })
    await vi.advanceTimersByTimeAsync(10000)
    expect(query.result.value).toBeNull()
    expect(query.status.value).toBe('idle')
    expect(reportApi.getLogs).toHaveBeenCalledTimes(1)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('受付の応答が届く前に閉じたら GET を始めない', async () => {
    const query = setup()
    let resolve!: (value: Awaited<ReturnType<typeof reportApi.startLogs>>) => void
    vi.mocked(reportApi.startLogs).mockReturnValue(new Promise(done => { resolve = done }))
    const opening = query.open(DATE, REF)
    query.close()
    resolve({ query_id: 'q1', status: 'running' })
    await opening
    await vi.advanceTimersByTimeAsync(10000)
    expect(reportApi.getLogs).not.toHaveBeenCalled()
    expect(query.status.value).toBe('idle')
  })
  it('空の受付・空の結果を成功にしない', async () => {
    const query = setup()
    vi.mocked(reportApi.startLogs).mockResolvedValueOnce(null)
    await query.open(DATE, REF)
    expect(query.status.value).toBe('failed')
    expect(query.error.value).toBeTruthy()
    vi.mocked(reportApi.getLogs).mockResolvedValue({ status: 'succeeded' })
    await query.open(DATE, REF)
    await vi.advanceTimersByTimeAsync(2000)
    expect(query.status.value).toBe('failed')
    expect(query.error.value).toBeTruthy()
  })
  it('別の行を開き直すと前の問い合わせの状態を消して始め直す', async () => {
    const query = setup()
    vi.mocked(reportApi.startLogs).mockResolvedValueOnce({ query_id: 'q1', status: 'succeeded', result: RESULT })
    await query.open(DATE, REF)
    const other: LogRef = { kind: 'lambda', function: 'example-fn' }
    await query.open(DATE, other)
    expect(reportApi.startLogs).toHaveBeenLastCalledWith(DATE, other)
    expect(query.result.value).toBeNull()
    expect(query.status.value).toBe('running')
  })
  it('スコープの破棄で止まる', async () => {
    const query = setup()
    vi.mocked(reportApi.getLogs).mockResolvedValue({ status: 'running' })
    await query.open(DATE, REF)
    scope.stop()
    expect(vi.getTimerCount()).toBe(0)
  })
})
