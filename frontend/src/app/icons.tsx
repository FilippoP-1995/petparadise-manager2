import type { SVGProps } from "react";

/**
 * Set minimale di icone stroke-based (stile Lucide, come in V1), scritte
 * a mano invece di aggiungere una nuova dipendenza npm - non serve altro
 * che questi pochi tracciati per l'header/sidebar/bottom-nav.
 */
function Icon({ children, ...props }: SVGProps<SVGSVGElement> & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      width={20}
      height={20}
      {...props}
    >
      {children}
    </svg>
  );
}

export const CalendarIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="3" y="4" width="18" height="18" rx="2" />
    <path d="M16 2v4M8 2v4M3 10h18" />
  </Icon>
);

export const ClipboardIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="6" y="4" width="12" height="17" rx="2" />
    <path d="M9 4V3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v1M9 12h6M9 16h6" />
  </Icon>
);

export const TruckIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="2" y="7" width="13" height="10" rx="1" />
    <path d="M15 10h3l3 3v4h-6z" />
    <circle cx="7" cy="19" r="1.6" />
    <circle cx="17" cy="19" r="1.6" />
  </Icon>
);

export const PackageIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M21 8l-9-5-9 5 9 5 9-5z" />
    <path d="M3 8v8l9 5 9-5V8M12 13v8" />
  </Icon>
);

export const FlameIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M12 22c4 0 6-2.7 6-6.3 0-3-2-5-3.3-7C13.9 6.7 14 4 12 2c0 3-2 4.6-3.5 7C7 11.3 6 13.3 6 15.7 6 19.3 8 22 12 22z" />
    <path d="M12 18a2.6 2.6 0 0 0 2.6-2.6c0-1.4-1-2.2-1.7-3.2-.5.8-1.4 1.5-1.4 2.7A2.6 2.6 0 0 0 12 18z" />
  </Icon>
);

export const ReceiptIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M5 3h14v18l-2.5-1.5L14 21l-2-1.5L10 21l-2.5-1.5L5 21V3z" />
    <path d="M8 8h8M8 12h8M8 16h5" />
  </Icon>
);

export const UsersIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx="9" cy="8" r="3.2" />
    <path d="M2.5 20c.6-3.4 3.2-5.5 6.5-5.5s5.9 2.1 6.5 5.5" />
    <circle cx="17" cy="8.5" r="2.5" />
    <path d="M16.5 14.7c2.6.4 4.6 2.3 5 5.3" />
  </Icon>
);

export const StethoscopeIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M5 3v6a4 4 0 0 0 8 0V3" />
    <path d="M9 13v2a5 5 0 0 0 10 0v-2.5" />
    <circle cx="19" cy="8" r="1.7" />
  </Icon>
);

export const BuildingIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="4" y="3" width="16" height="18" rx="1" />
    <path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1M10 21v-4h4v4" />
  </Icon>
);

export const ArchiveIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="3" y="4" width="18" height="4" rx="1" />
    <path d="M4 8v11a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1V8M10 13h4" />
  </Icon>
);

export const BoxIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <rect x="4" y="8" width="16" height="12" rx="1" />
    <path d="M4 8l2.5-4h11L20 8M12 12v8" />
  </Icon>
);

export const MoreIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <circle cx="5" cy="12" r="1.6" />
    <circle cx="12" cy="12" r="1.6" />
    <circle cx="19" cy="12" r="1.6" />
  </Icon>
);

export const LogOutIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
  </Icon>
);

export const XIcon = (props: SVGProps<SVGSVGElement>) => (
  <Icon {...props}>
    <path d="M18 6 6 18M6 6l12 12" />
  </Icon>
);
