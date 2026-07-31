const assetBase = '/brand/wellpled'

/**
 * Shared WellPled logo lockup for product and marketing surfaces.
 */
export default function WellPledLogo({
  className = '',
  compact = false,
  markOnly = false,
  reversed = false,
  showTagline = false,
}) {
  const inkClass = reversed ? 'text-brand-bg' : 'text-brand-ink'
  const taglineClass = reversed ? 'text-brand-bg/75' : 'text-brand-accent'
  const mark = reversed ? 'wellpled-mark-reversed.svg' : 'wellpled-mark.svg'

  return (
    <div
      className={`inline-flex items-center ${compact ? 'gap-2' : 'gap-3'} ${className}`}
      aria-label="WellPled"
    >
      <img
        src={`${assetBase}/${mark}`}
        alt=""
        aria-hidden="true"
        className={compact ? 'h-7 w-auto' : 'h-10 w-auto'}
      />
      {!markOnly && <span className="min-w-0">
        <span
          className={`block font-serif font-medium tracking-[-0.035em] leading-none ${inkClass} ${
            compact ? 'text-xl' : 'text-3xl'
          }`}
        >
          WellPled
        </span>
        {showTagline && !compact && (
          <span className={`mt-1.5 block font-sans text-xs font-medium tracking-wide ${taglineClass}`}>
            Practice, well played.
          </span>
        )}
      </span>}
    </div>
  )
}
