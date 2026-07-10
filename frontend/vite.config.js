import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import {
  buildMarketingStructuredData,
  buildRobotsTxt,
  buildSitemapXml,
  normalizeSiteOrigin,
} from './src/seo/config.js'

function seoAssets(siteOrigin) {
  return {
    name: 'clarity-seo-assets',
    apply: 'build',
    transformIndexHtml(html) {
      if (!siteOrigin) return html
      const transformed = html
        .replace('<link rel="canonical" href="/" />', `<link rel="canonical" href="${siteOrigin}/" />`)
        .replace('<meta property="og:url" content="/" />', `<meta property="og:url" content="${siteOrigin}/" />`)
        .replace('<meta property="og:image" content="/social-card.jpg" />', `<meta property="og:image" content="${siteOrigin}/social-card.jpg" />`)
        .replace('<meta name="twitter:image" content="/social-card.jpg" />', `<meta name="twitter:image" content="${siteOrigin}/social-card.jpg" />`)

      return {
        html: transformed,
        tags: [{
          tag: 'script',
          attrs: { type: 'application/ld+json', 'data-seo-structured-data': '' },
          children: JSON.stringify(buildMarketingStructuredData(siteOrigin)),
          injectTo: 'head',
        }],
      }
    },
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'robots.txt',
        source: buildRobotsTxt(siteOrigin),
      })
      if (siteOrigin) {
        this.emitFile({
          type: 'asset',
          fileName: 'sitemap.xml',
          source: buildSitemapXml(siteOrigin),
        })
      }
    },
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const siteOrigin = normalizeSiteOrigin(env.VITE_PUBLIC_SITE_URL)

  return {
    plugins: [react(), seoAssets(siteOrigin)],
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.js',
      css: true,
    },
    build: {
      // Public production source maps expose the original application source.
      // Keep maps for local/non-production diagnostics only; a future error
      // monitoring integration can upload hidden maps without serving them.
      sourcemap: mode !== 'production',
      chunkSizeWarningLimit: 650,
    },
    server: {
      port: 3000,
      host: '0.0.0.0',
      proxy: {
        '/api': {
          target: process.env.VITE_PROXY_TARGET || 'http://backend:8000',
          changeOrigin: true,
        },
      }
    },
  }
})
