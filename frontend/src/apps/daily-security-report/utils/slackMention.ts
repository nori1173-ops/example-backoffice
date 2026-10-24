/**
 * `slack_mention` の保存値（Slack のメンション記法を空白で連結した文字列）と
 * 画面で扱うトークン配列の相互変換（#1011）。
 *
 * 保存形式は変えないため、候補に無いトークンや手で書かれた断片も落とさずに残す。
 */

const MENTION_PATTERN =
  /<@[^<>|\s]+(?:\|[^<>]*)?>|<!subteam\^[^<>|\s]+(?:\|[^<>]*)?>|<![A-Za-z]+(?:\|[^<>]*)?>/g
const USER_PATTERN = /^<@[^<>|\s]+(?:\|[^<>]*)?>$/
const GROUP_PATTERN = /^<!subteam\^[^<>|\s]+(?:\|[^<>]*)?>$/
const SPECIAL_PATTERN = /^<!([A-Za-z]+)(?:\|[^<>]*)?>$/

const UNKNOWN_USER_LABEL = '@不明なユーザー'
const GROUP_LABEL = '@グループ'

function pushFragments(tokens: string[], chunk: string): void {
  for (const fragment of chunk.split(/\s+/)) {
    if (fragment) {
      tokens.push(fragment)
    }
  }
}

/** 保存値をトークンの配列にする。Slack 記法の間に挟まった断片も 1 トークンとして残す。 */
export function parseMentions(text: string): string[] {
  const tokens: string[] = []
  let index = 0
  for (const match of text.matchAll(MENTION_PATTERN)) {
    pushFragments(tokens, text.slice(index, match.index))
    tokens.push(match[0])
    index = match.index + match[0].length
  }
  pushFragments(tokens, text.slice(index))
  return tokens
}

/** トークンの配列を保存値にする。区切りは空白 1 つ。 */
export function joinMentions(tokens: string[]): string {
  return tokens.filter((token) => token !== '').join(' ')
}

/** 候補に解決できないトークンのチップ表示。ID は画面に出さない。 */
export function mentionFallbackLabel(token: string): string {
  if (USER_PATTERN.test(token)) {
    return UNKNOWN_USER_LABEL
  }
  if (GROUP_PATTERN.test(token)) {
    return GROUP_LABEL
  }
  const special = SPECIAL_PATTERN.exec(token)
  return special ? `@${special[1]}` : token
}
