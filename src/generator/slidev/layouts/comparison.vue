<template>
  <div class="slidev-layout h-full flex flex-col">
    <!-- Title -->
    <div class="mb-0">
      <slot name="title" />
    </div>

    <!-- Two comparison columns -->
    <div class="flex-1 grid grid-cols-2 gap-10">
      <!-- Left column -->
      <div class="left flex flex-col">
        <div class="comparison-heading mb-3">
          <slot name="left-title" />
        </div>
        <div class="comparison-content">
          <slot name="left" />
        </div>
      </div>

      <!-- Right column -->
      <div class="right flex flex-col">
        <div class="comparison-heading mb-3">
          <slot name="right-title" />
        </div>
        <div class="comparison-content">
          <slot name="right" />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.comparison-heading :deep(h2),
.comparison-heading :deep(h3) {
  font-size: 1.1rem;
  font-weight: 600;
  border-bottom: 2px solid currentColor;
  padding-bottom: 0.25rem;
}
</style>
