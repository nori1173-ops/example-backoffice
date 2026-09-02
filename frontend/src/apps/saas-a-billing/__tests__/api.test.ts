import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import { createAppApi } from '@/services/http'
import { billingApi } from '../api'

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>()
  return { ...actual, createAppApi: vi.fn(actual.createAppApi) }
})

const APP_ID = 'saas-a-billing'
const MONTH = '2026-10'

// アプリの axios インスタンスは api.ts の読み込み時に 1 つだけ作られる
const api = vi.mocked(createAppApi).mock.results[0].value

let captured: InternalAxiosRequestConfig[] = []

beforeEach(() => {
  captured = []
  vi.mocked(fetchAuthSession).mockResolvedValue(
    {} as Awaited<ReturnType<typeof fetchAuthSession>>,
  )
  const adapter: AxiosAdapter = async (config) => {
    captured.push(config)
    return {
      data: { ok: true, data: null, error: null, meta: null },
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    } as AxiosResponse
  }
  api.defaults.adapter = adapter
})

describe('billingApi', () => {
  it('api.ts の baseURL が /api/saas-a-billing', () => {
    expect(createAppApi).toHaveBeenCalledWith(APP_ID)
    expect(api.defaults.baseURL).toBe(`/api/${APP_ID}`)
  })

  it('listRuns が target_env と limit をクエリに載せる', async () => {
    await billingApi.listRuns({ targetEnv: 'production', limit: 24 })
    expect(captured[0].method).toBe('get')
    expect(captured[0].url).toBe('/runs')
    expect(captured[0].params).toEqual({ target_env: 'production', limit: 24 })

    await billingApi.listRuns()
    expect(captured[1].params).toEqual({})
  })

  it('getRun が detail クエリを載せる', async () => {
    await billingApi.getRun(MONTH, { targetEnv: 'verification', detail: true })
    expect(captured[0].url).toBe(`/runs/${MONTH}`)
    expect(captured[0].params).toEqual({ target_env: 'verification', detail: true })

    // API 側の既定は detail=false。要らないときは載せない
    await billingApi.getRun(MONTH)
    expect(captured[1].params).toEqual({})
  })

  // 集計対象環境はサーバ側で固定されるため既定では送らない（設計 R1）
  it('createRun が targetEnv 無しなら target_env を送らない', async () => {
    await billingApi.createRun({ month: MONTH, distribute: true, force: false })

    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe('/runs')
    expect(JSON.parse(captured[0].data as string)).toEqual({
      month: MONTH,
      distribute: true,
      force: false,
    })
  })

  it('createRun が targetEnv を渡されたときだけ target_env を載せる', async () => {
    await billingApi.createRun({
      month: MONTH,
      targetEnv: 'production',
      distribute: true,
      force: false,
    })

    expect(JSON.parse(captured[0].data as string)).toEqual({
      month: MONTH,
      target_env: 'production',
      distribute: true,
      force: false,
    })
  })

  it('getDownloadUrl が target_env をクエリに載せる', async () => {
    await billingApi.getDownloadUrl(MONTH, 'production')
    expect(captured[0].method).toBe('get')
    expect(captured[0].url).toBe(`/runs/${MONTH}/download`)
    expect(captured[0].params).toEqual({ target_env: 'production' })
  })

  it('listAudits が GET /audits を呼ぶ', async () => {
    await billingApi.listAudits()
    expect(captured[0].method).toBe('get')
    expect(captured[0].url).toBe('/audits')
  })
})
