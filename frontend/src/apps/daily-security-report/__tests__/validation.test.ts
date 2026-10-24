import { describe, expect, it } from 'vitest'
import { HELP } from '../help'
import type { PromptPreset } from '../types'
import { emptySettings, validateAiSettings, validateSettings, exclusionExpired, parseMetricTargets, MENTION_LEVELS } from '../utils/settings'
import { makeSettings } from './fixtures'

describe('settings validation', () => {
  it('未取得のフォームも既定の通知先と管理 API のパスを持つ', () => {
    const defaults = emptySettings()
    expect(defaults.sections.s3).toBe(true)
    expect(defaults.mail_to).toHaveLength(1)
    expect(defaults.mail_to[0]).toMatch(/^[^@\s]+@example\.co\.jp$/)
    const adminPath = new RegExp(defaults.alarmhuba_admin_path_regex)
    expect(adminPath.test('/api/admin')).toBe(true)
    expect(adminPath.test('/api/admin/test')).toBe(true)
    expect(adminPath.test('/api/admin-extra')).toBe(false)
  })
  it('配布なしと保存済み DM を許す', () => {
    expect(validateSettings(makeSettings({ mail_to: [] }))).toEqual({})
    expect(validateSettings(makeSettings({ slack_enabled: true }))).toEqual({})
  })
  it('有効な配布先には宛先が必要', () => {
    const errors = validateSettings(makeSettings({ mail_enabled: true, mail_to: [], slack_enabled: true, slack_channel_id: '' }))
    expect(errors.mail_to).toContain('必要')
    expect(errors.slack_channel_id).toContain('必要')
  })
  it('配布 OFF でも社外アドレスと不正な ID は保存しない', () => {
    const errors = validateSettings(makeSettings({ mail_cc: ['tester@example.invalid'], slack_channel_id: '#test' }))
    expect(errors.mail_cc).toContain('@example.co.jp')
    expect(errors.slack_channel_id).toBeTruthy()
  })
  it('監視区分をすべて OFF にできない', () => {
    expect(validateSettings(makeSettings({ sections: { guardduty: false, cloudtrail: false, metrics: false, insights: false, instances: false, s3: false } })).sections).toBeTruthy()
  })
  it.each(['ratio', 'min_count', 'baseline_days', 'min_baseline_days', 'access_denied_min', 'login_failure_min', 'top_n', 'list_limit'] as const)('%s の下限を検証する', field => {
    expect(validateSettings(makeSettings({ [field]: 0 }))[field]).toBeTruthy()
  })
  it('倍率・整数・基準日数・明細行数の上限を検証する', () => {
    expect(validateSettings(makeSettings({ ratio: 1 })).ratio).toBeTruthy()
    expect(validateSettings(makeSettings({ min_count: 1.2 })).min_count).toBeTruthy()
    expect(validateSettings(makeSettings({ baseline_days: 31 })).baseline_days).toBeTruthy()
    expect(validateSettings(makeSettings({ min_baseline_days: 8 })).min_baseline_days).toBeTruthy()
    expect(validateSettings(makeSettings({ top_n: 21, list_limit: 21 }))).toMatchObject({ top_n: expect.any(String), list_limit: expect.any(String) })
  })
  it('除外の入力エラーに行番号と欄を付け、重複と件数を検証する', () => {
    const row = { kind: 'ip' as const, value: '192.0.2.1', scope: ['all' as const], category: '' as const, reason: '', expires_at: '2026-02-30' }
    const errors = validateSettings(makeSettings({ exclusions: [row, row] }))
    expect(errors['exclusions.0.reason']).toContain('1 行目')
    expect(errors['exclusions.0.expires_at']).toContain('期限')
    expect(errors['exclusions.1.value']).toContain('重複')
    expect(validateSettings(makeSettings({ exclusions: Array.from({ length: 201 }, () => row) })).exclusions).toContain('200')
  })
  it('同じ種類の空値や空白の行は重複より必須エラーを優先する', () => {
    const row = { kind: 'ip' as const, scope: ['all' as const], category: '' as const, reason: 'テスト', expires_at: '' }
    const errors = validateSettings(makeSettings({ exclusions: ['', ' ', ''].map(value => ({ ...row, value })) }))
    for (let index = 0; index < 3; index++) {
      expect(errors[`exclusions.${index}.value`]).toBe(`${index + 1} 行目: 値を入力してください`)
    }
  })
  it('空行を挟んでも非空値の重複を検証する', () => {
    const row = { kind: 'ip' as const, scope: ['all' as const], category: '' as const, reason: 'テスト', expires_at: '' }
    const errors = validateSettings(makeSettings({ exclusions: ['192.0.2.1', '', ' 192.0.2.1 '].map(value => ({ ...row, value })) }))
    expect(errors['exclusions.1.value']).toBe('2 行目: 値を入力してください')
    expect(errors['exclusions.2.value']).toBe('3 行目: 種類と値が重複しています')
  })
  it('期限当日は有効、翌日から期限切れ（JST）', () => {
    expect(exclusionExpired('', '2026-10-15')).toBe(false)
    expect(exclusionExpired('2026-10-15', '2026-10-15')).toBe(false)
    expect(exclusionExpired('2026-10-14', '2026-10-15')).toBe(true)
  })
  it('指標対象は JSON オブジェクトとして保持する', () => {
    expect(parseMetricTargets('{"waf":[{"name":"test"}]}')).toEqual({ waf: [{ name: 'test' }] })
    expect(() => parseMetricTargets('[]')).toThrow('JSON オブジェクト')
    expect(() => parseMetricTargets('null')).toThrow('JSON オブジェクト')
    expect(() => parseMetricTargets('{')).toThrow('JSON')
  })
})


