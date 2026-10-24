export type Section = 'guardduty' | 'cloudtrail' | 'metrics' | 'insights' | 'instances' | 's3'
export type ExclusionKind = 'ip' | 'principal' | 'event' | 'instance'
export type ExclusionScope = 'cloudtrail' | 'insights' | 'gps' | 'instances' | 'all'
export type SlackMode = 'bot' | 'webhook'
export type MentionLevel = 'critical' | 'warning' | 'always'
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
export type MetricTargets = Record<string, JsonValue>

export interface Exclusion {
  kind: ExclusionKind
  value: string
  scope: ExclusionScope[]
  category: '' | 'office'
  reason: string
  expires_at: string
}

export interface PromptPreset {
  id: string
  name: string
  prompt: string
  updated_at: string
}
export interface AiPromptFixed {
  system_suffix: string
  data_prefix: string
}

export interface EditableSettings {
  ai_enabled: boolean
  ai_model_family: ModelFamily
  ai_model_pin: string
  ai_prompt: string
  ai_prompt_presets: PromptPreset[]
  dormant_days: number
  inventory_dormant_days: number
  inventory_weekday: number | null
  auto_run_enabled: boolean
  alert_enabled: boolean
  mail_enabled: boolean
  slack_enabled: boolean
  mail_to: string[]
  mail_cc: string[]
  slack_channel_id: string
  slack_channel_label: string
  slack_mention: string
  slack_mention_level: MentionLevel
  sections: Record<Section, boolean>
  ratio: number
  min_count: number
  baseline_days: number
  min_baseline_days: number
  access_denied_min: number
  login_failure_min: number
  top_n: number
  list_limit: number
  metric_targets: MetricTargets
  alarmhuba_admin_path_regex: string
  exclusions: Exclusion[]
}

export interface Settings extends EditableSettings {
  version: number
  updated_at: string
  updated_by: string
  updated_by_email: string
}
export interface SettingsView extends Settings {
  target_account: string
  slack_mode: SlackMode | null
  ai_prompt_default: string
  ai_prompt_fixed: AiPromptFixed
}
export type SettingsUpdate = Partial<EditableSettings> & { version: number }

export interface SlackChannel {
  id: string
  name: string
  is_private: boolean
  is_member: boolean
}
export interface SlackUser {
  id: string
  handle: string
  real_name: string
  display_name: string
}
export interface SlackDirectory {
  channels: SlackChannel[]
  users: SlackUser[]
  fetched_at: string
}

export interface DaySummary {
  date?: string | null
  status: string
  red_count?: number
  level: Level
  finding_counts: FindingCounts
  ai_status: AiStatus
  ai_model_label: string | null
  ai_opinion: string
  legacy: boolean
  notify_channels: string[]
  notified_at?: string | null
  notify_note?: string | null
  sent_count: number
  error?: string | null
}

export type Level = 'critical' | 'warning' | 'ok'
export type ModelFamily = 'opus' | 'fable' | 'sonnet' | 'haiku'
export type AiStatus = 'ok' | 'disabled' | 'failed' | 'refused' | 'timeout' | 'skipped'
export interface FindingCounts { critical: number; warning: number }
export type LogRef =
  | { kind: 'lambda'; function: string }
  | { kind: 'alarmhuba'; ip: string; path?: string }
  | { kind: 'gps'; ip: string }
  | { kind: 'cloudtrail'; region?: string; event?: string; actor?: string; event_id?: string; from?: string; to?: string }
  | { kind: 'guardduty'; region: string; id: string }
  | { kind: 'metric'; key: string; target?: string }
