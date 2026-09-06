  <template>
  <div class="slidev-layout h-full flex flex-col">
    <!-- Title -->
    <div class="mb-0">
      <slot name="title" />
    </div>

    <!-- Content -->
    <div class="flex-1 grid gap-10 min-h-0" :style="gridStyle">
      <!-- Left -->
      <div class="overflow-auto">
        <slot name="left" />
      </div>
      <!-- Right: two stacked images, each vertically centered -->
      <div class="grid grid-rows-2 gap-2 h-full min-h-0">
        <div class="flex items-center justify-center overflow-hidden">
          <slot name="right-top" />
        </div>
        <div class="flex items-center justify-center overflow-hidden">
          <slot name="right-bottom" />
        </div>
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

<style scoped>
.table-container {
  container-type: inline-size;
}

/* Auto-scale tables to fit container */
.table-container :deep(table) {
  width: 100%;
  max-width: 100%;
  font-size: clamp(0.5rem, 2.5cqw, 1rem);
  border-collapse: collapse;
}

.table-container :deep(th),
.table-container :deep(td) {
  padding: clamp(0.2rem, 1cqw, 0.5rem);
  text-align: left;
  border: 1px solid #ddd;
}

.table-container :deep(th) {
  font-weight: 600;
  background-color: rgba(0, 0, 0, 0.05);
}
</style>
