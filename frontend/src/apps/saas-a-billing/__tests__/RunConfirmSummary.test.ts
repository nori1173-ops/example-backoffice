import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RunConfirmSummary from '../components/RunConfirmSummary.vue'
import { FIELD_LABELS, changedKeys } from '../utils/audit'
import type { SettingsView } from '../types'

function settings(value?: 'billing_only' | 'all'): SettingsView {
  const config: SettingsView = {
    target_env: 'verification',
    cutoff_day: 20,
    cutoff_time: '24:00',
    auto_run_enabled: false,
    auto_run_from_month: '2026-10',
    mail_enabled: true,
    mail_to: ['devA@example.co.jp'],
    mail_cc: [],
    mail_attachment_sheets: value ?? 'billing_only',
    slack_enabled: false,
    slack_channel_id: '',
    slack_channel_label: '',
    slack_mention: '',
    slack_template: '{month}',
    exclude_keywords: [],
    camera_format_names: [],
    version: 0,
    updated_at: '2026-10-01',
    updated_by: 'sub',
    updated_by_email: 'devA@example.co.jp',
    saasa_host: null,
    targets: { verification: true },
    slack_mode: null,
  }
  if (value === undefined) {
    Reflect.deleteProperty(config, 'mail_attachment_sheets')
  }
  return config
}

function summary(config: SettingsView, distribute = true) {
  return mount(RunConfirmSummary, {
    props: { settings: config, month: '2026-10', distribute },
    global: { stubs: { EnvBadge: true } },
  })
}

describe('メール添付の実行確認と監査表示', () => {
  it.each([
    [undefined, 'シート 1 のみ'], ['billing_only', 'シート 1 のみ'], ['all', '両方のシート'],
  ] as const)('%s のメール配布の要約', (value, label) => {
    expect(summary(settings(value)).text()).toContain(`添付: ${label}`)
  })

  it.each([null, '', 'billing-only', 'BILLING_ONLY', 123])(
    '不正な値 %s は添付をシート 1 のみと表示する',
    (value) => {
      const invalid = settings('billing_only')
      Reflect.set(invalid, 'mail_attachment_sheets', value)
      const text = summary(invalid).text()

      expect(text).toContain('添付: シート 1 のみ')
      expect(text).not.toContain('添付: 両方のシート')
    },
  )

  it('メール無効・配布なしなら添付の要約を表示しない', () => {
    expect(summary({ ...settings('all'), mail_enabled: false }).text()).not.toContain('添付:')
    expect(summary(settings('all'), false).text()).not.toContain('添付:')
  })

  it('監査に設定画面と同じラベルを表示する', () => {
    expect(FIELD_LABELS.mail_attachment_sheets).toBe('メールに添付する Excel')
    expect(changedKeys({
      target: 'settings', actor: 'sub', actor_email: 'devA@example.co.jp', at: '2026-10-01',
      before: { mail_attachment_sheets: 'billing_only' }, after: { mail_attachment_sheets: 'all' },
    })).toBe('メールに添付する Excel')
  })
})
