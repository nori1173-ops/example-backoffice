import { defineApp } from '../types'
import { routes } from './routes'

// このディレクトリを frontend/src/apps/{app}/ にコピーし、id / title / icon / order /
// requiredGroups / nav を書き換える（ADD-APP-GUIDE 2 章ステップ 7）。
// 先頭 `_` のディレクトリはレジストリの glob から除外されるため、雛形のままでは配信されない。
export default defineApp({
  id: 'sample-app',
  title: 'サンプル',
  icon: 'mdi-application',
  description: 'アプリ雛形。コピーして中身を置き換える',
  order: 50,
  routes,
  nav: [{ title: '一覧', to: '/sample-app' }],
})
