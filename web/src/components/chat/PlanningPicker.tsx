"use client";

import { useEffect, useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useRepositories } from "@/lib/providers/repository-context";
import type { PlanningDocument } from "@/lib/domain/types";

interface PlanningPickerProps {
  selected: PlanningDocument[];
  onToggle: (doc: PlanningDocument) => void;
}

/**
 * "➕" button opening a planning-document picker. Multi-select; selected docs are
 * shown as removable chips by the Composer. Loads docs via planning.listDocuments.
 */
export function PlanningPicker({ selected, onToggle }: PlanningPickerProps) {
  const { planning } = useRepositories();
  const [open, setOpen] = useState(false);
  const [docs, setDocs] = useState<PlanningDocument[]>([]);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // Defer the loading flag so we don't setState synchronously inside the effect
    // (React Compiler rejects cascading renders from synchronous setState in effects).
    queueMicrotask(() => {
      if (!cancelled) setLoading(true);
    });
    planning
      .listDocuments(search.trim() ? { search: search.trim() } : undefined)
      .then((rows) => {
        if (!cancelled) setDocs(rows);
      })
      .catch(() => {
        if (!cancelled) setDocs([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // planning is a stable repository-context value in the app; in tests it is a
    // per-render mock, so depending on it would re-run the effect every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, search]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={
          <Button
            variant="outline"
            size="icon"
            aria-label="引用规划文档"
            className="shrink-0"
          >
            <Plus className="h-4 w-4" />
          </Button>
        }
      />
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>引用规划文档</DialogTitle>
          <DialogDescription>选择要附加到消息的规划文档，可多选</DialogDescription>
        </DialogHeader>

        <Input
          placeholder="搜索规划..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="搜索规划"
        />

        <ScrollArea className="max-h-64">
          {loading ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">加载中...</p>
          ) : docs.length === 0 ? (
            <p className="px-2 py-3 text-sm text-muted-foreground">暂无规划文档</p>
          ) : (
            <div className="space-y-1">
              {docs.map((doc) => (
                <label
                  key={doc.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent"
                >
                  <Checkbox
                    checked={selected.some((s) => s.id === doc.id)}
                    onCheckedChange={() => onToggle(doc)}
                  />
                  <span className="min-w-0 flex-1 truncate">{doc.title}</span>
                  <Badge variant="outline" className="shrink-0">
                    {doc.category}
                  </Badge>
                </label>
              ))}
            </div>
          )}
        </ScrollArea>

        <DialogFooter>
          <DialogClose render={<Button variant="outline">取消</Button>} />
          <DialogClose render={<Button variant="default">完成</Button>} />
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
