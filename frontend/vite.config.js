import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import {
  buildRobotsTxt,
  buildSitemapXml,
  buildStructuredData,
  normalizeSiteOrigin,
} from './src/seo/config.js'
import {
  PUBLIC_SERVER_SHELL_PATHS,
  buildPublicRouteHtml,
} from './src/seo/serverShell.js'
import {
  normalizeMeasurementId,
  normalizeVerificationToken,
} from './src/analytics/googleAnalytics.js'

const DEFAULT_CONTACT_URL = 'mailto:support@getlawhand.com'

/**
 * The no-JavaScript shell inside index.html is what a crawler and a visitor on
 * a failed bundle actually see, so its demo link has to honour the deployment's
 * configured contact URL rather than the source default.
 */
function demoContactHref(contactUrl) {
  if (!contactUrl.startsWith('mailto:')) return contactUrl
  return contactUrl.includes('?') ? contactUrl : `${contactUrl}?subject=LawHand%20Demo`
}

function seoAssets(siteOrigin, contactUrl, organizationProfile, verificationToken) {
  return {
    name: 'lawhand-seo-assets',
    apply: 'build',
    transformIndexHtml(html) {
      const withContact = html.replace(
        `href="${DEFAULT_CONTACT_URL}?subject=LawHand%20Demo"`,
        `href="${demoContactHref(contactUrl)}"`,
      )
      // Search Console's HTML-tag method has to be present in the served
      // document before verification, and it must stay there afterwards.
      const verificationTag = verificationToken
        ? [{
          tag: 'meta',
          attrs: { name: 'google-site-verification', content: verificationToken },
          injectTo: 'head',
        }]
        : []
      if (!siteOrigin) return { html: withContact, tags: verificationTag }
      const transformed = withContact
        .replace('<link rel="canonical" href="/" />', `<link rel="canonical" href="${siteOrigin}/" />`)
        .replace('<meta property="og:url" content="/" />', `<meta property="og:url" content="${siteOrigin}/" />`)
        .replace('<meta property="og:image" content="/social-card-v2.png" />', `<meta property="og:image" content="${siteOrigin}/social-card-v2.png" />`)
        .replace('<meta name="twitter:image" content="/social-card-v2.png" />', `<meta name="twitter:image" content="${siteOrigin}/social-card-v2.png" />`)

      return {
        html: transformed,
        tags: [
          ...verificationTag,
          {
            tag: 'script',
            attrs: { type: 'application/ld+json', 'data-seo-structured-data': '' },
            children: JSON.stringify(buildStructuredData(siteOrigin, '/', organizationProfile)),
            injectTo: 'head',
          },
        ],
      }
    },
    writeBundle(options) {
      const outputDirectory = path.resolve(options.dir || 'dist')
      const indexHtml = readFileSync(path.join(outputDirectory, 'index.html'), 'utf8')
      for (const pathname of PUBLIC_SERVER_SHELL_PATHS) {
        const routeDirectory = path.join(outputDirectory, pathname.slice(1))
        mkdirSync(routeDirectory, { recursive: true })
        writeFileSync(
          path.join(routeDirectory, 'index.html'),
          buildPublicRouteHtml(indexHtml, pathname, siteOrigin, contactUrl, organizationProfile),
          'utf8',
        )
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
  const contactUrl = env.VITE_CONTACT_URL?.trim() || DEFAULT_CONTACT_URL
  const organizationProfile = {
    contactUrl,
    telephone: env.VITE_ORG_TELEPHONE,
    sameAs: env.VITE_ORG_SAME_AS,
  }
  // Validate at build time so a malformed id or token fails here rather than
  // silently collecting nothing in production.
  normalizeMeasurementId(env.VITE_GA_MEASUREMENT_ID)
  const verificationToken = normalizeVerificationToken(env.VITE_GOOGLE_SITE_VERIFICATION)

  return {
    plugins: [react(), seoAssets(siteOrigin, contactUrl, organizationProfile, verificationToken)],
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
