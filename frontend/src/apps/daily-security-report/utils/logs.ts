import { DASH, formatCount, formatDateTimeSeconds, toWallClock } from '@/utils/format'
import type { Glossary, JsonValue, LogKind, LogRef } from '../types'

export interface LogColumn { key: string; label: string }
export type TableLogKind = Exclude<LogKind, 'guardduty'>

const TIME: LogColumn = { key: 'time', label: '時刻（JST）' }
const METHOD: LogColumn = { key: 'method', label: 'メソッド' }
const PATH: LogColumn = { key: 'path', label: 'パス' }

export const LOG_COLUMNS: Record<TableLogKind, LogColumn[]> = {
  lambda: [TIME, { key: 'text', label: '内容' }],
  alarmhuba: [TIME, METHOD, PATH, { key: 'status', label: '状態' }],
  gps: [TIME, METHOD, PATH],
  cloudtrail: [TIME, { key: 'event', label: '操作' }, { key: 'actor', label: '主体' }, { key: 'ip', label: 'IP' }, { key: 'event_id', label: 'イベント ID' }],
  metric: [TIME, { key: 'count', label: '件数' }],
}

export const GUARDDUTY_FIELDS: LogColumn[] = [
  { key: 'type', label: '種別' }, { key: 'severity', label: '重大度' }, { key: 'title', label: '題名' }, { key: 'description', label: '説明' },
  { key: 'resource', label: '対象' }, { key: 'remote_ip', label: '相手 IP' }, { key: 'remote_country', label: '相手の国' },
  { key: 'first_seen', label: '初回観測' }, { key: 'last_seen', label: '最終観測' }, { key: 'count', label: '回数' },
]

const REQUIRED: Record<LogKind, string[]> = {
  lambda: ['function'], alarmhuba: ['ip'], gps: ['ip'], cloudtrail: ['region'], guardduty: ['region', 'id'], metric: ['key'],
}
const present = (record: Record<string, unknown>, field: string) => typeof record[field] === 'string' && record[field] !== ''

export function isLogRef(value: unknown): value is LogRef {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  const kind = record.kind
  if (typeof kind !== 'string' || !Object.hasOwn(REQUIRED, kind)) return false
  if (!REQUIRED[kind as LogKind].every(field => present(record, field))) return false
  return kind !== 'cloudtrail' || ['event_id', 'event', 'actor'].some(field => present(record, field))
}

export function isMetricCounter(key: string): boolean {
  return /^(waf|apigw|alb|cloudfront)\./.test(key) || key === 'lambda.errors' || key === 'gps.rejected'
    || /^instances\.(ec2|lightsail)\..+\.(cpu_avg|net_out)$/.test(key)
}

const ERROR_TEXT: Record<string, string> = {
  TOO_MANY_QUERIES: '同時に見られるのは 3 件まで',
  TIMEOUT: '60 秒で打ち切り',
}

export function logErrorMessage(code: string | null | undefined, message?: string | null): string {
  if (code && ERROR_TEXT[code]) return ERROR_TEXT[code]
  if (code && message) return `${message}（${code}）`
  return code || message || '取得に失敗しました'
}

export function formatScanned(bytes: number | null | undefined): string {
  return typeof bytes === 'number' && Number.isFinite(bytes) ? `${(bytes / 1048576).toFixed(1)} MB` : ''
}

function minute(value: string): string {
  if (/^\d{2}:\d{2}$/.test(value)) return value
  const date = toWallClock(value)
  return date ? `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}` : value
}

export function formatLogCell(value: JsonValue | undefined, key: string, kind: LogKind): string {
  if (value === undefined || value === null || value === '') return DASH
  if (typeof value === 'string') {
    if (key === 'time' && kind === 'metric') return minute(value)
    return /^(time|first_seen|last_seen)$/.test(key) ? formatDateTimeSeconds(value) : value
  }
  if (typeof value === 'number') return key === 'status' ? String(value) : formatCount(value)
  if (typeof value === 'boolean') return value ? 'はい' : 'いいえ'
  return JSON.stringify(value)
}

export function describeLogRef(ref: LogRef, glossary?: Glossary | null): string {
  switch (ref.kind) {
    case 'lambda': return `Lambda ${ref.function}`
    case 'alarmhuba': return ['alarmhuba', ref.ip, ref.path].filter(Boolean).join(' ')
    case 'gps': return `GPS 受信口 ${ref.ip}`
    case 'cloudtrail': return ['CloudTrail', ref.region, ref.event_id || ref.event || ref.actor].filter(Boolean).join(' ')
    case 'guardduty': return `GuardDuty ${ref.region} ${ref.id}`
    case 'metric': return `指標 ${glossary?.[ref.key]?.label || ref.key}`
  }
}
