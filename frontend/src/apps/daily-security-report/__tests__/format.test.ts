import { describe, expect, it } from 'vitest'
import { formatBytes } from '../utils/format'

describe('formatBytes', () => {
  it.each([
    [0, '0 B'], [1, '1 B'], [1.4, '1 B'], [1.5, '2 B'], [1023, '1023 B'], [1024, '1.0 KB'], [1536, '1.5 KB'],
    [1024 ** 2, '1.0 MB'], [1024 ** 3, '1.0 GB'], [1024 ** 4, '1.0 TB'], [1024 ** 5, '1,024.0 TB'], [1024 ** 6, '1,048,576.0 TB'],
    [1234567, '1.2 MB'],
  ])('%s バイトを 1,024 基数で、B は整数、KB 以上は小数 1 桁・桁区切りで表示する', (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected)
  })
  it.each([null, undefined, NaN, Infinity, -Infinity, -1])('欠損や不正な容量 %s は —', value => {
    expect(formatBytes(value)).toBe('—')
  })
})
