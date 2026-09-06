<template>
  <div class="slidev-layout h-full flex flex-col">
    <!-- Title -->
    <div class="mb-0">
      <slot name="title" />
    </div>

    <!-- Image (top) -->
    <div class="flex items-center justify-center" :style="imageAreaStyle">
      <slot name="image" />
    </div>

    <!-- Text (bottom) -->
    <div class="flex-1 overflow-auto">
      <slot name="content" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps({
  imageHeight: {
    type: String,
    default: '50%'
  }
})

const imageAreaStyle = computed(() => ({
  height: props.imageHeight,
  flexShrink: '0'
}))
</script>
