export type TargetEnv = 'verification' | 'production'
export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'distributed'
export type RunTrigger = 'scheduled' | 'manual'
export type TenantStatus = 'ok' | 'failed'
export type Judgement = 'billable' | 'no_data' | 'excluded' | 'failed'
export type CameraSource = 'export' | 'settings'
export type SlackMode = 'bot' | 'webhook'

/** `SETTINGS#current` の保存属性（設計 7.1） */
export interface Settings {
  target_env: TargetEnv
  cutoff_day: number
  cutoff_time: string
  auto_retry_days: number
  auto_run_enabled: boolean
  auto_run_from_month: string
  /** メール配布の有効・無効（既定は無効）。Slack とは独立に切り替える */
  mail_enabled: boolean
  /** Slack 配布の有効・無効（既定は無効）。メールとは独立に切り替える */
  slack_enabled: boolean
  mail_to: string[]
  mail_cc: string[]
  slack_channel_id: string
  slack_channel_label: string
  slack_mention: string
  slack_template: string
  exclude_keywords: string[]
  camera_format_names: string[]
  version: number
  updated_at: string
  updated_by: string
  updated_by_email: string
}

export type EditableSettings = Omit<
  Settings,
  'version' | 'updated_at' | 'updated_by' | 'updated_by_email'
>

/** `PUT /settings` の要求。`version` は楽観ロック用（設計 8 章） */
export type SettingsUpdate = Partial<EditableSettings> & { version: number }

/** `GET /settings` の応答。SSM 由来の 3 項目は DynamoDB に保存されない（設計 8 章） */
export interface SettingsView extends Settings {
  saasa_host: string | null
  /** SSM に接続情報がある環境。集計対象環境はサイト固定なので固定環境のキーだけ届く（設計 R1） */
  targets: Partial<Record<TargetEnv, boolean>>
  slack_mode: SlackMode | null
}

export interface Band {
  max_items: number
  price_3y: number
  price_1y: number
}

export interface CameraPrice {
  price_3y: number
  price_1y: number
}

/** 単価表の中身（設計 7.3 の `price_table`） */
export interface PriceTable {
  effective_from: string
  bands: Band[]
  camera: CameraPrice
  note: string
}

/** `PRICE#EFFECTIVE#{日付}` の版（設計 7.1） */
export interface PriceVersion extends PriceTable {
  created_at: string
  created_by: string
  created_by_email: string
}

export interface PriceList {
  versions: PriceVersion[]
  current: PriceVersion | null
}

export interface ClosingPeriod {
  from: string
  to: string
  cutoff_day: number
  cutoff_time: string
  open: boolean
}

export interface BillingSummary {
  tenant_count: number
  device_count: number
  billable_count: number
  no_data_count: number
  excluded_count: number
  review_count: number
  failed_tenant_count: number
  missing_count: number
  total_amount: number
}

export interface RunMetrics {
  duration_sec: number
  export_calls: number
  export_bytes: number
}

/**
 * `RUN#{target_env}#{YYYY-MM}` の属性（設計 7.1）。
 * `api_token` / `run_token` など実行制御の内部属性は画面で使わないため持たない。
 */
export interface RunSummary {
  month: string
  target_env: TargetEnv
  saasa_host?: string
  status: RunStatus
  trigger: RunTrigger
  attempt: number
  auto_attempts?: number
  execution_arn?: string
  tenants_total?: number
  tenants_done?: number
  requested_by: string | null
  requested_by_email: string | null
  distribute: boolean
  force: boolean
  started_at: string
  finished_at?: string
  closing_period?: ClosingPeriod
  price_effective_from?: string
  summary?: BillingSummary
  s3_xlsx_key?: string
  s3_result_key?: string
  notify_message_id?: string
  notify_channels?: string[]
  distribute_note?: string
  notified_at?: string
  error?: string
  metrics?: RunMetrics
}

/** `result.json` の `devices[]` 1 件（設計 7.3） */
export interface DeviceJudgement {
  device_cd: string
  device_name: string
  group_cds: string[]
  group_names: string[]
  format_name: string
  item_count: number
  device_description: string
  is_camera: boolean
  camera_source: CameraSource | null
  retention: number | null
  received: boolean
  first_received_at: string | null
  last_received_at: string | null
  boundary_only: boolean
  judgement: Judgement
  unit_price: number
  amount: number
  flags: string[]
  note: string
}

export interface TenantCounts {
  billable: number
  no_data: number
  excluded: number
  review: number
}

/** `result.json` の `tenants[]` 1 件（設計 7.3） */
export interface TenantResult {
  tenant_id: string
  tenant_cd: string
  tenant_name: string
  tenant_description: string
  retention_period: number | null
  device_count_api: number
  status: TenantStatus
  error: string | null
  export_calls: number
  export_bytes: number
  subtotal: number
  counts: TenantCounts
  devices: DeviceJudgement[]
}

/** 前回 RUN にあり今回の地点一覧に無い地点（設計 7.3） */
export interface MissingDevice {
  tenant_cd: string
  tenant_name: string
  device_cd: string
  device_name: string
  group_names: string[]
  last_seen_month: string
}

export interface RunSnapshot {
  attempt: number
  trigger: RunTrigger
  started_at: string
  finished_at: string
  requested_by: string | null
  env: string
  target_env: TargetEnv
  saasa_host: string
  execution_arn: string
}

export interface SettingsSnapshot {
  exclude_keywords: string[]
  camera_format_names: string[]
}

/** `result.json` 全体（設計 7.3） */
export interface RunResult {
  schema: string
  target_env: TargetEnv
  month: string
  closing_period: ClosingPeriod
  run: RunSnapshot
  settings_snapshot: SettingsSnapshot
  price_table: PriceTable
  summary: BillingSummary
  tenants: TenantResult[]
  missing_devices: MissingDevice[]
}

/** `GET /runs/{month}` の応答。`detail` を付けないときの `result` は null */
export interface RunDetail {
  run: RunSummary
  result: RunResult | null
}

export interface RunRequest {
  month: string
  /** サイトごとに固定される（設計 R1）。省略すると API 側の固定値で実行される */
  targetEnv?: TargetEnv
  distribute: boolean
  force: boolean
}

/** `POST /runs` の 202 応答 */
export interface RunAccepted {
  month: string
  target_env: TargetEnv
  status: RunStatus
  attempt: number
  execution_arn: string
}

export interface DownloadUrl {
  url: string
  expires_at: string
}

/** `AUDIT#{ISO時刻}` の属性（設計 7.1） */
export interface AuditEntry {
  target: 'settings' | 'price'
  actor: string
  actor_email: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  at: string
}
