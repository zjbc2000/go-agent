import { describe, it, expect } from "vitest";
import { createMockPlanningRepository } from "@/lib/mock/planning-repository";

describe("MockPlanningRepository", () => {
  const repo = createMockPlanningRepository();

  it("lists all documents by default", async () => {
    const docs = await repo.listDocuments();
    expect(docs.length).toBeGreaterThanOrEqual(4);
  });

  it("filters by category", async () => {
    const docs = await repo.listDocuments({ category: "interest" });
    expect(docs.every((d) => d.category === "interest")).toBe(true);
  });

  it("filters by search", async () => {
    const docs = await repo.listDocuments({ search: "Rust" });
    expect(docs.length).toBeGreaterThan(0);
    expect(docs.some((d) => d.title.includes("Rust") || d.content.includes("Rust"))).toBe(true);
  });

  it("updates a document and increments version", async () => {
    const docs = await repo.listDocuments();
    const original = docs[0];
    const updated = await repo.updateDocument(original.id, { title: "Updated Title" });
    expect(updated.title).toBe("Updated Title");
    expect(updated.version).toBe(original.version + 1);
  });

  it("throws when updating non-existent document", async () => {
    await expect(repo.updateDocument("nonexistent", { title: "Nope" })).rejects.toThrow();
  });

  it("lists versions for a document", async () => {
    const versions = await repo.listVersions("doc_1");
    expect(versions.length).toBeGreaterThanOrEqual(2);
    expect(versions[0]).toHaveProperty("title");
    expect(versions[0]).toHaveProperty("content");
  });

  it("restores a version", async () => {
    const versions = await repo.listVersions("doc_1");
    const target = versions[0]; // oldest version
    await repo.restoreVersion("doc_1", target.id);
    // Document content should reflect the restored version
    const docs = await repo.listDocuments();
    const restored = docs.find((d) => d.id === "doc_1");
    expect(restored?.title).toBe(target.title);
    expect(restored?.content).toBe(target.content);
    // Restore switches current_version to the target (no new version row).
    expect(restored?.version).toBe(target.version);
  });
});
