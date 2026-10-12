// The JÁRN logo: anvil mark, steel wordmark with the lit Á, and tagline. Used in the sidebar and on the sign-in page.
// Styles live in App.css (logo classes) and SignIn.css (the large size).
export function JarnBrand({ large = false }: { large?: boolean }) {
  return (
    <div className={`logo${large ? ' logo-large' : ''}`} role="img" aria-label="JÁRN, forged patching">
      <svg className="logo-icon" viewBox="0 0 40 40" aria-hidden="true">
        <defs>
          <linearGradient id="jarn-steel" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#ffffff" />
            <stop offset="0.5" stopColor="#c9d2dd" />
            <stop offset="1" stopColor="#7d8a9b" />
          </linearGradient>
        </defs>
        <path className="logo-anvil" d="M4 9h32v6h-8v5h4l3 7H10l3-7h7v-5H4z" fill="url(#jarn-steel)" />
        <rect x="9" y="30" width="22" height="4" rx="1" fill="#4a8fd4" />
        <circle className="logo-spark logo-spark-1" cx="31" cy="5" r="1.6" />
        <circle className="logo-spark logo-spark-2" cx="36" cy="10" r="1.1" />
        <circle className="logo-spark logo-spark-3" cx="27" cy="2" r="1" />
      </svg>
      <div className="logo-wordmark">
        <span className="logo-text" aria-hidden="true">
          J<span className="logo-accent">Á</span>RN
        </span>
        <span className="logo-tagline" aria-hidden="true">Forged patching</span>
      </div>
    </div>
  )
}
