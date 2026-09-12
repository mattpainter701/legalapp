// Shared field geometry for every surface that places template variables on a
// document. The intake wizard and Template Studio must agree byte-for-byte on
// how a canvas rectangle becomes a persisted `pdf_overlays` rect, so this math
// lives in exactly one place.

export const MIN_FIELD_SIZE = 12
export const VARIABLE_NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_.-]*$/

export const roundCoordinate = (value) => Math.round(Number(value) * 1000) / 1000
export const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value))

// Overlay rects are PDF points with a bottom-left origin; canvas rects are CSS
// pixels with a top-left origin. Prefer pdf.js' own viewport transform when we
// have one, because it also carries page rotation.
export const overlayToCanvasRect = (overlay, page, viewport = null, scale = 1) => {
  const [left, bottom, right, top] = overlay?.rect || [0, 0, 120, 24]
  if (viewport?.convertToViewportRectangle) {
    const converted = viewport.convertToViewportRectangle([left, bottom, right, top])
    const xs = [converted[0], converted[2]]
    const ys = [converted[1], converted[3]]
    return {
      x: Math.min(...xs),
      y: Math.min(...ys),
      width: Math.max(MIN_FIELD_SIZE, Math.abs(xs[1] - xs[0])),
      height: Math.max(MIN_FIELD_SIZE, Math.abs(ys[1] - ys[0])),
    }
  }
  const height = Number(page?.height) || 792
  return {
    x: left * scale,
    y: (height - top) * scale,
    width: Math.max(MIN_FIELD_SIZE, (right - left) * scale),
    height: Math.max(MIN_FIELD_SIZE, (top - bottom) * scale),
  }
}

export const canvasToOverlayRect = (geometry, page, viewport = null, scale = 1) => {
  const { x, y, width, height } = geometry
  if (viewport?.convertToPdfPoint) {
    const corners = [
      viewport.convertToPdfPoint(x, y),
      viewport.convertToPdfPoint(x + width, y),
      viewport.convertToPdfPoint(x, y + height),
      viewport.convertToPdfPoint(x + width, y + height),
    ]
    const xs = corners.map((point) => point[0])
    const ys = corners.map((point) => point[1])
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)].map(roundCoordinate)
  }
  const safeScale = scale || 1
  const pageHeight = Number(page?.height) || 792
  const unscaled = {
    x: x / safeScale,
    y: y / safeScale,
    width: width / safeScale,
    height: height / safeScale,
  }
  return [
    unscaled.x,
    pageHeight - unscaled.y - unscaled.height,
    unscaled.x + unscaled.width,
    pageHeight - unscaled.y,
  ].map(roundCoordinate)
}

export const isImageFile = (file) => Boolean(
  file && (String(file.type || '').startsWith('image/') || /\.(png|jpe?g|tiff?|webp)$/i.test(file.name || '')),
)

export const isPdfFile = (file) => Boolean(
  file && (file.type === 'application/pdf' || /\.pdf$/i.test(file.name || '')),
)

export const fieldIdentity = (field, index = 0) => (
  field?.pdf_source_key
  || field?.pdf_field_name
  || field?._bodyName
  || `${field?.name || 'field'}:${index}`
)

export const placementsFor = (field) => {
  if (Array.isArray(field?.pdf_overlays) && field.pdf_overlays.length) {
    return field.pdf_overlays.map((overlay, index) => ({ overlay, index }))
  }
  if (field?.pdf_overlay) return [{ overlay: field.pdf_overlay, index: 0 }]
  if (Array.isArray(field?.rect) && field.rect.length === 4) {
    return [{
      overlay: {
        page: Number(field.page) || 1,
        rect: field.rect,
        source_kind: 'acroform',
        erase_source: false,
      },
      index: 0,
    }]
  }
  return []
}

export const sourceKind = (field) => (
  field?.pdf_field_name
    ? 'acroform'
    : placementsFor(field)[0]?.overlay?.source_kind || field?.source_kind || 'text'
)

export const firstPageFor = (field) => Number(
  field?.page || placementsFor(field)[0]?.overlay?.page || 1,
) || 1

export const makeUuid = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  const bytes = new Uint8Array(16)
  if (globalThis.crypto?.getRandomValues) globalThis.crypto.getRandomValues(bytes)
  else bytes.forEach((_, index) => { bytes[index] = Math.floor(Math.random() * 256) })
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export const nextFieldName = (fields) => {
  const existing = new Set(fields.map((field) => field.name))
  let suffix = fields.length + 1
  while (existing.has(`field_${suffix}`)) suffix += 1
  return `field_${suffix}`
}

// Default signing-field sizes in PDF points. A signature block a client can
// actually read is roughly the size Acrobat and the e-sign vendors use: wide
// enough for a full name on a ruled line, tall enough for the role caption
// above it. The old 120x24 strip looked like a typo on a letter-size page.
export const SIGNING_FIELD_SIZES = {
  signature: { width: 230, height: 56 },
  initials: { width: 96, height: 56 },
  date: { width: 150, height: 44 },
}

// Below this the ruled line and caption stop being legible, so resizing is
// clamped here rather than at the generic 12pt floor.
export const SIGNING_FIELD_MIN_SIZES = {
  signature: { width: 96, height: 30 },
  initials: { width: 44, height: 30 },
  date: { width: 64, height: 24 },
}

const SIGNING_FIELD_MARGIN = 54
const SIGNING_FIELD_GAP = 12

