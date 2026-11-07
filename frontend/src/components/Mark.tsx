/** Original mark. Not the grok.com glyph. */
export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect x="4" y="6" width="16" height="20" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
      <rect x="12" y="4" width="16" height="20" rx="4" fill="none" stroke="currentColor" strokeWidth="2" />
    </svg>
  );
}
