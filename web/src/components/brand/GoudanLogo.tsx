"use client";

import { cn } from "@/lib/utils";

interface GoudanLogoProps {
  className?: string;
  size?: number;
  showText?: boolean;
}

/**
 * Logo using currentColor — no hydration mismatch.
 * CSS variables handle light/dark theme automatically.
 */
export function GoudanLogo({ className, size = 32, showText = true }: GoudanLogoProps) {
  return (
    <div className={cn("flex items-center gap-2 text-foreground", className)}>
      <svg
        xmlns="http://www.w3.org/2000/svg"
        width={size}
        height={size}
        viewBox="0 0 256 256"
        fill="none"
        role="img"
        aria-label="苟蛋 Logo"
      >
        <title>苟蛋 Goudan</title>
        <g stroke="currentColor" strokeWidth="7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M70 103 77 57c2-18 12-29 25-28 11 1 16 13 19 26 18-5 38-5 55 0 8-9 17-19 30-16 15 4 14 20 9 36l-8 29c16 17 21 40 15 61-5 18-17 31-35 38" fill="none" />
          <path d="M70 103c-14 17-20 39-14 61 5 18 16 31 34 39" fill="none" />
          <path d="M90 203c-10 8-16 20-15 33 1 12 10 18 23 17 10 0 16-5 22-12 8 8 18 12 29 12 12 0 19-4 26-11 7 6 16 10 26 8 13-2 18-12 14-26-3-10-9-17-18-21" fill="none" />
          <path d="M93 184c7 7 18 10 28 10h20c13 0 25-3 33-10" fill="none" />
          <path d="M104 119c-7-4-16-3-21 3-5 7-3 17 4 22 8 5 18 3 22-5" fill="currentColor" />
          <path d="M151 119c7-4 16-3 21 3 5 7 3 17-4 22-8 5-18 3-22-5" fill="currentColor" />
          <path d="M128 144c5 0 9 4 9 9s-4 9-9 9-9-4-9-9 4-9 9-9Z" fill="currentColor" />
          <path d="M128 162v6c0 9-8 14-16 14-8 0-14-4-16-11" fill="none" />
          <path d="M128 168c0 9 8 14 16 14 8 0 14-4 16-11" fill="none" />
          <path d="M119 211c7-4 14-4 21 0" fill="none" />
          <path d="M33 243h190" fill="none" />
        </g>
      </svg>
      {showText && (
        <span className="font-semibold text-base">苟蛋</span>
      )}
    </div>
  );
}
