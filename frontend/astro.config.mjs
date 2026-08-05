// @ts-check
import { defineConfig } from 'astro/config';

// Static output: the whole app is prerendered to HTML and uploaded to S3, so there is no
// server to run. `format: 'directory'` (the default, set explicitly here because the
// CloudFront routing depends on it) emits /login/index.html rather than /login.html - the
// distribution's viewer-request function resolves directory paths to index.html to match.
export default defineConfig({
  output: 'static',
  build: {
    format: 'directory',
  },
});
