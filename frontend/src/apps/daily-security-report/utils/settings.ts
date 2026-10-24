import type { EditableSettings, MetricTargets, PromptPreset } from '../types'
import { charCount, formatChars, PRESET_LIMIT, PRESET_NAME_MAX_CHARS, PRESET_TOTAL_MAX_CHARS, presetTotalChars, PROMPT_MAX_CHARS } from './prompt'

export const SECTION_FIELDS = [
  { key: 'guardduty', label: 'GuardDuty', hint: '読み取りは無料。GuardDuty 自体の料金はアカウント側で、ここを OFF にしても止まりません。' },
  { key: 'cloudtrail', label: 'CloudTrail', hint: 'ほぼ無料' },
  { key: 'metrics', label: '入口の指標', hint: 'ほぼ無料' },
  { key: 'insights', label: 'Logs Insights', hint: '走査量課金。月 約 $5〜14（COST-ESTIMATE 5.3）' },
  { key: 'instances', label: 'インスタンス', hint: 'ほぼ無料' },
  { key: 's3', label: 'S3', hint: '取得費 月約 $0.05。要求メトリクスの追加は別途' },
] as const
export const THRESHOLD_FIELDS = [
  { key: 'ratio', label: '比較の倍率', min: 1, step: 0.1 },
  { key: 'min_count', label: '倍率判定の最小件数', min: 1, step: 1 },
  { key: 'baseline_days', label: '比較対象の日数', min: 1, max: 30, step: 1 },
  { key: 'min_baseline_days', label: '基準に必要な日数', min: 1, max: 30, step: 1 },
  { key: 'access_denied_min', label: 'AccessDenied の最小件数', min: 1, step: 1 },
  { key: 'login_failure_min', label: 'ログイン失敗の最小回数', min: 1, step: 1 },
  { key: 'top_n', label: 'GuardDuty の上位タイトル数', min: 1, max: 20, step: 1 },
  { key: 'list_limit', label: '通知の区分ごとの所見行数', min: 1, max: 20, step: 1 },
  { key: 'dormant_days', label: '休眠主体の判定日数', min: 7, max: 365, step: 1 },
  { key: 'inventory_dormant_days', label: '棚卸しの未使用日数', min: 30, max: 365, step: 1 },
] as const

export const MODEL_FAMILIES = [
  { title: 'Claude Opus（jp、国内完結、月 約 $5.3）', value: 'opus' },
  { title: 'Claude Fable（global、月 約 $12、aws_review のオプトインが必要）', value: 'fable' },
  { title: 'Claude Sonnet（global、月 約 $2.64）', value: 'sonnet' },
  { title: 'Claude Haiku（jp、月 約 $0.13）', value: 'haiku' },
] as const

export const MENTION_LEVELS = [
  { title: 'Critical のみ', value: 'critical' },
  { title: 'Warning 以上', value: 'warning' },
  { title: '常に（OK の日も）', value: 'always' },
] as const

export function presetsError(presets: readonly PromptPreset[]): string {
  if (!Array.isArray(presets)) return '名前付きプロンプトの形式が不正です'
  if (presets.length > PRESET_LIMIT) return `名前付きプロンプトは ${PRESET_LIMIT} 件までです`
  const seen = new Map<string, number>()
  for (const [index, preset] of presets.entries()) {
    const name = typeof preset?.name === 'string' ? preset.name.trim() : ''
    const nameChars = charCount(name)
    if (nameChars < 1 || nameChars > PRESET_NAME_MAX_CHARS) return `${index + 1} 件目の名前は 1〜${PRESET_NAME_MAX_CHARS} 字にしてください`
    const first = seen.get(name)
    if (first !== undefined) return `${index + 1} 件目の名前「${name}」は ${first + 1} 件目と重複しています`
    seen.set(name, index)
    const promptChars = typeof preset.prompt === 'string' ? charCount(preset.prompt.trim()) : 0
    if (promptChars < 1 || promptChars > PROMPT_MAX_CHARS) return `${index + 1} 件目の本文は 1〜8,000 字にしてください`
  }
  const total = presetTotalChars(presets)
  if (total > PRESET_TOTAL_MAX_CHARS) {
    return `名前付きプロンプトの本文の合計は ${formatChars(PRESET_TOTAL_MAX_CHARS)} 字までです（現在 ${formatChars(total)} 字）`
  }
  return ''
}

export function validateAiSettings(form: Pick<EditableSettings, 'ai_model_family' | 'ai_model_pin' | 'ai_prompt' | 'ai_prompt_presets'>): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!MODEL_FAMILIES.some(model => model.value === form.ai_model_family)) errors.ai_model_family = 'AI の系統を選んでください'
  if (typeof form.ai_model_pin !== 'string' || (form.ai_model_pin !== ''
    && (!/^(jp|apac|global|us)\.anthropic\.claude-[a-z0-9.-]+(:\d+)?$/.test(form.ai_model_pin) || /\s/.test(form.ai_model_pin)))) {
    errors.ai_model_pin = '有効な推論プロファイル ID を指定してください（空欄は自動）'
  }
  if (typeof form.ai_prompt !== 'string' || charCount(form.ai_prompt) > PROMPT_MAX_CHARS) errors.ai_prompt = 'プロンプトは 8,000 字以内にしてください'
  const presets = presetsError(form.ai_prompt_presets)
  if (presets) errors.ai_prompt_presets = presets
  return errors
}

