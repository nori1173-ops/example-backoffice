/**
 * Cognito の PasswordPolicy（infra/core/template.yaml の UserPool）と同じ規則。
 * 緩いままだと画面のバリデーションを通ってから Cognito に弾かれる。
 */
export const PASSWORD_POLICY_MESSAGE =
  'パスワードは 8 文字以上で、英大文字・英小文字・数字をそれぞれ 1 文字以上含めてください'

const MINIMUM_LENGTH = 8
const REQUIRED_CHARACTER_CLASSES = [/[A-Z]/, /[a-z]/, /[0-9]/]

export function isValidPassword(value: string): boolean {
  return (
    value.length >= MINIMUM_LENGTH &&
    REQUIRED_CHARACTER_CLASSES.every((pattern) => pattern.test(value))
  )
}

/** Vuetify の `:rules` に渡す規則 */
export function passwordPolicyRule(value: string): true | string {
  return isValidPassword(value) || PASSWORD_POLICY_MESSAGE
}
