import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createVuetify } from 'vuetify'
import * as components from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { useUiStore } from '@/stores/ui'
import RunDialog from '../components/RunDialog.vue'
import type { RunSummary, SettingsView } from '../types'

interface RunsStoreMock {
  runs: RunSummary[]
  loading: boolean
  error: string | null
  downloading: string | null
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
    downloading: null,
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
  setActivePinia(createPinia())
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 1, 9, 0, 0))
  holder.runs.createRun.mockReset().mockResolvedValue({
    month: '2026-08',
    target_env: 'verification',
    status: 'queued',
    attempt: 1,
    execution_arn: 'arn:aws:states:ap-northeast-1:000000000000:execution:x',
  })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('RunDialog', () => {
  // 集計対象環境はサイト固定（設計 R1 / UI-08）。画面から切り替えられないことを保証する
  it('環境の選択操作が無い', async () => {
    await mountDialog()

    expect(el('run-env')).toBeNull()
    expect(el('run-env-verification')).toBeNull()
    expect(el('run-env-production')).toBeNull()
  })

  it('検証環境ではオレンジの固定環境チップが出る', async () => {
    await mountDialog()

    const chip = el('run-fixed-env')!
    expect(chip.textContent).toContain('検証環境の SaaS_A を集計')
    expect(chip.classList).toContain('bg-orange')
  })

  it('本番では緑の固定環境チップが出る', async () => {
    await mountDialog(makeSettings({ target_env: 'production' }))

    const chip = el('run-fixed-env')!
    expect(chip.textContent).toContain('本番 SaaS_A を集計（実データ）')
    expect(chip.classList).toContain('bg-green')
  })

  it('固定環境の接続情報が未登録なら確認へ進めない', async () => {
    await mountDialog(makeSettings({ targets: {} }))

    expect(el('run-next')!.hasAttribute('disabled')).toBe(true)
    expect(el('run-target-hint')!.textContent).toContain('接続情報（SSM）が未登録')
  })

  // A1 以降 `targets` は固定環境のキーだけを返す（設計 R1）
  it('targets が固定環境のキーだけでも確認へ進める', async () => {
    await mountDialog(makeSettings({ targets: { verification: true } }))

    expect(el('run-next')!.hasAttribute('disabled')).toBe(false)
    expect(el('run-target-hint')).toBeNull()
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

  // 配布 ON は宛先を読んだ確認を挟む（設計 R6 / UI-07）
  it('配布 ON では宛先の確認チェックまで実行できない', async () => {
    await mountDialog()

    await toggle('run-distribute')
    await click('run-next')

    expect(el('run-submit')!.hasAttribute('disabled')).toBe(true)

    await toggle('distribute-ack')

    expect(el('run-submit')!.hasAttribute('disabled')).toBe(false)
  })

  it('配布 OFF なら確認チェックは出ない', async () => {
    await mountDialog()

    await click('run-next')

    expect(el('distribute-ack')).toBeNull()
    expect(el('run-submit')!.hasAttribute('disabled')).toBe(false)
  })

  it('本番へ配布するときは実行ボタンを警告色にする', async () => {
    await mountDialog(makeSettings({ target_env: 'production' }))

    await toggle('run-distribute')
    await click('run-next')
    await toggle('distribute-ack')

    expect(el('run-submit')!.className).toContain('bg-error')
    expect(el('run-submit')!.textContent).toContain('本番へ配布して実行')
  })

  it('実行を受け付けたらスナックバーで知らせる', async () => {
    await mountDialog()

    await click('run-next')
    await click('run-submit')

    expect(useUiStore().snackbar.text).toContain('実行を受け付けました')
  })

  it('実行で createRun が呼ばれる', async () => {
    const wrapper = await mountDialog()

    await click('run-next')
    expect(holder.runs.createRun).not.toHaveBeenCalled()

    await click('run-submit')

    expect(holder.runs.createRun).toHaveBeenCalledWith({
      month: '2026-08',
      distribute: false,
      force: false,
    })
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual([false])
  })
})
