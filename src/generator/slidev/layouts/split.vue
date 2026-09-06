<template>
  <div class="slidev-layout h-full flex flex-col">
    <!-- Title (full width) -->
    <div class="mb-0">
      <slot name="title" />
    </div>

    <!-- Content (split into 2 columns) -->
    <div class="flex-1 grid gap-10 min-h-0" :style="gridStyle">
      <div class="left overflow-auto">
        <slot name="left" />
      </div>

      <div class="right flex items-center justify-center h-full">
        <slot name="right" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  imageWidth: {
    type: String,
    default: '50%'
  }
})

const gridStyle = computed(() => ({
  gridTemplateColumns: `1fr ${props.imageWidth}`
}))
</script>
