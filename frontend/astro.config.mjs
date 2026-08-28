// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';

// Static: the whole app is prerendered and uploaded to S3, so there is no server to run.
export default defineConfig({
  integrations: [react()],
  output: 'static',
  build: {
    format: 'directory',
  },
});
