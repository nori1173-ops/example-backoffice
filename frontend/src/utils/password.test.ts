import { describe, expect, it } from 'vitest'
import { PASSWORD_POLICY_MESSAGE, isValidPassword, passwordPolicyRule } from './password'

describe('isValidPassword', () => {
  it('8 文字以上で英大小・数字を含むと通る', () => {
    expect(isValidPassword('Passw0rd')).toBe(true)
  })

  it('記号なしでも通る', () => {
    expect(isValidPassword('Abcdefg1')).toBe(true)
  })

  it('記号を含んでも通る', () => {
    expect(isValidPassword('Passw0rd!')).toBe(true)
  })

  it('7 文字は落ちる（Cognito の MinimumLength は 8）', () => {
    expect(isValidPassword('Passw0r')).toBe(false)
  })

  it.each([
    ['大文字なし', 'passw0rd'],
    ['小文字なし', 'PASSW0RD'],
    ['数字なし', 'PasswordAbc'],
  ])('%s は落ちる', (_label, value) => {
    expect(isValidPassword(value)).toBe(false)
  })
})

describe('passwordPolicyRule', () => {
  it('規則を満たさないと日本語の規則メッセージを返す', () => {
    expect(passwordPolicyRule('short')).toBe(PASSWORD_POLICY_MESSAGE)
  })

  it('規則を満たすと true を返す', () => {
    expect(passwordPolicyRule('Passw0rd')).toBe(true)
  })
})
