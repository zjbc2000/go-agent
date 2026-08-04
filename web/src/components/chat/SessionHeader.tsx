"use client";

interface SessionHeaderProps {
  title: string;
}

export function SessionHeader({ title }: SessionHeaderProps) {
  return (
    <div className="flex items-center gap-2 px-4 h-12 border-b border-border shrink-0 bg-surface">
      <h2 className="text-sm font-medium truncate">{title}</h2>
    </div>
  );
}