describe('v2 settings validation', () => {
  it('AI と休眠の既定値を持つ', () => {
    expect(emptySettings()).toMatchObject({ ai_enabled: true, ai_model_family: 'opus', ai_model_pin: '', ai_prompt: '',
      dormant_days: 30, inventory_dormant_days: 90, inventory_weekday: 0, list_limit: 5 })
  })
  it.each(['opus', 'fable', 'sonnet', 'haiku'] as const)('系統 %s を許す', ai_model_family => {
    expect(validateSettings({ ...emptySettings(), ai_model_family })).toEqual({})
  })
  it('不正な系統とトグルを拒否する', () => {
    const form = { ...emptySettings(), ai_model_family: 'other', ai_enabled: 'true' } as unknown as ReturnType<typeof emptySettings>
    expect(validateSettings(form)).toMatchObject({ ai_model_family: expect.any(String), ai_enabled: expect.any(String) })
  })
  it.each(['', 'jp.anthropic.claude-opus-5-5', 'us.anthropic.claude-sonnet-5-5-v1:0', 'global.anthropic.claude-fable-5-1', 'apac.anthropic.claude-haiku-5-5'])('ピン %s を許す', ai_model_pin => {
    expect(validateSettings({ ...emptySettings(), ai_model_pin })).toEqual({})
  })
  it.each(['anthropic.claude-opus-5-5', 'jp.anthropic.claude-opus 5', 'jp.anthropic.claude-opus-5-5\n'])('不正なピン %s を拒否する', ai_model_pin => {
    expect(validateSettings({ ...emptySettings(), ai_model_pin }).ai_model_pin).toBeTruthy()
  })
  it('文面は Unicode の 8,000 字まで、8,001 字は拒否する', () => {
    expect(validateSettings({ ...emptySettings(), ai_prompt: '😀'.repeat(8000) }).ai_prompt).toBeUndefined()
    expect(validateSettings({ ...emptySettings(), ai_prompt: 'あ'.repeat(8001) }).ai_prompt).toBeTruthy()
  })
  it.each([
    ['dormant_days', 7, 365, 6], ['inventory_dormant_days', 30, 365, 29],
  ] as const)('%s の上下限と整数を検証する', (field, min, max, invalid) => {
    for (const value of [min, max]) expect(validateSettings({ ...emptySettings(), [field]: value })[field]).toBeUndefined()
    for (const value of [invalid, 366, 30.5, NaN, true, '30', '']) {
      expect(validateSettings({ ...emptySettings(), [field]: value })[field]).toBeTruthy()
    }
  })
  it('棚卸しの曜日は null か 0〜6 の整数だけを許す', () => {
    for (const value of [null, 0, 6]) expect(validateSettings({ ...emptySettings(), inventory_weekday: value }).inventory_weekday).toBeUndefined()
    for (const value of [-1, 7, 1.5, true, '', '0']) {
      expect(validateSettings({ ...emptySettings(), inventory_weekday: value } as unknown as ReturnType<typeof emptySettings>).inventory_weekday).toBeTruthy()
    }
  })
})

