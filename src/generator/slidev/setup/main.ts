import { defineAppSetup } from '@slidev/types'
import { MotionPlugin } from '@vueuse/motion'

export default defineAppSetup(({ app, router }) => {
  // Register the motion plugin
  app.use(MotionPlugin)
})