export type LogKind = LogRef['kind']
export type LogQueryRequest = LogRef
export type LogQueryStatus = 'running' | 'succeeded' | 'failed'
export type LogRow = Record<string, JsonValue>
export interface LogQueryResult {
  source: string
  range: { from: string; to: string }
  rows: LogRow[]
  truncated: boolean
  scanned_bytes?: number | null
  note?: string | null
  link?: string | null
}
export type LogQueryError = string | { code: string; message?: string | null }
export interface LogQueryResponse {
  query_id?: string
  status: LogQueryStatus
  requested_at?: string
  result?: LogQueryResult | null
  error?: LogQueryError | null
}
export interface Finding {
  section: Section
  code: string
  level: Exclude<Level, 'ok'>
  text: string
  count: number
  first_at?: string
  last_at?: string
  where?: string
  ref?: LogRef
  link?: string
}
export interface Comparison {
  value?: number
  baseline: number | null
  days: number
  exceeded: boolean
  ratio: number | null
  label: string
}
export interface GlossaryEntry { label: string; description: string }
export type Glossary = Record<string, GlossaryEntry>
export interface AiSection { section: Section; level: Level; reason: string; keys?: string[] }
export interface AiAdjustment { section: Section; from: Level; to: Level; reason: string }
export interface AiResult {
  status: AiStatus
  model_id: string | null
  model_label: string | null
  prompt_hash: string | null
  sections: AiSection[]
  opinion: string
  adjustments: AiAdjustment[]
  input_tokens: number | null
  output_tokens: number | null
  duration_sec: number | null
  error: string | { code: string; message: string } | null
  note: string | null
}
export type EvidenceRow = Record<string, JsonValue>
export interface SourceResult {
  status: 'ok' | 'failed' | 'disabled'
  error?: string | null
  duration_sec?: number
  details?: Record<string, EvidenceRow[]>
  details_truncated?: Record<string, number>
  config_changes?: EvidenceRow[]
  regions?: Record<string, string>
  missing_targets?: string[]
  notes?: string[]
  truncated?: string[]
  hourly?: Record<string, number[]>
  hourly_truncated?: boolean
}
export interface S3SourceResult {
  status: 'ok' | 'failed' | 'disabled'
  error?: string
  regions?: string[]
  buckets?: number
  metrics_buckets?: number
  truncated?: boolean
  note?: string
}
export type S3BucketRow = EvidenceRow & {
  bucket: string
  region: string
  objects: number | null
  objects_prev: number | null
  objects_diff: number | null
  objects_pct: number | null
  size: number | null
  size_prev: number | null
  size_pct: number | null
  measured_at?: string | null
  metrics: boolean
  get_requests: number | null
  put_requests: number | null
  delete_requests: number | null
  bytes_downloaded: number | null
  errors_4xx: number | null
  download_baseline: number | null
  delete_baseline: number | null
  level: Level
  key: string
  first_at: string | null
  last_at: string | null
  where: string | null
  link: string | null
}
export interface ActorSummary {
  status: 'ok' | 'unavailable' | 'disabled' | 'initializing'
  note?: string | null
  dormant: EvidenceRow[]
  new: EvidenceRow[]
  external: EvidenceRow[]
  truncated?: Record<string, number>
  inventory?: {
    status: string
    users: EvidenceRow[]
    keys: EvidenceRow[]
    mfa_missing: EvidenceRow[]
    counts: Record<string, number>
    truncated?: Record<string, number>
    generated_at?: string | null
    note?: string | null
  }
}
export interface DailySummary {
  schema: string
  date: string
  range: { from: string; to: string }
  sources: Partial<Record<Exclude<Section, 's3'>, SourceResult>> & { s3?: S3SourceResult }
  counters: Record<string, number>
  comparisons?: Record<string, Comparison>
  seen?: Record<string, string[]>
  top?: Record<string, EvidenceRow[]> & { s3_buckets?: S3BucketRow[] }
  top_truncated?: Record<string, number>
  instances?: Record<string, EvidenceRow[] | string[]>
  findings: Finding[]
  finding_counts: FindingCounts
  findings_truncated?: Partial<Record<Section, number>>
  rule_levels: Record<Section, Level>
  levels: Record<Section, Level>
  level: Level
  ai: AiResult
  actors?: ActorSummary
  excluded: Record<string, number>
  baseline_missing: Section[]
  duration_sec: number
}
export interface DayDetail extends DaySummary {
  summary?: DailySummary | null
  glossary?: Glossary | null
  ai?: AiResult | null
  started_at?: string | null
  finished_at?: string | null
  trigger?: string
  attempt?: number
  notify_message_id?: string | null
}
export interface PreviewRequest {
  date: string
  prompt?: string
  model_family?: ModelFamily
  model_pin?: string
}
export interface PreviewVerdict extends AiResult {
  level: Level
  levels?: Partial<Record<Section, Level>>
}
export interface PreviewResult {
  status: 'running' | 'succeeded' | 'failed'
  date: string
  result?: PreviewVerdict | null
  error?: { code: string; message: string; http_status?: number } | null
  prompt_hash?: string | null
  model_id?: string | null
  requested_at?: string
}
export interface RunAccepted {
  date: string
  accepted_at: string
}
