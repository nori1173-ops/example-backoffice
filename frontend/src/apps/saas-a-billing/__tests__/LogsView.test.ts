import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import type { AuditEntry, AuditTarget } from '../types'
import LogsView from '../views/LogsView.vue'

interface LogsStoreMock {
  entries: AuditEntry[]
  filtered: AuditEntry[]
  loading: boolean
  error: string | null
  range: string
  target: '' | AuditTarget
  query: string
  fetch: Mock
}

const holder = vi.hoisted(() => ({ logs: null as unknown as LogsStoreMock }))

vi.mock('../stores/logs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../stores/logs')>()
  const { reactive } = await import('vue')
  holder.logs = reactive({
    entries: [],
    filtered: [],
    loading: false,
    error: null,
    range: '30d',
    target: '',
    query: '',
    fetch: vi.fn(),
  }) as LogsStoreMock
  return { ...actual, useLogsStore: () => holder.logs }
})

/** onActivated は KeepAlive 配下でのみ呼ばれるため、実際の配置に合わせて包む */
const Host = { components: { LogsView }, template: '<KeepAlive><LogsView /></KeepAlive>' }

function makeAudit(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return {
    target: 'settings',
    actor: 'sub',
    actor_email: 'devA@example.co.jp',
    before: { cutoff_day: 20 },
    after: { cutoff_day: 25 },
    at: '2026-11-03T10:05:07+09:00',
    ...overrides,
  }
}

function makeRunAudit(overrides: Partial<AuditEntry> = {}): AuditEntry {
  return makeAudit({
    target: 'run',
    before: null,
    after: { month: '2026-10', target_env: 'production', distribute: true, force: true },
    at: '2026-11-04T09:00:00+09:00',
    ...overrides,
  })
}

function setEntries(entries: AuditEntry[]): void {
  holder.logs.entries = entries
  holder.logs.filtered = entries
}

let mounted: VueWrapper | null = null

/** ストアのモックはファイル全体で 1 つなので、残した画面の watch が次のテストに漏れる */
async function mountView(): Promise<VueWrapper> {
  const vuetify = createVuetify({ components, directives })
  mounted = mount(Host, { global: { plugins: [vuetify] } }) as unknown as VueWrapper
  await flushPromises()
  return mounted
}

beforeEach(() => {
  document.body.innerHTML = ''
  setActivePinia(createPinia())
  setEntries([])
  holder.logs.loading = false
  holder.logs.error = null
  holder.logs.range = '30d'
  holder.logs.target = ''
  holder.logs.query = ''
  holder.logs.fetch.mockReset()
})

afterEach(() => {
  mounted?.unmount()
  mounted = null
  vi.useRealTimers()
})

describe('LogsView', () => {
  it('画面に入ると操作ログを取りに行く', async () => {
    await mountView()

    expect(holder.logs.fetch).toHaveBeenCalledTimes(1)
  })

  it('行に日時・種別・実行者・内容が出る', async () => {
    setEntries([makeRunAudit(), makeAudit(), makeAudit({ target: 'price' })])

    const wrapper = await mountView()

    const rows = wrapper.findAll('[data-test="logs-row"]')
    expect(rows).toHaveLength(3)
    expect(rows[0].text()).toContain('2026-11-04 09:00:00')
    expect(rows[0].text()).toContain('手動実行')
    expect(rows[0].text()).toContain('devA@example.co.jp')
    expect(rows[0].text()).toContain('2026-10 本番環境 配布あり 強制')
    expect(rows[1].text()).toContain('設定')
    expect(rows[1].text()).toContain('締め日')
    expect(rows[2].text()).toContain('単価表')
  })

  // 単価表の監査は before が空で after の全キーが「変わった項目」になる（UI-17）
  it('単価表の内容に英語の生キーを出さない', async () => {
    setEntries([
      makeAudit({
        target: 'price',
        before: {},
        after: { effective_from: '2026-10-01', created_by_email: 'devA@example.co.jp' },
      }),
    ])

    const wrapper = await mountView()

    const row = wrapper.find('[data-test="logs-row"]').text()
    expect(row).toContain('適用開始日')
    expect(row).toContain('登録者メール')
    expect(row).not.toContain('created_by_email')
  })

  it('system の実行はシステムと表示する', async () => {
    setEntries([makeAudit({ actor: 'system', actor_email: '' })])

    const wrapper = await mountView()

    expect(wrapper.find('[data-test="logs-row"]').text()).toContain('システム')
  })

  it('0 件は案内を出す', async () => {
    const wrapper = await mountView()

    expect(wrapper.findAll('[data-test="logs-row"]')).toHaveLength(0)
    expect(wrapper.find('[data-test="logs-empty"]').text()).toContain('該当する操作ログがありません')
  })

  it('取得に失敗するとエラーを出す', async () => {
    holder.logs.error = '操作ログの取得に失敗しました'

    const wrapper = await mountView()

    expect(wrapper.find('[data-test="logs-error"]').text()).toContain('操作ログの取得に失敗しました')
  })

  it('詳細ボタンで before / after の JSON を出す', async () => {
    setEntries([makeRunAudit(), makeAudit()])
    const wrapper = await mountView()

    await wrapper.find('[data-test="logs-detail-0"]').trigger('click')
    await flushPromises()

    const dialog = document.body.querySelector('[data-test="logs-detail-dialog"]')
    expect(dialog?.textContent).toContain('"month": "2026-10"')
    expect(dialog?.querySelector('[data-test="logs-detail-before"]')?.textContent?.trim()).toBe('—')
  })

  it('検索はデバウンスしてからストアへ渡す', async () => {
    const wrapper = await mountView()
    vi.useFakeTimers()

    await wrapper.find('[data-test="logs-search"] input').setValue('colleagued')
    expect(holder.logs.query).toBe('')

    vi.advanceTimersByTime(300)
    await flushPromises()

    expect(holder.logs.query).toBe('colleagued')
  })

  it('期間と種別を変えると取り直す', async () => {
    await mountView()

    holder.logs.range = '7d'
    await flushPromises()
    expect(holder.logs.fetch).toHaveBeenCalledTimes(2)

    holder.logs.target = 'run'
    await flushPromises()
    expect(holder.logs.fetch).toHaveBeenCalledTimes(3)
  })

  it('更新ボタンで取り直す', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-test="logs-refresh"]').trigger('click')
    await flushPromises()

    expect(holder.logs.fetch).toHaveBeenCalledTimes(2)
  })

  it('記録対象の注記を出す', async () => {
    const wrapper = await mountView()

    const note = wrapper.find('[data-test="logs-note"]').text()
    expect(note).toContain('スケジュール実行は実行履歴タブ')
    expect(note).toContain('JST')
  })
})
