export type SiteEnv = 'dev' | 'prod'

/** タブ名・画面のサイト名はここを正とする（index.html の <title> も同じ表記に揃える）。 */
export const SITE_TITLE = '社内保守管理サイト'

export const SITE_ENV_LABELS: Record<SiteEnv, string> = {
  dev: '検証環境',
  prod: '本番',
}

export const SITE_ENV_COLORS: Record<SiteEnv, string> = {
  dev: 'orange',
  prod: 'green',
}

/**
 * サイトの配置環境。`VITE_SITE_ENV` はデプロイスクリプトが渡す（設計 R7）。
 * 未設定・未知の値は検証サイト扱いにして、本番の見た目を名乗らせない。
 */
export function siteEnv(): SiteEnv {
  return import.meta.env.VITE_SITE_ENV === 'prod' ? 'prod' : 'dev'
}
