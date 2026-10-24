import { DASH } from '@/utils/format'
import type { AuditEntry, AuditTarget } from '../types'

export const AUDIT_TARGET_LABELS: Record<AuditTarget, string> = {
  run: '手動実行',
  settings: '設定',
  price: '単価表',
}

/** 監査表はフォームと同じ語で読ませる（UI-17）。未知のキーは生のまま出す。 */
export const FIELD_LABELS: Record<string, string> = {
  target_env: '集計対象環境',
  cutoff_day: '締め日',
  cutoff_time: '締め時刻',
  auto_retry_days: '自動再実行上限日数',
  auto_run_enabled: '自動実行',
  auto_run_from_month: '自動実行の開始月',
  mail_enabled: 'メール配布',
  slack_enabled: 'Slack 配布',
  mail_to: 'メール宛先 To',
  mail_cc: 'メール宛先 Cc',
  mail_attachment_sheets: 'メールに添付する Excel',
  slack_channel_id: 'Slack チャンネル ID',
  slack_channel_label: 'Slack チャンネル表示名',
  slack_mention: 'Slack メンション',
  slack_template: 'Slack 文面',
  exclude_keywords: '除外キーワード',
  camera_format_names: 'カメラ扱いフォーマット名',
  effective_from: '適用開始日',
  bands: '項目数帯',
  camera: 'カメラ地点の単価',
  note: '備考',
  created_at: '登録日時',
  created_by: '登録者',
  created_by_email: '登録者メール',
}

export function changedKeys(entry: AuditEntry): string {
  const before = entry.before ?? {}
  const keys = Object.keys(entry.after).filter(
    (key) => JSON.stringify(before[key]) !== JSON.stringify(entry.after[key]),
  )
  return keys.map((key) => FIELD_LABELS[key] ?? key).join('、') || DASH
}

/** 一覧の「内容」列。手動実行は対象月と配布の有無、設定・単価は変わった項目名を出す。 */
export function summarize(entry: AuditEntry): string {
  if (entry.target !== 'run') {
    return changedKeys(entry)
  }
  const env = entry.after.target_env === 'production' ? '本番環境' : '検証環境'
  const distribute = entry.after.distribute ? 'あり' : 'なし'
  const force = entry.after.force ? ' 強制' : ''
  return `${String(entry.after.month ?? DASH)} ${env} 配布${distribute}${force}`
}
