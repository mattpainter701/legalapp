/**
 * One hue per card, used identically everywhere a card appears.
 *
 * The reason a template builder reads at a glance is that the subject a blank
 * belongs to is the same colour in the rail, in the document chip, and on the
 * band around a conditional region. Deriving the hue from the card key rather
 * than storing it means a card added to the server catalogue gets a colour
 * without a matching frontend change — and gets the same one on every screen.
 */

// Hand-placed hues for the cards that exist today, so the common ones are
// distinguishable rather than merely distinct. Anything else hashes into the
// wheel, avoiding the reserved band so a new card cannot collide with these.
const ASSIGNED = {
  firm: 210,
  matter: 262,
  billing: 28,
  client: 158,
  plaintiff: 200,
  defendant: 12,
  attorney: 288,
  preparer: 320,
  item: 96,
}

const RESERVED = new Set(Object.values(ASSIGNED))

export const cardHue = (key) => {
  if (!key) return 220
  if (key in ASSIGNED) return ASSIGNED[key]
  let hash = 0
  for (let index = 0; index < key.length; index += 1) {
    hash = (hash * 31 + key.charCodeAt(index)) % 360
  }
  // Walk off a reserved hue rather than accept a collision with a named card.
  let hue = hash
  for (let step = 0; step < 360 && RESERVED.has(hue); step += 1) hue = (hue + 7) % 360
  return hue
}

/**
 * CSS custom properties for one card. Applied to a container so the chip,
 * the dot, and the region band can all read the same three values without
 * each recomputing the hue.
 */
export const cardStyle = (key) => {
  const hue = cardHue(key)
  return {
    '--card-hue': String(hue),
    '--card-accent': `hsl(${hue} 62% 45%)`,
    '--card-wash': `hsl(${hue} 70% 95%)`,
  }
}
