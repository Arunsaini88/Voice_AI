// src/Brand.tsx — app name with a microphone mark, used in the top bar and login card.
export function MicIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8" />
    </svg>
  );
}

export default function Brand() {
  return (
    <div className="brand">
      <MicIcon />
      <h1>Voice Notes</h1>
    </div>
  );
}
