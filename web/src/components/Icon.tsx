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
  trash: <><path d="M4 7h16" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" /><path d="M10 11v6M14 11v6" /></>,
  volume: <><path d="M4 10v4h4l5 4V6l-5 4H4z" /><path d="M16 9a4 4 0 0 1 0 6" /><path d="M18.5 6.5a7.5 7.5 0 0 1 0 11" /></>,
  noise: <><path d="M4 12h16" /><path d="M7 8l10 8" /><path d="M7 16L17 8" /><circle cx="12" cy="12" r="8" /></>,
  map: <><path d="M4 6l5-2 6 2 5-2v14l-5 2-6-2-5 2V6z" /><path d="M9 4v14M15 6v14" /></>,
  search: <><circle cx="11" cy="11" r="6" /><path d="M16 16l4 4" /></>,
  sun: <><circle cx="12" cy="12" r="4.2" /><path d="M12 2v2.5M12 19.5V22M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8l1.8-1.8M18 6l1.8-1.8" /></>,
  moon: <path d="M20 13.5A8 8 0 1 1 10.5 4a6.3 6.3 0 0 0 9.5 9.5z" />,
};

export function Icon({ name, className = "icon" }: { name: keyof typeof P | string; className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {P[name] ?? <path className="icon-missing" d="M12 12h0" />}
    </svg>
  );
}
