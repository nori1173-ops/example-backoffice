import { DASH, formatCount, toWallClock } from '@/utils/format'
import type { Comparison, Finding, Glossary, JsonValue, Section } from '../types'

export const TABLE_LABELS: Record<string, string> = {
  human_operations: '人の操作', config_changes: '構成変更', console_logins: 'コンソールログイン', access_denied: 'AccessDenied',
  root: 'root の利用', iam_changes: 'IAM の変更', sg_changes: 'セキュリティグループの変更', countries: '国別件数',
  alarmhuba_admin: '社外からの管理 API', alarmhuba_denied: '拒否された操作', admin_external: '社外からの管理 API', direct: '直接 URL 到達',
  denied: '拒否された操作', gps: 'GPS の受信元', findings: 'GuardDuty の検出（明細）',
  ec2: 'EC2', lightsail: 'Lightsail', new: '新しく現れた台', gone: '消えた台',
  guardduty: 'GuardDuty の検出（上位）', access_denied_principals: 'AccessDenied の主体（上位）', console_login_failures: 'コンソールログイン失敗（上位）',
  waf_countries: 'WAF 国別（上位）', waf_vulnerabilities: 'WAF 脆弱性分類（上位）', waf_rules: 'WAF ルール（上位）', waf_dimensions: 'WAF 内訳（上位）',
  lambda_errors: 'Lambda エラーの関数（上位）', alarmhuba_statuses: 'alarmhuba 状態コード', alarmhuba_ips: 'alarmhuba の上位 IP',
  alarmhuba_countries: 'alarmhuba 国別（上位）', gps_ips: 'GPS の受信元 IP（上位）',
}

export const TOP_SECTIONS: Record<string, Section> = {
  guardduty: 'guardduty',
  access_denied_principals: 'cloudtrail', console_login_failures: 'cloudtrail',
  waf_countries: 'metrics', waf_vulnerabilities: 'metrics', waf_rules: 'metrics', waf_dimensions: 'metrics', lambda_errors: 'metrics',
  alarmhuba_statuses: 'insights', alarmhuba_ips: 'insights', alarmhuba_countries: 'insights', gps_ips: 'insights',
}

export function counterSection(key: string): Section | null {
  if (/^(waf|apigw|alb|cloudfront|lambda)\./.test(key)) return 'metrics'
  if (/^(alarmhuba|gps)\./.test(key)) return 'insights'
  const section = (['guardduty', 'cloudtrail', 'instances', 's3'] as const).find(name => key.startsWith(`${name}.`))
  return section ?? null
}

function clock(value: JsonValue | undefined): string | null {
  if (typeof value !== 'string' || !value) return null
  const date = toWallClock(value)
  return date ? `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` : value
}

export function formatTimeRange(first: JsonValue | undefined, last: JsonValue | undefined): string {
  const start = clock(first)
  const end = clock(last)
  if (start && end) return start === end ? start : `${start}〜${end}`
  return start ?? end ?? DASH
}

export function isConsoleLink(value: unknown): value is string {
  if (typeof value !== 'string' || !value.startsWith('https://')) return false
  try {
    const { protocol, hostname } = new URL(value)
    return protocol === 'https:' && (hostname === 'console.aws.amazon.com' || hostname.endsWith('.console.aws.amazon.com'))
  } catch {
    return false
  }
}

function hours(hourly: unknown): number[] | null {
  if (!Array.isArray(hourly) || !hourly.length || !hourly.every(item => typeof item === 'number' && Number.isFinite(item))) return null
  return hourly as number[]
}

const hourLabel = (hour: number, count: number) => `${String(hour).padStart(2, '0')} 時 ${formatCount(count)} 件`

export function peakHours(hourly: unknown, limit = 2): string {
  const counts = hours(hourly)
  if (!counts) return DASH
  const peaks = counts.map((count, hour) => ({ hour, count })).filter(item => item.count > 0)
    .sort((a, b) => b.count - a.count || a.hour - b.hour).slice(0, limit)
  return peaks.length ? peaks.map(item => hourLabel(item.hour, item.count)).join('、') : DASH
}

export function hourlyBreakdown(hourly: unknown): string {
  const counts = hours(hourly)
  return counts ? counts.map((count, hour) => hourLabel(hour, count)).join(' / ') : ''
}

export function formatBaseline(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return DASH
  const rounded = Math.round(value * 10) / 10
  return Number.isInteger(rounded) ? formatCount(rounded) : rounded.toLocaleString('ja-JP', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
}

export function formatRatio(ratio: number | null | undefined): string {
  return typeof ratio === 'number' && Number.isFinite(ratio) ? `×${ratio.toFixed(2)}` : DASH
}

export interface CounterRow {
  key: string
  label: string
  description: string
  value: number
  comparison: Comparison | null
  hourly: number[] | null
  ai: boolean
}

export interface CounterInput {
  counters?: Record<string, number> | null
  comparisons?: Record<string, Comparison> | null
  hourly?: Record<string, number[]> | null
  glossary?: Glossary | null
  aiKeys?: string[] | null
}

export function counterRows(input: CounterInput): CounterRow[] {
  const ai = new Set(input.aiKeys ?? [])
  return Object.entries(input.counters ?? {}).map(([key, value]) => ({
    key,
    label: input.glossary?.[key]?.label || key,
    description: input.glossary?.[key]?.description ?? '',
    value,
    comparison: input.comparisons?.[key] ?? null,
    hourly: hours(input.hourly?.[key]),
    ai: ai.has(key),
  }))
}

export function evidenceNames(keys: unknown, glossary: Glossary | null | undefined, findings: Finding[]): string[] {
  if (!Array.isArray(keys)) return []
  return keys.filter((key): key is string => typeof key === 'string' && key !== '').map(key =>
    glossary?.[key]?.label || findings.find(finding => finding.code === key)?.text || TABLE_LABELS[key] || key)
}
