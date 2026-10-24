import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { fetchAuthSession } from 'aws-amplify/auth'
import { createAppApi } from '@/services/http'
import { adminUsersApi } from '../api'

vi.mock('@/services/http', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/services/http')>()
  return { ...actual, createAppApi: vi.fn(actual.createAppApi) }
})

const APP_ID = 'admin-users'
const SUB = '00000000-0000-0000-0000-000000000002'

// アプリの axios インスタンスは api.ts の読み込み時に 1 つだけ作られる
const api = vi.mocked(createAppApi).mock.results[0].value

let captured: InternalAxiosRequestConfig[] = []

beforeEach(() => {
  captured = []
  vi.mocked(fetchAuthSession).mockResolvedValue({} as Awaited<ReturnType<typeof fetchAuthSession>>)
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

describe('adminUsersApi', () => {
  it('baseURL が /api/admin-users', () => {
    expect(createAppApi).toHaveBeenCalledWith(APP_ID)
    expect(api.defaults.baseURL).toBe(`/api/${APP_ID}`)
  })

  it('listUsers が GET /users', async () => {
    await adminUsersApi.listUsers()

    expect(captured[0].method).toBe('get')
    expect(captured[0].url).toBe('/users')
  })

  it.each([
    [true],
    [false],
  ])('setAdmin が PUT /users/{sub}/admin に admin=%s を送る', async (admin) => {
    await adminUsersApi.setAdmin(SUB, admin)

    expect(captured[0].method).toBe('put')
    expect(captured[0].url).toBe(`/users/${SUB}/admin`)
    expect(JSON.parse(String(captured[0].data))).toEqual({ admin })
  })

  it('setEnabled が PUT /users/{sub}/enabled に enabled を送る', async () => {
    await adminUsersApi.setEnabled(SUB, false)

    expect(captured[0].method).toBe('put')
    expect(captured[0].url).toBe(`/users/${SUB}/enabled`)
    expect(JSON.parse(String(captured[0].data))).toEqual({ enabled: false })
  })

  it('resetPassword が POST /users/{sub}/password-reset', async () => {
    await adminUsersApi.resetPassword(SUB)

    expect(captured[0].method).toBe('post')
    expect(captured[0].url).toBe(`/users/${SUB}/password-reset`)
  })

  it('removeUser が DELETE /users/{sub}', async () => {
    await adminUsersApi.removeUser(SUB)

    expect(captured[0].method).toBe('delete')
    expect(captured[0].url).toBe(`/users/${SUB}`)
  })

  // sub は URL に埋めるためエスケープする（想定外の値でも別のパスへ飛ばさない）
  it('sub をパーセントエンコードする', async () => {
    await adminUsersApi.removeUser('a/b c')

    expect(captured[0].url).toBe('/users/a%2Fb%20c')
  })
})