// Lay new signing blocks out on a grid of uniform slots sized for the widest
// block, starting at the foot of the page and filling left to right, then
// upward. Uniform slots are what keeps a signature and an initials block from
// landing on top of each other, which a simple diagonal cascade did.
export const createSigningFieldRect = (fieldType, { page, pageNumber, fields = [] }) => {
  const size = SIGNING_FIELD_SIZES[fieldType] || SIGNING_FIELD_SIZES.signature
  const pageWidth = Number(page?.width) || 612
  const pageHeight = Number(page?.height) || 792
  const usableWidth = Math.max(MIN_FIELD_SIZE, pageWidth - SIGNING_FIELD_MARGIN * 2)
  const usableHeight = Math.max(MIN_FIELD_SIZE, pageHeight - SIGNING_FIELD_MARGIN * 2)
  const width = Math.min(size.width, usableWidth)
  const height = Math.min(size.height, usableHeight)
  const slot = {
    width: Math.min(SIGNING_FIELD_SIZES.signature.width, usableWidth) + SIGNING_FIELD_GAP,
    height: Math.min(SIGNING_FIELD_SIZES.signature.height, usableHeight) + SIGNING_FIELD_GAP,
  }
  const bottomRow = pageHeight * 0.12
  const columns = Math.max(1, Math.floor((usableWidth + SIGNING_FIELD_GAP) / slot.width))
  const rows = Math.max(1, Math.floor((pageHeight - bottomRow - SIGNING_FIELD_MARGIN) / slot.height))
  const placed = fields.filter((field) => Number(field?.page) === Number(pageNumber)).length
  const cell = placed % (columns * rows)
  const left = clamp(
    SIGNING_FIELD_MARGIN + (cell % columns) * slot.width,
    0,
    Math.max(0, pageWidth - width),
  )
  const bottom = clamp(
    bottomRow + Math.floor(cell / columns) * slot.height,
    0,
    Math.max(0, pageHeight - height),
  )
  return [left, bottom, left + width, bottom + height].map(roundCoordinate)
}

const PREFERRED_DIMENSIONS = {
  checkbox: { width: 20, height: 20 },
  signature: SIGNING_FIELD_SIZES.signature,
  multiline: { width: 200, height: 64 },
}

// Build a brand-new manual placement. Kept beside the coordinate math because
// the starting rect has to obey the same bottom-left point space.
export const createManualField = (kind, { page, pageNumber, fields }) => {
  const id = makeUuid()
  const name = nextFieldName(fields)
  const pageWidth = Number(page?.width || 612)
  const pageHeight = Number(page?.height || 792)
  const preferred = PREFERRED_DIMENSIONS[kind] || { width: 160, height: 26 }
  const dimensions = {
    width: Math.min(preferred.width, Math.max(MIN_FIELD_SIZE, pageWidth - 8)),
    height: Math.min(preferred.height, Math.max(MIN_FIELD_SIZE, pageHeight - 8)),
  }
  const left = clamp(pageWidth * 0.12, 4, Math.max(4, pageWidth - dimensions.width - 4))
  const top = clamp(
    pageHeight * 0.84,
    dimensions.height + 4,
    Math.max(dimensions.height + 4, pageHeight - 4),
  )
  const overlay = {
    page: pageNumber,
    rect: [left, top - dimensions.height, left + dimensions.width, top].map(roundCoordinate),
    source_kind: 'manual',
    erase_source: false,
  }
  return {
    name,
    label: `New ${kind === 'multiline' ? 'paragraph' : kind} field`,
    field_type: kind === 'multiline' ? 'text' : kind,
    required: false,
    multiline: kind === 'multiline',
    page: pageNumber,
    source_kind: 'manual',
    pdf_source_key: `manual:${id}`,
    erase_source: false,
    included: true,
    pdf_overlay: overlay,
    pdf_overlays: [overlay],
    confidence: 1,
    review_required: false,
    _bodyName: name,
  }
}

export const createCoverRegion = ({ page, pageNumber }) => {
  const pageWidth = Number(page?.width || 612)
  const pageHeight = Number(page?.height || 792)
  const width = Math.min(180, pageWidth - 8)
  const height = Math.min(24, pageHeight - 8)
  const left = clamp(pageWidth * 0.12, 4, Math.max(4, pageWidth - width - 4))
  const top = clamp(pageHeight * 0.84, height + 4, Math.max(height + 4, pageHeight - 4))
  return { page: pageNumber, rect: [left, top - height, left + width, top].map(roundCoordinate), source_kind: 'manual', erase_source: true }
}

export const coverRegionIdentity = (region, index = 0) => region?.id || `cover:${index}:${(region?.page || 1)}:${(region?.rect || []).join(',')}`

// Clamp a dragged/resized rect to the canvas, then convert it back to points.
export const geometryToOverlays = (field, placementIndex, geometry, {
  page,
  pageNumber,
  viewport,
  scale,
  canvasWidth,
  canvasHeight,
}) => {
  const boundedX = clamp(geometry.x, 0, Math.max(0, canvasWidth - MIN_FIELD_SIZE))
  const boundedY = clamp(geometry.y, 0, Math.max(0, canvasHeight - MIN_FIELD_SIZE))
  const bounded = {
    x: boundedX,
    y: boundedY,
    width: clamp(geometry.width, MIN_FIELD_SIZE, Math.max(MIN_FIELD_SIZE, canvasWidth - boundedX)),
    height: clamp(geometry.height, MIN_FIELD_SIZE, Math.max(MIN_FIELD_SIZE, canvasHeight - boundedY)),
  }
  const placements = placementsFor(field)
  const existing = placements[placementIndex]?.overlay || {
    page: pageNumber,
    source_kind: 'manual',
    erase_source: false,
  }
  const nextOverlay = {
    ...existing,
    page: Number(existing.page) || pageNumber,
    rect: canvasToOverlayRect(bounded, page, viewport, scale),
  }
  const overlays = placements.length
    ? placements.map((placement) => ({ ...placement.overlay }))
    : [nextOverlay]
  overlays[placementIndex] = nextOverlay
  return overlays
}
