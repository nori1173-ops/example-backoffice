import { defineApp } from '../types'
import { routes } from './routes'

export default defineApp({
  id: 'saas-a-billing',
  title: 'SaaS_A 月額課金レポート',
  icon: 'mdi-receipt-text-outline',
  description: 'SaaS_A の月額ライセンス料金を月次で集計し、Excel を配布する',
  order: 10,
  requiredGroups: [],
  helpMode: true,
  routes,
  nav: [
    { title: '実行履歴', to: '/saas-a-billing', icon: 'mdi-history' },
    { title: '設定', to: '/saas-a-billing/settings', icon: 'mdi-cog', requiredGroups: ['admin'] },
    {
      title: 'ログ',
      to: '/saas-a-billing/logs',
      icon: 'mdi-clipboard-text-clock-outline',
      requiredGroups: ['admin'],
    },
  ],
})
