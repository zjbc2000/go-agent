"use client";

import { Badge } from "@/components/ui/badge";

/** Split a title like "[秘书]苏曼" into the badge position and the plain name. */
function splitEmployeeTitle(title: string): { position?: string; name: string } {
  const match = /^\[([^\]]+)\](.*)$/.exec(title);
  if (!match) return { name: title };
  return { position: match[1], name: match[2] || title };
}

interface SessionHeaderProps {
  title: string;
}

export function SessionHeader({ title }: SessionHeaderProps) {
  const { position, name } = splitEmployeeTitle(title);
  return (
    <div className="flex items-center gap-2 px-4 h-12 border-b border-border shrink-0 bg-surface">
      {position && (
        <Badge variant="secondary" className="text-[10px] px-1.5 py-0 shrink-0">
          {position}
        </Badge>
      )}
      <h2 className="text-sm font-medium truncate">{name}</h2>
    </div>
  );
}
