// @ts-check
import { defineConfig } from 'astro/config';

import react from '@astrojs/react';

// Static output: the whole app is prerendered to HTML and uploaded to S3, so there is no
// server to run. `format: 'directory'` (the default, set explicitly because the CloudFront
// routing depends on it) emits /page/index.html rather than /page.html - the
// distribution's viewer-request function resolves directory paths to index.html to match.
//
// The React integration is required by camp's UI: every interactive component is a React
// island mounted with client:only="react".
export default defineConfig({
  integrations: [react()],
  output: 'static',
  build: {
    format: 'directory',
  },
});
