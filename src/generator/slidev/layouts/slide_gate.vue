<template>
  <div class="slidev-layout h-full flex flex-col items-center justify-center text-center">
    <div class="lecture-name mb-4">
      <slot name="lecture_name" />
    </div>
    <div class="speaker-info">
      <slot name="speaker_infor" />
    </div>
  </div>
</template>

<style scoped>
.lecture-name :deep(h1) {
  font-size: 2.4rem;
  font-weight: 700;
  line-height: 1.2;
}

.speaker-info :deep(h2) {
  font-size: 1.1rem;
  font-weight: 400;
  opacity: 0.85;
}
</style>
