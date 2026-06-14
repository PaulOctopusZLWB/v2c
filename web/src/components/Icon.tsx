// Inline SVG icon set — 16px, 1.5 stroke, currentColor. No dependency.
const P: Record<string, React.ReactNode> = {
  device: <><rect x="4" y="3" width="16" height="13" rx="2" /><path d="M8 20h8M12 16v4" /></>,
  import: <><path d="M12 3v12M7 10l5 5 5-5" /><path d="M5 21h14" /></>,
  play: <path d="M7 5l12 7-12 7z" fill="currentColor" stroke="none" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />,
  run: <path d="M7 5l12 7-12 7z" fill="currentColor" stroke="none" />,
  accept: <path d="M5 13l4 4L19 7" />,
  reject: <path d="M6 6l12 12M18 6L6 18" />,
  flag: <><path d="M5 21V4" /><path d="M5 4h11l-2 4 2 4H5" /></>,
  person: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20a7 7 0 0 1 14 0" /></>,
  refresh: <><path d="M4 12a8 8 0 0 1 14-5l2 2" /><path d="M20 5v4h-4" /><path d="M20 12a8 8 0 0 1-14 5l-2-2" /><path d="M4 19v-4h4" /></>,
  viewpoint: <path d="M12 3l2.4 5.6L20 11l-5.6 2.4L12 21l-2.4-7.6L4 11l5.6-2.4z" />,
  mic: <><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0M12 18v3" /></>,
  chevron: <path d="M9 6l6 6-6 6" />,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3 2" /></>,
  link: <><path d="M9 15l6-6" /><path d="M11 6l1-1a4 4 0 0 1 6 6l-1 1" /><path d="M13 18l-1 1a4 4 0 0 1-6-6l1-1" /></>,
  check_circle: <><circle cx="12" cy="12" r="9" /><path d="M8 12l3 3 5-6" /></>,
  inbox: <><path d="M4 13l2-8h12l2 8v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2z" /><path d="M4 13h4l1 2h6l1-2h4" /></>,
};

export function Icon({ name, className = "icon" }: { name: keyof typeof P | string; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {P[name] ?? null}
    </svg>
  );
}
