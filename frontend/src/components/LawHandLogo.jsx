const assetBase = '/brand/lawhand'

/**
 * Shared LawHand logo lockup for product and marketing surfaces.
 */
export default function LawHandLogo({
  className = '',
  compact = false,
  markOnly = false,
  reversed = false,
  showTagline = false,
}) {
  const inkClass = reversed ? 'text-brand-bg' : 'text-brand-ink'
  const taglineClass = reversed ? 'text-white/70' : 'text-brand-muted'
  const mark = reversed ? 'lawhand-mark-reversed.svg' : 'lawhand-mark.svg'

  return (
    <div
      className={`inline-flex items-center ${compact ? 'gap-2' : 'gap-3'} ${className}`}
      aria-label="LawHand"
    >
      <img
        src={`${assetBase}/${mark}`}
        alt=""
        aria-hidden="true"
        className={compact ? 'h-7 w-7' : 'h-10 w-10'}
      />
      {!markOnly && <span className="min-w-0">
        <span
          className={`block font-sans font-medium tracking-[-0.045em] leading-none ${inkClass} ${
            compact ? 'text-xl' : 'text-3xl'
          }`}
        >
          lawhand
        </span>
        {showTagline && !compact && (
          <span className={`mt-1.5 block font-sans text-xs font-medium tracking-wide ${taglineClass}`}>
            The whole matter, in hand.
          </span>
        )}
      </span>}
    </div>
  )
}
