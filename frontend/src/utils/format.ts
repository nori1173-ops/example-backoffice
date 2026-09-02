/** 値が無いことを示す表示。全画面で同じ記号を使う（UI-13 / UI-16）。 */
export const DASH = '—'

const ISO_PATTERN = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/

function pad(value: number): string {
  return String(value).padStart(2, '0')
}

/** JST オフセット付き ISO をそのままの壁時計として組み直す（環境の時差に依存させない）。 */
export function toWallClock(iso: string): Date | null {
  const parts = ISO_PATTERN.exec(iso)
  if (!parts) {
    return null
  }
  const [year, month, day, hour, minute] = parts.slice(1, 6).map(Number)
  return new Date(year, month - 1, day, hour, minute, Number(parts[6] ?? 0))
}

export function formatDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function toDate(value: string | Date | null | undefined): Date | null {
  if (value instanceof Date) {
    return value
  }
  return value ? toWallClock(value) : null
}

/** 解釈できない文字列は捨てずにそのまま見せる（API の表記ゆれに気付けるように）。 */
function fallback(value: string | Date | null | undefined): string {
  return typeof value === 'string' && value ? value : DASH
}

export function formatDateTime(value: string | Date | null | undefined): string {
  const date = toDate(value)
  return date ? `${formatDate(date)} ${pad(date.getHours())}:${pad(date.getMinutes())}` : fallback(value)
}

export function formatDateTimeSeconds(value: string | Date | null | undefined): string {
  const date = toDate(value)
  return date
    ? `${formatDateTime(date)}:${pad(date.getSeconds())}`
    : fallback(value)
}

export function formatYen(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : `¥${value.toLocaleString('ja-JP')}`
}

export function formatCount(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : value.toLocaleString('ja-JP')
}
