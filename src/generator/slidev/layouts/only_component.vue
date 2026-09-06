<template>
  <div class="slidev-layout h-full flex flex-col">
    <!-- Title -->
    <div>
      <slot name="title" />
    </div>

    <!-- Component -->
    <div class="flex flex-col items-center justify-center mt-2">
      <div class="component-container w-full flex items-center justify-center" style="height: 40vh;">
        <slot name="component" />
      </div>
      
      <!-- Caption -->
      <div class="mt-4 text-center text-gray-500 italic text-sm">
        <slot name="caption" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.component-container {
  container-type: inline-size;
}

/* Auto-scale tables to fit container */
.component-container :deep(table) {
  width: auto;
  min-width: 50%;
  max-width: 95%;
  font-size: clamp(0.8rem, 3cqw, 1.5rem);
  border-collapse: collapse;
  margin: 0 auto;
}

.component-container :deep(th),
.component-container :deep(td) {
  padding: clamp(0.4rem, 1.5cqw, 0.8rem);
  text-align: left;
  border: 1px solid #ddd;
}

.component-container :deep(th) {
  font-weight: 600;
  background-color: rgba(0, 0, 0, 0.05);
}

/* Ensure images fit nicely */
.component-container :deep(img) {
  max-width: 95%;
  max-height: 100%;
  object-fit: contain;
  margin: 0 auto;
}
</style>
