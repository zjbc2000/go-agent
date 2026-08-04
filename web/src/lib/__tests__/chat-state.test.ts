import { describe, it, expect } from "vitest";
import { generateRequestId, generateId } from "@/lib/utils/id";

describe("generateRequestId", () => {
  it("produces unique ids with req_ prefix", () => {
    const id1 = generateRequestId();
    const id2 = generateRequestId();
    expect(id1).toMatch(/^req_\d+_\d+$/);
    expect(id2).toMatch(/^req_\d+_\d+$/);
    expect(id1).not.toBe(id2);
  });
});

describe("generateId", () => {
  it("produces unique ids with given prefix", () => {
    const id1 = generateId("msg");
    const id2 = generateId("msg");
    expect(id1).toMatch(/^msg_\d+_/);
    expect(id2).toMatch(/^msg_\d+_/);
    expect(id1).not.toBe(id2);
  });

  it("supports different prefixes", () => {
    const a = generateId("sess");
    const b = generateId("ver");
    expect(a).toMatch(/^sess_/);
    expect(b).toMatch(/^ver_/);
  });
});
