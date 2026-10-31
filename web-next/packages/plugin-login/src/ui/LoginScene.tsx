import { NiuuMark, NiuuWordmark } from '@niuulabs/ui';

const EMBLEM_MARK_SIZE = 120;
const EMBLEM_WORDMARK_HEIGHT = 34;

/** The NIUU primary lockup: orbit mark over the path-drawn wordmark. */
function Emblem() {
  return (
    <div className="login-scene-emblem">
      <NiuuMark size={EMBLEM_MARK_SIZE} />
      <h1 className="login-scene-emblem-word" aria-label="NIUU">
        <NiuuWordmark height={EMBLEM_WORDMARK_HEIGHT} />
      </h1>
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
