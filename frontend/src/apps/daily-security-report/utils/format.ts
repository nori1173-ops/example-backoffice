import { DASH } from '@/utils/format'

export function formatBytes(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return DASH
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit++
  }
  const formatted = unit === 0 ? value.toFixed(0) : value.toLocaleString('ja-JP', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
  return `${formatted} ${units[unit]}`
}
