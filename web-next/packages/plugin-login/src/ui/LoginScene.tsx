/**
 * The niuu emblem: a constellation of isometric wireframe cubes, the lit
 * central cube carrying the wordmark. Ported 1:1 from the approved
 * "full curtain + cube constellation" design board.
 */
function Emblem() {
  return (
    <div className="login-scene-emblem">
      <svg
        aria-hidden="true"
        width="440"
        height="300"
        viewBox="0 0 440 300"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <g stroke="color-mix(in srgb, var(--brand-300) 35%, transparent)" strokeWidth="1.2">
          <path d="M121 71.5L165.4 98.1" />
          <path d="M279.2 99.1L297 88.9" />
          <path d="M341 106.4L358.8 165.4" />
          <path d="M174.3 177.7L155.4 195.7" />
          <path d="M54.7 128.5L76.8 84.4" />
        </g>
        <circle cx="250" cy="40" r="1.2" fill="var(--brand-200)" opacity="0.4" />
        <circle cx="60" cy="252" r="1.2" fill="var(--brand-200)" opacity="0.4" />
        <circle cx="402" cy="120" r="1.2" fill="var(--brand-200)" opacity="0.4" />
        <circle cx="180" cy="20" r="1.2" fill="var(--brand-200)" opacity="0.4" />
        <g
          stroke="color-mix(in srgb, var(--brand-500) 32%, transparent)"
          strokeWidth="2"
          strokeLinejoin="miter"
          fill="none"
        >
          <path d="M92 28L114.5 41V67L92 80L69.5 67V41Z" />
          <path d="M69.5 41L92 54L114.5 41M92 54V80" />
        </g>
        <g
          stroke="color-mix(in srgb, var(--brand-500) 45%, transparent)"
          strokeWidth="2"
          strokeLinejoin="miter"
          fill="none"
        >
          <path d="M330 40L356 55V85L330 100L304 85V55Z" />
          <path d="M304 55L330 70L356 55M330 70V100" />
        </g>
        <g
          stroke="color-mix(in srgb, var(--brand-500) 32%, transparent)"
          strokeWidth="2"
          strokeLinejoin="miter"
          fill="none"
        >
          <path d="M368 172L388.8 184V208L368 220L347.2 208V184Z" />
          <path d="M347.2 184L368 196L388.8 184M368 196V220" />
        </g>
        <g
          stroke="color-mix(in srgb, var(--brand-500) 45%, transparent)"
          strokeWidth="2"
          strokeLinejoin="miter"
          fill="none"
        >
          <path d="M128 192L154 207V237L128 252L102 237V207Z" />
          <path d="M102 207L128 222L154 207M128 222V252" />
        </g>
        <g
          stroke="color-mix(in srgb, var(--brand-500) 22%, transparent)"
          strokeWidth="1.6"
          strokeLinejoin="miter"
          fill="none"
        >
          <path d="M44 134L57.9 142V158L44 166L30.1 158V142Z" />
          <path d="M30.1 142L44 150L57.9 142M44 150V166" />
        </g>
        <path
          d="M222 74L272.2 103L222 132L171.8 103Z"
          fill="color-mix(in srgb, var(--brand-500) 8%, transparent)"
        />
        <path
          d="M222 74L272.2 103V161L222 190L171.8 161V103Z"
          stroke="var(--brand-500)"
          strokeWidth="2.5"
          strokeLinejoin="miter"
          fill="none"
        />
        <path
          d="M171.8 103L222 132L272.2 103"
          stroke="var(--brand-500)"
          strokeWidth="2.5"
          strokeLinejoin="miter"
          fill="none"
        />
      </svg>
      <h1 className="login-scene-emblem-word">niuu</h1>
    </div>
  );
}

export function LoginScene() {
  return (
    <div aria-hidden="true" className="login-scene-backdrop">
      {' '}
      <div className="login-scene-aur login-scene-aur-violet" />
      <div className="login-scene-aur login-scene-aur-main" />
      <div className="login-scene-aur login-scene-aur-teal" />
      <div className="login-scene-aur login-scene-aur-core" />
      <div className="login-scene-shaft login-scene-shaft-1" />
      <div className="login-scene-shaft login-scene-shaft-2" />
      <div className="login-scene-shaft login-scene-shaft-3" />
      <div className="login-scene-horizon" />
      <svg
        className="login-scene-stars"
        width="1440"
        height="640"
        viewBox="0 0 1440 640"
        xmlns="http://www.w3.org/2000/svg"
        preserveAspectRatio="xMidYMin slice"
      >
        <g fill="var(--brand-100)">
          <circle cx="120" cy="80" r="1.2" opacity="0.5" />
          <circle cx="260" cy="180" r="0.9" opacity="0.35" />
          <circle cx="340" cy="60" r="1.4" opacity="0.6" className="login-scene-tw" />
          <circle cx="430" cy="240" r="0.8" opacity="0.3" />
          <circle cx="520" cy="120" r="1.1" opacity="0.45" />
          <circle cx="610" cy="40" r="0.9" opacity="0.4" />
          <circle
            cx="700"
            cy="200"
            r="1.3"
            opacity="0.55"
            className="login-scene-tw login-scene-tw-d1"
          />
          <circle cx="790" cy="90" r="0.8" opacity="0.3" />
          <circle cx="880" cy="160" r="1.2" opacity="0.5" />
          <circle
            cx="960"
            cy="50"
            r="1"
            opacity="0.4"
            className="login-scene-tw login-scene-tw-d2"
          />
          <circle cx="1050" cy="220" r="0.9" opacity="0.35" />
          <circle cx="1140" cy="110" r="1.3" opacity="0.55" />
          <circle cx="1230" cy="30" r="0.9" opacity="0.4" />
          <circle
            cx="1310"
            cy="170"
            r="1.1"
            opacity="0.5"
            className="login-scene-tw login-scene-tw-d3"
          />
          <circle cx="80" cy="300" r="0.9" opacity="0.3" />
          <circle cx="200" cy="380" r="1.1" opacity="0.4" />
          <circle cx="360" cy="330" r="0.8" opacity="0.28" />
          <circle cx="1180" cy="330" r="1" opacity="0.38" />
          <circle
            cx="1330"
            cy="290"
            r="0.9"
            opacity="0.3"
            className="login-scene-tw login-scene-tw-d4"
          />
          <circle
            cx="640"
            cy="320"
            r="1"
            opacity="0.35"
            className="login-scene-tw login-scene-tw-d1"
          />
          <circle cx="900" cy="280" r="0.9" opacity="0.3" />
          <circle cx="150" cy="500" r="0.9" opacity="0.22" />
          <circle cx="1290" cy="470" r="0.8" opacity="0.2" />
        </g>
      </svg>
      <svg className="login-scene-grain" xmlns="http://www.w3.org/2000/svg">
        <filter id="loginGrain">
          <feTurbulence
            type="fractalNoise"
            baseFrequency="0.8"
            numOctaves="3"
            stitchTiles="stitch"
          />
          <feColorMatrix type="saturate" values="0" />
        </filter>
        <rect width="100%" height="100%" filter="url(#loginGrain)" />
      </svg>
    </div>
  );
}
export { Emblem };
