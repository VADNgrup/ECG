<template>
  <div class="slidev-layout h-full flex flex-col">
    <!-- Title -->
    <div class="mb-0">
      <slot name="title" />
    </div>

    <!-- Two-column content -->
    <div class="flex-1 grid grid-cols-2 gap-10">
      <div class="left">
        <slot name="left" />
      </div>
      <div class="right">
        <slot name="right" />
      </div>
    </div>
  </div>
</template>