describe('メンションの段階と名前付きプロンプトの検証', () => {
  const card = (index: number, overrides: Partial<PromptPreset> = {}): PromptPreset => ({
    id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`, name: `案 ${index}`, prompt: `本文 ${index}`,
    updated_at: '2026-10-16T04:00:00.000Z', ...overrides,
  })
  it('既定はメンション Critical のみ・カードなし', () => {
    expect(emptySettings()).toMatchObject({ slack_mention_level: 'critical', ai_prompt_presets: [] })
  })
  it('メンションの段階は 3 択', () => {
    expect(MENTION_LEVELS).toEqual([
      { title: 'Critical のみ', value: 'critical' },
      { title: 'Warning 以上', value: 'warning' },
      { title: '常に（OK の日も）', value: 'always' },
    ])
    for (const level of ['critical', 'warning', 'always'] as const) {
      expect(validateSettings({ ...emptySettings(), slack_mention_level: level }).slack_mention_level).toBeUndefined()
    }
    for (const level of ['', 'ok', 'Critical', null]) {
      const form = { ...emptySettings(), slack_mention_level: level } as unknown as ReturnType<typeof emptySettings>
      expect(validateSettings(form).slack_mention_level).toBeTruthy()
    }
  })
  it('20 件までの正しいカードを許す', () => {
    const presets = Array.from({ length: 20 }, (_, index) => card(index + 1))
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: presets })).toEqual({})
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: [card(1, { name: ` ${'名'.repeat(40)} `, prompt: '😀'.repeat(8000) })] })).toEqual({})
  })
  it('21 件は拒否する', () => {
    const presets = Array.from({ length: 21 }, (_, index) => card(index + 1))
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: presets }).ai_prompt_presets).toBe('名前付きプロンプトは 20 件までです')
  })
  it.each([
    ['', '3 件目の名前は 1〜40 字にしてください'],
    ['   ', '3 件目の名前は 1〜40 字にしてください'],
    ['名'.repeat(41), '3 件目の名前は 1〜40 字にしてください'],
  ])('名前 %j を件数付きの 1 文で拒否する', (name, message) => {
    const presets = [card(1), card(2), card(3, { name })]
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: presets }).ai_prompt_presets).toBe(message)
  })
  it.each([
    ['', '2 件目の本文は 1〜8,000 字にしてください'],
    [' \n ', '2 件目の本文は 1〜8,000 字にしてください'],
    ['あ'.repeat(8001), '2 件目の本文は 1〜8,000 字にしてください'],
  ])('本文 %# を件数付きの 1 文で拒否する', (prompt, message) => {
    const presets = [card(1), card(2, { prompt })]
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: presets }).ai_prompt_presets).toBe(message)
  })
  it('trim 後に同じ名前を重複として拒否する', () => {
    const presets = [card(1, { name: '本番用' }), card(2, { name: ' 本番用 ' })]
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: presets }).ai_prompt_presets).toBe('2 件目の名前「本番用」は 1 件目と重複しています')
  })
  it('本文の合計（trim 後、code point）は 20,000 字まで', () => {
    const exact = [card(1, { prompt: 'あ'.repeat(8000) }), card(2, { prompt: ` ${'😀'.repeat(8000)}\n` }), card(3, { prompt: 'い'.repeat(4000) })]
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: exact })).toEqual({})
    const over = [...exact.slice(0, 2), card(3, { prompt: 'い'.repeat(4001) })]
    const message = '名前付きプロンプトの本文の合計は 20,000 字までです（現在 20,001 字）'
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: over }).ai_prompt_presets).toBe(message)
    expect(validateAiSettings({ ...emptySettings(), ai_prompt_presets: over }).ai_prompt_presets).toBe(message)
  })
  it('1 件ごとの誤りを合計より先に返す', () => {
    const presets = [card(1, { prompt: 'あ'.repeat(8000) }), card(2, { prompt: 'あ'.repeat(8000) }), card(3, { prompt: 'あ'.repeat(8001) })]
    expect(validateSettings({ ...emptySettings(), ai_prompt_presets: presets }).ai_prompt_presets).toBe('3 件目の本文は 1〜8,000 字にしてください')
  })
  it('AI の検証にもカードを含める（試行の可否に使う）', () => {
    const form = { ...emptySettings(), ai_prompt_presets: [card(1, { name: '' })] }
    expect(validateAiSettings(form).ai_prompt_presets).toBe('1 件目の名前は 1〜40 字にしてください')
  })
  it('ヘルプのメンションは設定した段階に従う', () => {
    expect(HELP.slackMention).toContain('設定した段階以上の日と即時通知に付け、メールには付けません')
    expect(HELP.slackMention).not.toContain('Warning 以上の日と')
    expect(HELP.slackMentionLevel).toContain('即時通知')
    for (const key of ['ai_prompt', 'ai_prompt_fixed', 'ai_prompt_presets'] as const) expect(HELP[key]).toBeTruthy()
    expect(HELP.ai_prompt_presets).toContain('本文の合計は 20,000 字まで')
  })
})