export function emptySettings(): EditableSettings {
  return {
    ai_enabled: true, ai_model_family: 'opus', ai_model_pin: '', ai_prompt: '', ai_prompt_presets: [],
    dormant_days: 30, inventory_dormant_days: 90, inventory_weekday: 0,
    auto_run_enabled: false, alert_enabled: false, mail_enabled: false, slack_enabled: false,
    mail_to: ['devA@example.co.jp'], mail_cc: [], slack_channel_id: '', slack_channel_label: '', slack_mention: '',
    slack_mention_level: 'critical',
    sections: { guardduty: true, cloudtrail: true, metrics: true, insights: true, instances: true, s3: true },
    ratio: 3, min_count: 10, baseline_days: 7, min_baseline_days: 3,
    access_denied_min: 20, login_failure_min: 3, top_n: 3, list_limit: 5,
    metric_targets: {}, alarmhuba_admin_path_regex: '^/api/admin(?:/|$)', exclusions: [],
  }
}

export function parseMetricTargets(text: string): MetricTargets {
  let value: unknown
  try {
    value = JSON.parse(text)
  } catch {
    throw new Error('指標対象は有効な JSON オブジェクトで指定してください')
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('指標対象は JSON オブジェクトで指定してください')
  }
  return value as MetricTargets
}

export function exclusionExpired(expiresAt: string, today?: string): boolean {
  const jstDate = today ?? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Tokyo' }).format(new Date())
  return !!expiresAt && expiresAt < jstDate
}

export function validateSettings(form: EditableSettings): Record<string, string> {
  const errors = validateAiSettings(form)
  if (typeof form.ai_enabled !== 'boolean') errors.ai_enabled = 'AI 判定の有効・無効を指定してください'
  if (form.inventory_weekday !== null && (typeof form.inventory_weekday !== 'number'
    || !Number.isInteger(form.inventory_weekday) || form.inventory_weekday < 0 || form.inventory_weekday > 6)) {
    errors.inventory_weekday = '棚卸しの曜日は月〜日または無効を選んでください'
  }
  for (const key of ['mail_to', 'mail_cc'] as const) {
    if (form[key].some(address => !/^[^@\s]+@example\.co\.jp$/i.test(address))) {
      errors[key] = 'メール形式の @example.co.jp アドレスだけを指定してください'
    }
  }
  if (form.mail_enabled && !form.mail_to.length) errors.mail_to = 'メール配布には To の宛先が必要です'
  if (form.slack_enabled && !form.slack_channel_id) errors.slack_channel_id = 'Slack 配布にはチャンネルが必要です'
  if (!MENTION_LEVELS.some(level => level.value === form.slack_mention_level)) {
    errors.slack_mention_level = 'メンションを付ける段階を選んでください'
  }
  if (form.slack_channel_id && !/^[CGD][A-Z0-9]{8,}$/.test(form.slack_channel_id)) {
    errors.slack_channel_id = '保存済みのチャンネルの形式が不正です。候補から選び直してください'
  }
  if (!Object.values(form.sections).some(Boolean)) errors.sections = '監視する区分を 1 つ以上有効にしてください'
  for (const field of THRESHOLD_FIELDS) {
    const value = form[field.key]
    if (!Number.isFinite(value) || (field.key === 'ratio'
      ? value <= 1
      : !Number.isInteger(value) || value < field.min || ('max' in field && value > field.max))) {
      errors[field.key] = field.key === 'ratio' ? '1 より大きい数を指定してください'
        : `${field.min}${'max' in field ? `〜${field.max}` : ' 以上'}の整数を指定してください`
    }
  }
  if (Number(form.min_baseline_days) > Number(form.baseline_days)) {
    errors.min_baseline_days = '基準に必要な日数は比較対象の日数以下にしてください'
  }
  if (form.exclusions.length > 200) errors.exclusions = '除外リストは 200 件までです'
  const seen = new Set<string>()
  form.exclusions.forEach((row, index) => {
    const add = (field: string, message: string) => { errors[`exclusions.${index}.${field}`] = `${index + 1} 行目: ${message}` }
    if (!row.value.trim()) add('value', '値を入力してください')
    if (!row.reason.trim()) add('reason', '理由を入力してください')
    if (!row.scope.length) add('scope', '適用先を 1 つ以上選んでください')
    const key = `${row.kind}:${row.value.trim()}`
    if (row.value.trim()) {
      if (seen.has(key)) add('value', '種類と値が重複しています')
      seen.add(key)
    }
    if (row.expires_at && (!/^\d{4}-\d{2}-\d{2}$/.test(row.expires_at)
      || Number.isNaN(Date.parse(row.expires_at))
      || new Date(row.expires_at).toISOString().slice(0, 10) !== row.expires_at)) {
      add('expires_at', '期限を YYYY-MM-DD の日付で指定してください')
    }
  })
  return errors
}
