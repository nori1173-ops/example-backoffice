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

  // 集計対象環境はサイト固定なのでフィルタを渡さない（設計 R1）
  it('fetchRuns が環境フィルタを渡さない', async () => {
    const store = useRunsStore()

    await store.fetchRuns()

    expect(billingApi.listRuns).toHaveBeenLastCalledWith()
  })

  it('エラー時に error がセットされ loading が false に戻る', async () => {
    vi.mocked(billingApi.listRuns).mockRejectedValue(new Error('boom'))
    const store = useRunsStore()

    await store.fetchRuns()

    expect(store.error).not.toBeNull()
    expect(store.loading).toBe(false)
    expect(store.runs).toEqual([])
  })

  // ポップアップブロックを避けるためアンカー遷移で開く（UI-15 / 設計 R4）
  it('download が署名付き URL をアンカー遷移で開く', async () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    let href = ''
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(function (this: HTMLAnchorElement) {
        href = this.href
      })
    vi.mocked(billingApi.getDownloadUrl).mockResolvedValue({
      url: 'https://example.invalid/report.xlsx?sig=1',
      expires_at: '2026-11-21T06:05:00+09:00',
    })
    const store = useRunsStore()

    await store.download(MONTH, 'production')

    expect(billingApi.getDownloadUrl).toHaveBeenCalledWith(MONTH, 'production')
    expect(click).toHaveBeenCalledTimes(1)
    expect(href).toBe('https://example.invalid/report.xlsx?sig=1')
    expect(open).not.toHaveBeenCalled()
    expect(document.querySelector('a[href*="report.xlsx"]')).toBeNull()
  })

  it('ダウンロード中は downloading に対象のキーが入る', async () => {
    let release: (value: null) => void = () => {}
    vi.mocked(billingApi.getDownloadUrl).mockReturnValue(
      new Promise((resolve) => {
        release = resolve
      }),
    )
    const store = useRunsStore()

    const pending = store.download(MONTH, 'verification')
    expect(store.downloading).toBe(`verification#${MONTH}`)

    release(null)
    await pending
    expect(store.downloading).toBeNull()
  })

  it('ダウンロードに失敗しても downloading が残らない', async () => {
    vi.mocked(billingApi.getDownloadUrl).mockRejectedValue(new Error('boom'))
    const store = useRunsStore()

    await store.download(MONTH, 'verification')

    expect(store.error).not.toBeNull()
    expect(store.downloading).toBeNull()
  })

  // 409 の生文言「RUN を取得できません: ALREADY_SUCCEEDED」が画面に出ていた
  it.each([
    ['ALREADY_SUCCEEDED', '強制再実行'],
    ['ALREADY_RUNNING', '実行中'],
  ])('createRun の 409 %s を日本語にする', async (code, expected) => {
    vi.mocked(billingApi.createRun).mockRejectedValue({
      response: { data: { data: null, error: { code, message: `RUN を取得できません: ${code}` } } },
    })
    const store = useRunsStore()

    const accepted = await store.createRun({ month: MONTH, distribute: false, force: false })

    expect(accepted).toBeNull()
    expect(store.error).toContain(expected)
    expect(store.error).not.toContain(code)
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
