import React, { useEffect } from 'react'
import { useLocation } from 'react-router-dom'
import {
  PRIVATE_DESCRIPTION,
  SITE_NAME,
  buildStructuredData,
  getRouteMeta,
  normalizeSiteOrigin,
} from '../seo/config'

const INDEX_ROBOTS = 'index, follow, max-image-preview:large, max-snippet:-1, max-video-preview:-1'
const PRIVATE_ROBOTS = 'noindex, nofollow, noarchive, nosnippet'
const SOCIAL_IMAGE_PATH = '/social-card-v2.png'

function setMeta(attribute, key, content) {
  let node = document.head.querySelector(`meta[${attribute}="${key}"]`)
  if (!node) {
    node = document.createElement('meta')
    node.setAttribute(attribute, key)
    document.head.appendChild(node)
  }
  node.setAttribute('content', content)
}

function setCanonical(href) {
  let node = document.head.querySelector('link[rel="canonical"]')
  if (!href) {
    node?.remove()
    return
  }
  if (!node) {
    node = document.createElement('link')
    node.setAttribute('rel', 'canonical')
    document.head.appendChild(node)
  }
  node.setAttribute('href', href)
}

function setStructuredData(data) {
  let node = document.head.querySelector('script[data-seo-structured-data]')
  if (!data) {
    node?.remove()
    return
  }
  if (!node) {
    node = document.createElement('script')
    node.type = 'application/ld+json'
    node.setAttribute('data-seo-structured-data', '')
    document.head.appendChild(node)
  }
  node.textContent = JSON.stringify(data).replace(/</g, '\\u003c')
}

function runtimeSiteOrigin() {
  const configured = normalizeSiteOrigin(import.meta.env.VITE_PUBLIC_SITE_URL)
  return configured || window.location.origin
}

export default function SeoHead() {
  const { pathname } = useLocation()

  useEffect(() => {
    const route = getRouteMeta(pathname)
    const siteOrigin = runtimeSiteOrigin()
    const robots = route.indexable ? INDEX_ROBOTS : PRIVATE_ROBOTS
    const socialTitle = route.indexable ? route.title : SITE_NAME
    const socialDescription = route.indexable ? route.description : PRIVATE_DESCRIPTION
    const socialUrl = route.indexable
      ? new URL(route.canonicalPath, `${siteOrigin}/`).href
      : `${siteOrigin}/`

    document.title = route.title
    setMeta('name', 'description', route.description)
    setMeta('name', 'robots', robots)
    setMeta('name', 'googlebot', robots)
    setCanonical(route.indexable ? socialUrl : null)

    setMeta('property', 'og:type', 'website')
    setMeta('property', 'og:site_name', SITE_NAME)
    setMeta('property', 'og:locale', 'en_US')
    setMeta('property', 'og:title', socialTitle)
    setMeta('property', 'og:description', socialDescription)
    setMeta('property', 'og:url', socialUrl)
    setMeta('property', 'og:image', `${siteOrigin}${SOCIAL_IMAGE_PATH}`)
    setMeta('property', 'og:image:type', 'image/png')
    setMeta('property', 'og:image:width', '1200')
    setMeta('property', 'og:image:height', '630')
    setMeta('property', 'og:image:alt', 'LawHand law firm operations and legal AI workspace')

    setMeta('name', 'twitter:card', 'summary_large_image')
    setMeta('name', 'twitter:title', socialTitle)
    setMeta('name', 'twitter:description', socialDescription)
    setMeta('name', 'twitter:image', `${siteOrigin}${SOCIAL_IMAGE_PATH}`)
    setMeta('name', 'twitter:image:alt', 'LawHand law firm operations and legal AI workspace')

    setStructuredData(buildStructuredData(siteOrigin, pathname))
  }, [pathname])

  return null
}
