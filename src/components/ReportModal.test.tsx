import { createElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

type FocusableProps = {
  children?: ReactNode;
  onActivate?: () => void;
  onClick?: () => void;
  role?: string;
  preferredFocus?: boolean;
  "aria-label"?: string;
  "aria-checked"?: boolean;
};

const mocks = vi.hoisted(() => ({
  modal: null as ReactNode | null,
  focusables: [] as FocusableProps[],
}));

vi.mock("@decky/ui", async () => {
  const React = await import("react");
  return {
    DialogButton: ({ children, ...props }: { children?: ReactNode }) =>
      React.createElement("button", props, children),
    ToggleField: (props: { label?: string; checked?: boolean; description?: string }) =>
      React.createElement("div", props, props.label),
    PanelSectionRow: ({ children }: { children?: ReactNode }) =>
      React.createElement("div", null, children),
    Focusable: ({ children, onActivate, onClick, ...props }: FocusableProps) => {
      mocks.focusables.push({ children, onActivate, onClick, ...props });
      return React.createElement("div", props, children);
    },
    ModalRoot: ({ children }: { children?: ReactNode }) => children,
    showModal: (node: ReactNode) => { mocks.modal = node; },
  };
});

vi.mock("../api", () => ({
  getDiagnosticsCapture: vi.fn(() => new Promise(() => {})),
  setDiagnosticsCapture: vi.fn(),
  deleteDiagnosticsLogs: vi.fn(),
}));

vi.mock("../i18n", () => ({
  I18nProvider: ({ children }: { children?: ReactNode }) => children,
  useI18n: () => ({ t: (key: string) => key }),
}));

vi.mock("./FocusRoot", () => ({
  FocusRoot: ({ children }: { children?: ReactNode }) => children,
}));

import {
  openReportModal,
  reportFocusTarget,
} from "./ReportModal";

const device = {
  name: "MSI Claw",
  product: "Claw A1M",
  board: "MS-1T41",
};

describe("ReportModal local diagnostics", () => {
  afterEach(() => {
    mocks.modal = null;
    mocks.focusables.length = 0;
    vi.unstubAllGlobals();
  });

  it("shows local capture guidance without categories or issue links", () => {
    vi.stubGlobal("window", {});
    openReportModal(device);
    const html = renderToStaticMarkup(createElement("div", null, mocks.modal));

    expect(html).toContain("MSI Claw");
    expect(html).toContain("report.capture.toggle");
    expect(html).toContain("report.privacy.local");
    expect(html).toContain("report.capture.delete");
    expect(html).toContain("report.capture.sources");
    expect(html).not.toContain("report.capture.save");
    expect(html).not.toContain("<input");
    expect(html).not.toContain("report.section.what");
    expect(html).not.toContain("report.cat.");
    expect(html).not.toContain("report.issues.link");
  });

  it("hands focus to the primary action in the form", () => {
    const action = {} as HTMLElement;
    const root = { querySelector: vi.fn(() => action) };

    expect(reportFocusTarget(root)).toBe(action);
    expect(root.querySelector).toHaveBeenCalledWith('[data-report-primary-action="true"]');
  });

  it("uses the same primary action as the modal focus target", () => {
    const action = {} as HTMLElement;
    const root = { querySelector: vi.fn(() => action) };

    expect(reportFocusTarget(root)).toBe(action);
    expect(root.querySelector).toHaveBeenCalledWith(
      '[data-report-primary-action="true"]',
    );
  });
});
