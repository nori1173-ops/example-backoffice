import { describe, expect, it } from 'vitest'
import { HELP } from '../help'

type HelpNode = string | { [key: string]: HelpNode }

function flatten(node: HelpNode, path = ''): [string, string][] {
  if (typeof node === 'string') {
    return [[path, node]]
  }
  return Object.entries(node).flatMap(([key, value]) =>
    flatten(value, path ? `${path}.${key}` : key),
  )
}

describe('HELP', () => {
  it('説明文に空のものが無い', () => {
    const empty = flatten(HELP)
      .filter(([, text]) => !text.trim())
      .map(([path]) => path)

    expect(empty).toEqual([])
  })

  // 件数タイルと実行履歴の列見出しは同じ説明を引くため、キーの取りこぼしが即座に画面に出る
  it('件数の説明が判定の全種類ぶんある', () => {
    expect(Object.keys(HELP.judgement)).toEqual([
      'billable',
      'no_data',
      'excluded',
      'failed',
      'missing',
    ])
    expect(Object.keys(HELP.summary)).toContain('missing_count')
    expect(Object.keys(HELP.summary)).toContain('total_amount')
  })

  it('消えた地点の説明は前月分の結果との比較として書く', () => {
    expect(HELP.summary.missing_count).toBe(
      '前月分の結果にはあったのに、今回の地点一覧に無い地点。集計を実行するまでに削除された可能性があります。金額には計上しません',
    )
    expect(HELP.judgement.missing).toBe('前月分の結果にあり今回の一覧に無い（金額に計上しない）')
  })

  it('課金対象期間の説明は観測データの日時で判定すると書く', () => {
    expect(HELP.runDetail.closingPeriod).toBe(
      '締め日時（設定画面）から決まる集計期間。観測データの日時（JST）がこの範囲に入るかで課金・通信なしを判定します',
    )
  })
})
