"use client";

import { useEffect, useState } from "react";
import type { DocumentVersion } from "@/lib/domain/types";
import { useRepositories } from "@/lib/providers/repository-context";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { RotateCcw, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

interface VersionHistoryProps {
  documentId: string;
  onRestored: () => void;
}

export function VersionHistory({ documentId, onRestored }: VersionHistoryProps) {
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const { planning: planningRepo } = useRepositories();

  useEffect(() => {
    planningRepo
      .listVersions(documentId)
      .then(setVersions)
      .finally(() => setLoading(false));
  }, [documentId, planningRepo]);

  const handleRestore = async (versionId: string) => {
    setRestoring(versionId);
    try {
      await planningRepo.restoreVersion(documentId, versionId);
      toast.success("版本已恢复");
      // Re-fetch the version list so the newly created version appears here, then
      // let the document list refresh its current version number.
      const rows = await planningRepo.listVersions(documentId);
      setVersions(rows);
      onRestored();
    } catch {
      toast.error("恢复失败");
    } finally {
      setRestoring(null);
    }
  };

  const handleDeleteVersion = async (versionId: string, isCurrent: boolean) => {
    if (isCurrent) {
      toast.error("当前版本不可删除");
      return;
    }
    setDeleting(versionId);
    try {
      await planningRepo.deleteVersion(documentId, versionId);
      toast.success("版本已删除");
      const rows = await planningRepo.listVersions(documentId);
      setVersions(rows);
      onRestored();
    } catch {
      toast.error("删除失败");
    } finally {
      setDeleting(null);
    }
  };

  const currentVersion =
    versions.length > 0 ? Math.max(...versions.map((v) => v.version)) : 0;

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-2">
        <Loader2 className="h-3 w-3 animate-spin" />
        加载历史版本...
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <p className="text-xs text-muted-foreground py-2">暂无历史版本</p>
    );
  }

  return (
    <div>
      <p className="text-xs font-medium text-foreground mb-2">历史版本</p>
      <ScrollArea className="max-h-48">
        <div className="space-y-1.5">
          {[...versions].reverse().map((v) => {
            const isCurrent = v.version === currentVersion;
            return (
              <div
                key={v.id}
                className="flex items-center justify-between rounded-md bg-background px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-xs font-medium truncate">{v.title}</p>
                  <p className="text-[10px] text-muted-foreground">
                    v{v.version} · {new Date(v.createdAt).toLocaleDateString("zh-CN")}
                    {isCurrent ? " · 当前" : ""}
                  </p>
                </div>
                <div className="flex items-center gap-0.5 shrink-0 ml-2">
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6"
                    aria-label={`恢复版本 ${v.version}`}
                    onClick={() => handleRestore(v.id)}
                    disabled={restoring === v.id}
                  >
                    {restoring === v.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3 w-3" />
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-6 w-6 text-muted-foreground hover:text-destructive"
                    aria-label={`删除版本 ${v.version}`}
                    onClick={() => handleDeleteVersion(v.id, isCurrent)}
                    disabled={deleting === v.id || isCurrent}
                  >
                    {deleting === v.id ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <Trash2 className="h-3 w-3" />
                    )}
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
