import { describe, expect, it } from 'vitest'
import { joinMentions, mentionFallbackLabel, parseMentions } from '../utils/slackMention'

describe('parseMentions', () => {
  it('ユーザーと特別トークンを順序どおり取り出す', () => {
    expect(parseMentions('<@U0000DEVA1> <!here>')).toEqual(['<@U0000DEVA1>', '<!here>'])
    expect(parseMentions('<!channel> <@U0000001>')).toEqual(['<!channel>', '<@U0000001>'])
  })

  it('ユーザーグループと表示名付きのトークンを 1 つとして扱う', () => {
    expect(parseMentions('<!subteam^S123|@dev> <@U1|devA>')).toEqual([
      '<!subteam^S123|@dev>',
      '<@U1|devA>',
    ])
  })

  it('空白で区切られていないトークンも分けて取り出す', () => {
    expect(parseMentions('<@U1><!here>')).toEqual(['<@U1>', '<!here>'])
  })

  it('空文字と空白だけの入力は空配列', () => {
    expect(parseMentions('')).toEqual([])
    expect(parseMentions('   \n ')).toEqual([])
  })

  it('トークン以外の断片も 1 つとして残す', () => {
    expect(parseMentions('@開発者本人 <!here> よろしく')).toEqual(['@開発者本人', '<!here>', 'よろしく'])
  })
})

describe('joinMentions', () => {
  it('空白 1 つで連結する', () => {
    expect(joinMentions(['<@U1>', '<!here>'])).toBe('<@U1> <!here>')
    expect(joinMentions([])).toBe('')
  })

  it('parse と往復しても情報を失わない', () => {
    const saved = '<@U0000DEVA1> <!subteam^S123|@dev> <!here> 未知の値'
    expect(joinMentions(parseMentions(saved))).toBe(saved)
  })

  it('往復で余分な空白だけを詰める', () => {
    expect(joinMentions(parseMentions('  <@U1>   <!here>  '))).toBe('<@U1> <!here>')
  })
})

describe('mentionFallbackLabel', () => {
  it('候補に無いユーザーは名前を出さずに知らせる', () => {
    expect(mentionFallbackLabel('<@U9999999>')).toBe('@不明なユーザー')
  })

  it('ユーザーグループはグループとして出す', () => {
    expect(mentionFallbackLabel('<!subteam^S123|@dev>')).toBe('@グループ')
  })

  it('特別トークンはそのまま読める形にする', () => {
    expect(mentionFallbackLabel('<!here>')).toBe('@here')
    expect(mentionFallbackLabel('<!channel>')).toBe('@channel')
  })

  it('それ以外はそのまま出す', () => {
    expect(mentionFallbackLabel('@開発者本人')).toBe('@開発者本人')
  })
})
