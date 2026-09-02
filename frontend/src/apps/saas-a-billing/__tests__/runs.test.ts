import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { billingApi } from '../api'
import type { RunSummary } from '../types'
import { useRunsStore } from '../stores/runs'

vi.mock('../api', () => ({
  billingApi: {
    listRuns: vi.fn(),
    getRun: vi.fn(),
    createRun: vi.fn(),
    getDownloadUrl: vi.fn(),
  },
}))

const MONTH = '2026-10'
const POLL_INTERVAL_MS = 5_000
const POLL_TIMEOUT_MS = 10 * 60 * 1_000

function makeRun(overrides: Partial<RunSummary> = {}): RunSummary {
  return {
    month: MONTH,
    target_env: 'verification',
    status: 'running',
    trigger: 'manual',
    attempt: 1,
    requested_by: null,
    requested_by_email: null,
    distribute: false,
    force: false,
    started_at: '2026-11-21T06:00:00+09:00',
    ...overrides,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.mocked(billingApi.listRuns).mockResolvedValue([])
  vi.mocked(billingApi.getRun).mockResolvedValue(null)
  vi.mocked(billingApi.createRun).mockResolvedValue(null)
  vi.mocked(billingApi.getDownloadUrl).mockResolvedValue(null)
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('runs ストア', () => {
  it('fetchRuns が API を呼び runs に入る', async () => {
    const run = makeRun({ status: 'succeeded' })
    vi.mocked(billingApi.listRuns).mockResolvedValue([run])
    const store = useRunsStore()

    await store.fetchRuns()

    expect(billingApi.listRuns).toHaveBeenCalledTimes(1)
    expect(store.runs).toEqual([run])
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('fetchRuns が targetEnv をそのまま渡す', async () => {
    const store = useRunsStore()

    await store.fetchRuns('production')
    expect(billingApi.listRuns).toHaveBeenLastCalledWith({ targetEnv: 'production' })
    expect(store.targetEnvFilter).toBe('production')

    await store.fetchRuns()
    expect(billingApi.listRuns).toHaveBeenLastCalledWith({ targetEnv: undefined })
    expect(store.targetEnvFilter).toBeNull()
  })

  it('エラー時に error がセットされ loading が false に戻る', async () => {
    vi.mocked(billingApi.listRuns).mockRejectedValue(new Error('boom'))
    const store = useRunsStore()

    await store.fetchRuns()

    expect(store.error).not.toBeNull()
    expect(store.loading).toBe(false)
    expect(store.runs).toEqual([])
  })

  it('download が署名付き URL を取得して window.open する', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    vi.mocked(billingApi.getDownloadUrl).mockResolvedValue({
      url: 'https://example.invalid/report.xlsx?sig=1',
      expires_at: '2026-11-21T06:05:00+09:00',
    })
    const store = useRunsStore()

    await store.download(MONTH, 'production')

    expect(billingApi.getDownloadUrl).toHaveBeenCalledWith(MONTH, 'production')
    expect(open).toHaveBeenCalledWith(
      'https://example.invalid/report.xlsx?sig=1',
      '_blank',
      'noopener,noreferrer',
    )
  })

  it('createRun 後に pollRun が開始される', async () => {
    vi.useFakeTimers()
    vi.mocked(billingApi.createRun).mockResolvedValue({
      month: MONTH,
      target_env: 'verification',
      status: 'queued',
      attempt: 1,
      execution_arn: 'arn:aws:states:ap-northeast-1:000000000000:execution:x',
    })
    vi.mocked(billingApi.getRun).mockResolvedValue({
      run: makeRun({ status: 'succeeded' }),
      result: null,
    })
    const store = useRunsStore()

    await store.createRun({
      month: MONTH,
      targetEnv: 'verification',
      distribute: false,
      force: false,
    })

    expect(billingApi.getRun).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

    expect(billingApi.getRun).toHaveBeenCalledWith(MONTH, { targetEnv: 'verification' })
  })

  it('pollRun が succeeded で停止する', async () => {
    vi.useFakeTimers()
    vi.mocked(billingApi.getRun)
      .mockResolvedValueOnce({ run: makeRun({ status: 'running' }), result: null })
      .mockResolvedValueOnce({ run: makeRun({ status: 'succeeded' }), result: null })
    const store = useRunsStore()

    const polling = store.pollRun(MONTH, 'verification')
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2)
    await polling

    expect(billingApi.getRun).toHaveBeenCalledTimes(2)
    expect(store.runs[0].status).toBe('succeeded')

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 10)
    expect(billingApi.getRun).toHaveBeenCalledTimes(2)
  })

  it('pollRun が同一キーの二重起動を無視する', async () => {
    vi.useFakeTimers()
    vi.mocked(billingApi.getRun).mockResolvedValue({
      run: makeRun({ status: 'running' }),
      result: null,
    })
    const store = useRunsStore()

    const first = store.pollRun(MONTH, 'verification')
    await store.pollRun(MONTH, 'verification')
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)

    expect(billingApi.getRun).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(POLL_TIMEOUT_MS)
    await first
  })

  it('pollRun が上限時間で停止する', async () => {
    vi.useFakeTimers()
    vi.mocked(billingApi.getRun).mockResolvedValue({
      run: makeRun({ status: 'running' }),
      result: null,
    })
    const store = useRunsStore()

    const polling = store.pollRun(MONTH, 'verification')
    await vi.advanceTimersByTimeAsync(POLL_TIMEOUT_MS)
    await polling

    const calls = POLL_TIMEOUT_MS / POLL_INTERVAL_MS
    expect(billingApi.getRun).toHaveBeenCalledTimes(calls)

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 10)
    expect(billingApi.getRun).toHaveBeenCalledTimes(calls)
  })
})
