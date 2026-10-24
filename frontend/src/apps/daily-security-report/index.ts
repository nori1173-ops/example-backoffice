import { defineApp } from '../types'
import { routes } from './routes'

export default defineApp({
  id: 'daily-security-report',
  title: 'AWS日次セキュリティレポート',
  icon: 'mdi-shield-search',
  description: 'AWS の日次セキュリティレポートと監視・通知の設定',
  order: 30,
  requiredGroups: [],
  helpMode: true,
  routes,
  nav: [
    { title: 'レポート一覧', to: '/daily-security-report', icon: 'mdi-calendar-search' },
    { title: '設定', to: '/daily-security-report/settings', icon: 'mdi-cog', requiredGroups: ['admin'] },
  ],
})
