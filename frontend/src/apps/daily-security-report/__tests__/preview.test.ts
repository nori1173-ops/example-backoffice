import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { reportApi } from '../api'
import { useAiPreview } from '../composables/useAiPreview'
import { makeAi } from './fixtures'

vi.mock('../api', () => ({ reportApi: { startPreview: vi.fn(), getPreview: vi.fn() } }))
const request = { date: '2026-10-14', prompt: '未保存の文面', model_family: 'opus' as const, model_pin: '' }
let scope: ReturnType<typeof effectScope>
function setup() { scope = effectScope(); return scope.run(() => useAiPreview())! }
beforeEach(() => { vi.useFakeTimers(); vi.resetAllMocks(); vi.mocked(reportApi.startPreview).mockResolvedValue({ preview_id: 'test-id' }) })
afterEach(() => { scope?.stop(); vi.useRealTimers() })
describe('AI preview polling', () => {
  it('未保存の入力を POST し、2 秒ごとに GET、成功したら停止する', async () => {
    const preview = setup()
    vi.mocked(reportApi.getPreview).mockResolvedValueOnce({ status: 'running', date: request.date })
      .mockResolvedValueOnce({ status: 'succeeded', date: request.date, result: { ...makeAi(), level: 'warning' } })
    await preview.start(request)
    expect(reportApi.startPreview).toHaveBeenCalledWith(request)
    await preview.start(request)
    expect(reportApi.startPreview).toHaveBeenCalledTimes(1)
    expect(reportApi.getPreview).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2000)
    expect(reportApi.getPreview).toHaveBeenCalledWith('test-id')
    expect(preview.busy.value).toBe(true)
    await vi.advanceTimersByTimeAsync(2000)
    expect(preview.result.value?.result?.opinion).toBe(makeAi().opinion)
    expect(preview.busy.value).toBe(false)
    await vi.advanceTimersByTimeAsync(20000)
    expect(reportApi.getPreview).toHaveBeenCalledTimes(2)
  })
  it('非同期の失敗理由を表示して停止し、再試行できる', async () => {
    const preview = setup()
    vi.mocked(reportApi.getPreview).mockResolvedValue({ status: 'failed', date: request.date, error: { code: 'AI_UNAVAILABLE', message: '拒否されました' } })
    await preview.start(request)
    await vi.advanceTimersByTimeAsync(2000)
    expect(preview.error.value).toBe('拒否されました')
    expect(preview.busy.value).toBe(false)
    await preview.start(request)
    expect(preview.error.value).toBeNull()
    expect(preview.busy.value).toBe(true)
  })
  it.each(['start', 'poll'] as const)('%s の通信エラーを処理する', async phase => {
    const preview = setup()
    const failure = { response: { data: { error: { code: 'PREVIEW_LIMIT', message: '試行上限です' } } } }
    if (phase === 'start') vi.mocked(reportApi.startPreview).mockRejectedValue(failure)
    else vi.mocked(reportApi.getPreview).mockRejectedValue(failure)
    await preview.start(request)
    await vi.advanceTimersByTimeAsync(2000)
    expect(preview.error.value).toBe('試行上限です')
    expect(preview.busy.value).toBe(false)
  })
  it('5 分で停止する（GET の応答が戻らない場合も同じ）', async () => {
    const preview = setup()
    vi.mocked(reportApi.getPreview).mockReturnValue(new Promise(() => {}))
    await preview.start(request)
    await vi.advanceTimersByTimeAsync(299999)
    expect(preview.busy.value).toBe(true)
    await vi.advanceTimersByTimeAsync(1)
    expect(preview.busy.value).toBe(false)
    expect(preview.error.value).toContain('5 分')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('画面離脱時に停止し、遅れて届いた応答を無視する', async () => {
    const preview = setup()
    let resolve!: (value: Awaited<ReturnType<typeof reportApi.getPreview>>) => void
    vi.mocked(reportApi.getPreview).mockReturnValue(new Promise(done => { resolve = done }))
    await preview.start(request)
    await vi.advanceTimersByTimeAsync(2000)
    preview.stop()
    resolve({ status: 'succeeded', date: request.date, result: { ...makeAi(), level: 'warning' } })
    await vi.advanceTimersByTimeAsync(2000)
    expect(preview.result.value).toBeNull()
    expect(preview.busy.value).toBe(false)
    expect(vi.getTimerCount()).toBe(0)
  })
  it('空の受付と空の結果を成功にしない', async () => {
    const preview = setup()
    vi.mocked(reportApi.startPreview).mockResolvedValueOnce(null)
    await preview.start(request)
    expect(preview.error.value).toBeTruthy()
    vi.mocked(reportApi.getPreview).mockResolvedValue(null)
    await preview.start(request)
    await vi.advanceTimersByTimeAsync(2000)
    expect(preview.error.value).toBeTruthy()
    expect(preview.busy.value).toBe(false)
  })
})


it('180 秒を過ぎても 5 分以内なら結果を受け取れる', async () => {
  const preview = setup()
  vi.mocked(reportApi.getPreview).mockResolvedValue({ status: 'running', date: request.date })
  await preview.start(request)
  await vi.advanceTimersByTimeAsync(180000)
  expect(preview.busy.value).toBe(true)
  vi.mocked(reportApi.getPreview).mockResolvedValue({ status: 'succeeded', date: request.date, result: { ...makeAi(), level: 'ok' } })
  await vi.advanceTimersByTimeAsync(2000)
  expect(preview.result.value?.status).toBe('succeeded')
  expect(preview.busy.value).toBe(false)
})
