import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import { createAppApi } from '@/services/http'
import { reportApi } from '../api'

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>()
  return { ...actual, createAppApi: vi.fn(actual.createAppApi) }
})
const api = vi.mocked(createAppApi).mock.results[0].value
let captured: InternalAxiosRequestConfig[] = []
beforeEach(() => {
  captured = []
  vi.mocked(fetchAuthSession).mockResolvedValue({} as Awaited<ReturnType<typeof fetchAuthSession>>)
  const adapter: AxiosAdapter = async config => {
    captured.push(config)
    return { data: { ok: true, data: { version: 3 }, error: null }, status: 200,
      statusText: 'OK', headers: {}, config }
  }
  api.defaults.adapter = adapter
})
describe('reportApi', () => {
  it('専用の baseURL と設定のエンベロープを使う', async () => {
    expect(createAppApi).toHaveBeenCalledWith('daily-security-report')
    expect(api.defaults.baseURL).toBe('/api/daily-security-report')
    expect(await reportApi.getSettings()).toEqual({ version: 3 })
    expect(captured[0].url).toBe('/settings')
  })
  it('PUT に version と変更項目を渡す', async () => {
    expect(await reportApi.updateSettings({ version: 2, alert_enabled: true })).toEqual({ version: 3 })
    expect(captured[0].method).toBe('put')
    expect(captured[0].url).toBe('/settings')
    expect(JSON.parse(captured[0].data as string)).toEqual({ version: 2, alert_enabled: true })
  })
  it('候補の再取得だけ refresh=1 を渡す', async () => {
    await reportApi.getSlackDirectory()
    await reportApi.getSlackDirectory(true)
    expect(captured[0].url).toBe('/slack/directory')
    expect(captured[0].params).toEqual({})
    expect(captured[1].params).toEqual({ refresh: 1 })
  })
  it('レポートの取得件数を渡す', async () => {
    await reportApi.listDays()
    await reportApi.listDays(90)
    expect(captured[0].url).toBe('/days')
    expect(captured[0].params).toEqual({ limit: 30 })
    expect(captured[1].params).toEqual({ limit: 90 })
  })
})


it('詳細と非同期プレビューを共通エンベロープで読み書きする', async () => {
  const adapter: AxiosAdapter = async config => {
    captured.push(config)
    return { data: { ok: true, data: config.method === 'post' ? { preview_id: 'test-id' } : { status: 'running' }, error: null },
      status: config.method === 'post' ? 202 : 200, statusText: 'OK', headers: {}, config }
  }
  api.defaults.adapter = adapter
  await reportApi.getDay('2026-10-14')
  expect(captured[0].url).toBe('/days/2026-10-14')
  const body = { date: '2026-10-14', prompt: '', model_family: 'haiku' as const, model_pin: '' }
  expect(await reportApi.startPreview(body)).toEqual({ preview_id: 'test-id' })
  expect(captured[1].url).toBe('/ai/preview')
  expect(captured[1].method).toBe('post')
  expect(JSON.parse(captured[1].data as string)).toEqual(body)
  expect(await reportApi.getPreview('test-id')).toEqual({ status: 'running' })
  expect(captured[2].url).toBe('/ai/preview/test-id')
})
describe('今すぐ実行', () => {
  it('POST /days/{date}/run を本文なしで呼ぶ', async () => {
    await reportApi.runDay('2026-10-15')
    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe('/days/2026-10-15/run')
    expect(captured[0].data).toBeUndefined()
  })
})
describe('ログを見る', () => {
  it('POST /days/{date}/logs に ref と同じ本文を渡し、202 と 200 を同じ形で返す', async () => {
    const adapter: AxiosAdapter = async config => {
      captured.push(config)
      const data = config.method === 'post' ? { query_id: '0123456789abcdef', status: 'running' } : { status: 'succeeded', requested_at: '2026-10-16T09:00:00+09:00', result: { rows: [] } }
      return { data: { ok: true, data, error: null }, status: config.method === 'post' ? 202 : 200, statusText: 'OK', headers: {}, config }
    }
    api.defaults.adapter = adapter
    const body = { kind: 'alarmhuba' as const, ip: '198.51.100.7', path: '/api/admin' }
    expect(await reportApi.startLogs('2026-10-14', body)).toEqual({ query_id: '0123456789abcdef', status: 'running' })
    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe('/days/2026-10-14/logs')
    expect(JSON.parse(captured[0].data as string)).toEqual(body)
    expect((await reportApi.getLogs('2026-10-14', '0123456789abcdef'))?.status).toBe('succeeded')
    expect(captured[1].method).toBe('get')
    expect(captured[1].url).toBe('/days/2026-10-14/logs/0123456789abcdef')
  })
})
