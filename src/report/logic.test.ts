import { describe, expect, it } from "vitest";
import { canSaveDiagnostics } from "./logic";

describe("canSaveDiagnostics", () => {
  it("requires a non-empty problem description", () => {
    expect(canSaveDiagnostics("  ")).toBe(false);
    expect(canSaveDiagnostics("Steam UI restarted after switching modes")).toBe(true);
  });
});
