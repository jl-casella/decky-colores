import {
  FC,
  ReactNode,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  DialogButton,
  Focusable,
  getFocusNavController,
  ModalRoot,
  PanelSectionRow,
  showModal,
  ToggleField,
} from "@decky/ui";

import {
  DiagnosticCaptureState,
  deleteDiagnosticsLogs,
  getDiagnosticsCapture,
  setDiagnosticsCapture,
} from "../api";
import { deviceDisplayName } from "../deviceName";
import { I18nProvider, useI18n } from "../i18n";
import { theme } from "../theme";
import { DeviceInfo } from "../types";
import { FocusRoot } from "./FocusRoot";

export function reportFocusTarget(
  root: Pick<HTMLElement, "querySelector">,
): HTMLElement | null {
  return root.querySelector<HTMLElement>('[data-report-primary-action="true"]');
}

const ReportBody: FC<{ device: DeviceInfo }> = ({ device }) => {
  const { t } = useI18n();
  const [capture, setCapture] = useState<DiagnosticCaptureState | null>(null);
  const [captureError, setCaptureError] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    getDiagnosticsCapture()
      .then((state) => { if (alive) { setCapture(state); setCaptureError(!!state.error); } })
      .catch(() => { if (alive) setCaptureError(true); });
    return () => { alive = false; };
  }, []);

  const toggleCapture = (enabled: boolean) => {
    setCaptureError(false);
    setDeleted(false);
    setDiagnosticsCapture(enabled)
      .then((state) => { setCapture(state); setCaptureError(!!state.error); })
      .catch(() => setCaptureError(true));
  };

  const deleteLogs = () => {
    if (deleting || !capture?.has_logs) return;
    setDeleting(true);
    setCaptureError(false);
    setDeleted(false);
    deleteDiagnosticsLogs()
      .then((state) => {
        setCapture(state);
        setCaptureError(!!state.error);
        setDeleted(!state.error);
      })
      .catch(() => {
        setCapture((current) => current ? { ...current, error: "delete_failed" } : current);
        setCaptureError(true);
      })
      .finally(() => setDeleting(false));
  };

  const wrap = (children: ReactNode) => (
    <div
      ref={rootRef}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: theme.space.lg,
        padding: theme.space.sm,
        maxWidth: 720,
        width: "100%",
        margin: "0 auto",
      }}
    >
      <div style={{ fontSize: theme.font.value, color: theme.color.textPrimary }}>
        {deviceDisplayName(device, t)}
      </div>
      {children}
    </div>
  );
  useLayoutEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const target = reportFocusTarget(root);
    if (!target) return;
    try {
      const controller = getFocusNavController();
      if (typeof controller?.FocusElement === "function") {
        controller.FocusElement(target);
        return;
      }
    } catch {}
    target.focus({ preventScroll: true });
  }, []);

  return wrap(
    <>
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: theme.font.value, fontWeight: 700, color: theme.color.textPrimary }}>
          {t("report.title")}
        </div>
      </div>
      <div style={{ fontSize: theme.font.body, color: theme.color.textMuted }}>
        {t("report.intro")}
      </div>
      <PanelSectionRow>
        <ToggleField
          label={t("report.capture.toggle")}
          description={t("report.capture.duration")}
          checked={capture?.enabled ?? false}
          disabled={!capture}
          onChange={toggleCapture}
          bottomSeparator="none"
        />
      </PanelSectionRow>
      <div style={{ fontSize: theme.font.caption, color: captureError ? theme.color.danger : theme.color.textMuted }}>
        {captureError
          ? capture?.error === "delete_failed"
            ? t("report.capture.delete_error")
            : t("report.capture.error")
          : t("report.capture.location", { path: capture?.directory ?? "~/Documents/colores-logs" })}
      </div>
      <div style={{ fontSize: theme.font.caption, color: theme.color.textMuted }}>
        {t("report.capture.sources")}
      </div>

      <div style={{ ...theme.card, padding: theme.space.md, fontSize: theme.font.caption, color: theme.color.textMuted, lineHeight: 1.5 }}>
        <div style={theme.sectionLabel}>{t("report.privacy.title")}</div>
        <div>{t("report.privacy.local")}</div>
        <div>{t("report.privacy.redaction")}</div>
        <div>{t("report.privacy.manual")}</div>
      </div>
      <Focusable>
        <DialogButton
          data-report-primary-action="true"
          disabled={!capture?.has_logs || deleting}
          onClick={deleteLogs}
        >
          {deleting ? t("report.capture.deleting") : t("report.capture.delete")}
        </DialogButton>
      </Focusable>
      {captureError || deleted ? (
        <div style={{ fontSize: theme.font.caption, color: captureError ? theme.color.danger : theme.color.textMuted }}>
          {captureError ? t("report.capture.delete_error") : t("report.capture.deleted")}
        </div>
      ) : null}
    </>,
  );
};

const ReportModal: FC<{ device: DeviceInfo; closeModal?: () => void }> = ({
  device,
  closeModal,
}) => (
  <ModalRoot closeModal={closeModal} bAllowFullSize>
    <FocusRoot>
      <I18nProvider>
        <ReportBody device={device} />
      </I18nProvider>
    </FocusRoot>
  </ModalRoot>
);

export function openReportModal(device: DeviceInfo): void {
  showModal(<ReportModal device={device} />, window);
}
