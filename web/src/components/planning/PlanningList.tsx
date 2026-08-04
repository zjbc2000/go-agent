"use client";

import { useEffect, useState, useCallback } from "react";
import type { PlanningCategory, PlanningDocument as PlanningDocumentType } from "@/lib/domain/types";
import { useRepositories } from "@/lib/providers/repository-context";
import { PlanningFilters } from "./PlanningFilters";
import { PlanningDocument } from "./PlanningDocument";
import { Loader2, FileText } from "lucide-react";

export function PlanningList() {
  const [documents, setDocuments] = useState<PlanningDocumentType[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<PlanningCategory>("all");
  const { planning: planningRepo } = useRepositories();

  const loadDocuments = useCallback(async () => {
    setLoading(true);
    try {
      const docs = await planningRepo.listDocuments(
        filter !== "all" ? { category: filter } : undefined,
      );
      setDocuments(docs);
    } finally {
      setLoading(false);
    }
  }, [filter, planningRepo]);

  useEffect(() => {
    loadDocuments();
  }, [loadDocuments]);

  return (
    <div className="space-y-4">
      <PlanningFilters active={filter} onChange={setFilter} />

      {loading ? (
        <div className="flex items-center justify-center gap-2 py-12 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-sm">加载中...</span>
        </div>
      ) : documents.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 py-12 text-muted-foreground">
          <FileText className="h-8 w-8" />
          <p className="text-sm">暂无规划文档</p>
          <p className="text-xs">在对话中确认的计划草稿会出现在这里</p>
        </div>
      ) : (
        <div className="grid gap-3">
          {documents.map((doc) => (
            <PlanningDocument
              key={doc.id}
              document={doc}
              onUpdated={loadDocuments}
            />
          ))}
        </div>
      )}
    </div>
  );
}
