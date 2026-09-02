import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import RunDialog from '../components/RunDialog.vue'
import type { RunSummary, SettingsView, TargetEnv } from '../types'

interface RunsStoreMock {
  runs: RunSummary[]
  loading: boolean
  error: string | null
  targetEnvFilter: TargetEnv | null
  fetchRuns: Mock
  download: Mock
  createRun: Mock
  pollRun: Mock
}

const holder = vi.hoisted(() => ({ runs: null as unknown as RunsStoreMock }))

vi.mock('../stores/runs', async () => {
  const { reactive } = await import('vue')
  holder.runs = reactive({
    runs: [],
    loading: false,
    error: null,
    targetEnvFilter: null,
    fetchRuns: vi.fn(),
    download: vi.fn(),
    createRun: vi.fn(),
    pollRun: vi.fn(),
  }) as RunsStoreMock
  return { useRunsStore: () => holder.runs }
})

function makeSettings(overrides: Partial<SettingsView> = {}): SettingsView {
  return {
    target_env: 'verification',
    cutoff_day: 20,
    cutoff_time: '24:00',
    auto_retry_days: 5,
    auto_run_enabled: false,
    auto_run_from_month: '2026-01',
    mail_enabled: true,
    slack_enabled: true,
    mail_to: ['devA@example.co.jp'],
    mail_cc: ['cc@example.co.jp'],
    slack_channel_id: 'C0123ABCD',
    slack_channel_label: '#proj-saas-a-billing',
    slack_mention: '',
    slack_template: '{month} のレポートです',
    exclude_keywords: ['デモ'],
    camera_format_names: [],
    version: 3,
    updated_at: '2026-08-01T10:00:00+09:00',
    updated_by: 'sub',
    updated_by_email: 'devA@example.co.jp',
    saasa_host: 'example.invalid',
    targets: { verification: true, production: true },
    slack_mode: 'bot',
    ...overrides,
  }
}

async function mountDialog(settings: SettingsView | null = makeSettings()): Promise<VueWrapper> {
  const vuetify = createVuetify({ components, directives })
  const wrapper = mount(RunDialog, {
    props: { modelValue: true, settings },
    global: { plugins: [vuetify] },
  })
  await flushPromises()
  return wrapper
}

function el(testId: string): HTMLElement | null {
  return document.body.querySelector(`[data-test="${testId}"]`)
}

function field(testId: string): HTMLInputElement {
  return document.body.querySelector(`[data-test="${testId}"] input`) as HTMLInputElement
}

async function click(testId: string): Promise<void> {
  el(testId)!.click()
  await flushPromises()
}

async function toggle(testId: string): Promise<void> {
  field(testId).click()
  await flushPromises()
}

beforeEach(() => {
  document.body.innerHTML = ''
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 1, 9, 0, 0))
  holder.runs.createRun.mockReset()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RunDialog', () => {
  it('既定の集計対象環境が設定値', async () => {
    await mountDialog(makeSettings({ target_env: 'production' }))

    expect(el('run-env-production')!.classList).toContain('v-btn--active')
    expect(el('run-env-verification')!.classList).not.toContain('v-btn--active')
  })

  it('SSM 未登録の環境が選択できない', async () => {
    await mountDialog(makeSettings({ targets: { verification: true, production: false } }))

    expect(el('run-env-production')!.hasAttribute('disabled')).toBe(true)
    expect(el('run-env-verification')!.hasAttribute('disabled')).toBe(false)
  })

  it('既定の対象月が直近の締め月', async () => {
    await mountDialog()

    expect(field('run-month').value).toBe('2026-08')
  })

  it('force を選ぶと警告が出る', async () => {
    await mountDialog()
    expect(el('force-warning')).toBeNull()

    await toggle('run-force')

    expect(el('force-warning')).not.toBeNull()
    expect(el('force-warning')!.textContent).toContain('二重')
  })

  it('配布先にメールと Slack の両方が出る', async () => {
    await mountDialog()

    expect(el('distribute-targets')!.textContent).toContain('配布先: メール・Slack')
  })

  it('メール配布だけ有効なら配布先はメールのみ', async () => {
    await mountDialog(makeSettings({ slack_enabled: false }))

    expect(el('distribute-targets')!.textContent).toContain('配布先: メール')
    expect(el('distribute-targets')!.textContent).not.toContain('Slack')
  })

  it('両方無効なら配布先なしと表示し、配布チェックは操作できる', async () => {
    await mountDialog(makeSettings({ mail_enabled: false, slack_enabled: false }))

    expect(el('distribute-targets')!.textContent).toContain('配布先: なし（設定で配布が無効）')
    expect(field('run-distribute').hasAttribute('disabled')).toBe(false)
  })

  it('確認ダイアログに集計対象環境と宛先が出る', async () => {
    await mountDialog()

    await toggle('run-distribute')
    await click('run-next')

    const confirm = el('run-confirm')!
    expect(confirm.textContent).toContain('検証環境')
    expect(confirm.textContent).toContain('devA@example.co.jp')
    expect(confirm.textContent).toContain('cc@example.co.jp')
    expect(confirm.textContent).toContain('#proj-saas-a-billing')
  })

  it('distribute=false のとき宛先を表示しない', async () => {
    await mountDialog()

    await click('run-next')

    const confirm = el('run-confirm')!
    expect(confirm.textContent).toContain('検証環境')
    expect(confirm.textContent).toContain('配布しません')
    expect(confirm.textContent).not.toContain('devA@example.co.jp')
    expect(confirm.textContent).not.toContain('#proj-saas-a-billing')
  })

  it('実行で createRun が呼ばれる', async () => {
    const wrapper = await mountDialog()

    await click('run-next')
    expect(holder.runs.createRun).not.toHaveBeenCalled()

    await click('run-submit')

    expect(holder.runs.createRun).toHaveBeenCalledWith({
      month: '2026-08',
      targetEnv: 'verification',
      distribute: false,
      force: false,
    })
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
  })
})
