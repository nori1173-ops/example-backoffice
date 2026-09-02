import type { RouteRecordRaw } from 'vue-router'

// 各ルートはトップレベルの `component`（`() => import()`）を必ず持たせる。
// redirect のみ・children のみのルートは registry.test.ts の検査で弾かれる。
export const routes: RouteRecordRaw[] = [
  {
    path: '',
    name: 'sample-app.index',
    component: () => import('./views/IndexView.vue'),
    meta: { title: 'サンプル' },
  },
]
