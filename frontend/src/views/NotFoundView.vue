<template>
  <v-app>
    <v-main class="bg-grey-lighten-4">
      <v-container class="d-flex flex-column align-center justify-center text-center fill-height">
        <div class="text-h4 font-weight-bold">
          404
        </div>
        <h1 class="text-h6 mt-2 mb-2">
          ページが見つかりません
        </h1>
        <p class="text-body-2 text-medium-emphasis mb-6">
          URL が変更されたか、削除された可能性があります。
        </p>
        <v-btn
          color="primary"
          variant="tonal"
          :to="{ name: 'home' }"
        >
          ホームへ戻る
        </v-btn>
      </v-container>
    </v-main>
  </v-app>
</template>
