import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, test, vi } from "vitest";
import FileUpload from "./FileUpload";
import { uploadFile } from "../../services/api";
import toast from "react-hot-toast";

vi.mock("../../services/api", () => ({ uploadFile: vi.fn() }));
vi.mock("react-hot-toast", () => ({
  default: { error: vi.fn(), success: vi.fn() },
}));

// react-dropzone is exercised for real rather than mocked: the accept-list and
// the disabled-while-uploading flag are part of what this component is *for*,
// and a mocked useDropzone would assert nothing about either.
const csv = (name = "sales.csv") =>
  new File(["region,total\nNorth,10\n"], name, { type: "text/csv" });

const okResult = (over = {}) => ({
  message: "Loaded sales.csv",
  session_id: "abcdef12-3456-7890-abcd-ef1234567890",
  rows: 42,
  columns: ["region", "total"],
  dtypes: { region: "VARCHAR", total: "BIGINT" },
  anomalies: [],
  ...over,
});

const fileInput = () => document.querySelector('input[type="file"]');
// react-dropzone's `disabled` option does *not* put a `disabled` attribute on
// the input — it drops the handlers and the component reflects the state in a
// class. Asserting `toBeDisabled()` here passes whatever the component does,
// which is how the first draft of these tests managed to be vacuous.
const dropzone = () => document.querySelector(".dropzone");

// onDrop is async, so its setState calls land in a microtask after the event.
// Wrapping the interaction is the same pattern ChatWindow.test.jsx uses for the
// SSE callbacks, and it is what keeps React's act() warnings out of the output.
const drop = (file) =>
  act(async () => {
    await userEvent.upload(fileInput(), file);
  });

beforeEach(() => {
  vi.clearAllMocks();
});

// ── Idle state ───────────────────────────────────────────────────────────────

test("invites a drop and names the formats it accepts", () => {
  render(<FileUpload onUploadSuccess={vi.fn()} />);
  expect(screen.getByText(/drag & drop your data file here/i)).toBeInTheDocument();
  expect(screen.getByText(/CSV, Excel/i)).toBeInTheDocument();
});

test("shows no result or progress before anything is uploaded", () => {
  render(<FileUpload onUploadSuccess={vi.fn()} />);
  expect(screen.queryByText(/detected schema/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/uploading/i)).not.toBeInTheDocument();
});

// ── The success path ─────────────────────────────────────────────────────────

test("uploads the dropped file and reports the schema back", async () => {
  uploadFile.mockResolvedValue({ data: okResult() });
  const onUploadSuccess = vi.fn();
  render(<FileUpload onUploadSuccess={onUploadSuccess} />);

  await drop(csv());

  await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
  expect(uploadFile.mock.calls[0][0].name).toBe("sales.csv");

  expect(await screen.findByText("Loaded sales.csv")).toBeInTheDocument();
  expect(screen.getByText("region")).toBeInTheDocument();
  expect(screen.getByText("VARCHAR")).toBeInTheDocument();
  expect(screen.getByText("total")).toBeInTheDocument();
  expect(screen.getByText("BIGINT")).toBeInTheDocument();
});

test("hands the parsed result to the parent so the chat can open on it", async () => {
  const data = okResult();
  uploadFile.mockResolvedValue({ data });
  const onUploadSuccess = vi.fn();
  render(<FileUpload onUploadSuccess={onUploadSuccess} />);

  await drop(csv());

  await waitFor(() => expect(onUploadSuccess).toHaveBeenCalledWith(data));
});

test("confirms the row count, which is the number the user came for", async () => {
  uploadFile.mockResolvedValue({ data: okResult({ rows: 1234 }) });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  await waitFor(() =>
    expect(toast.success).toHaveBeenCalledWith("Loaded 1234 rows successfully!")
  );
});

test("truncates the session id rather than printing all 36 characters", async () => {
  uploadFile.mockResolvedValue({ data: okResult() });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  expect(await screen.findByText(/Session ID: abcdef12\.\.\./)).toBeInTheDocument();
  expect(
    screen.queryByText(/abcdef12-3456-7890-abcd-ef1234567890/)
  ).not.toBeInTheDocument();
});

// ── The failure path ─────────────────────────────────────────────────────────

test("surfaces the server's reason when the upload is rejected", async () => {
  uploadFile.mockRejectedValue({
    response: { data: { detail: "File exceeds the 50 MB limit" } },
  });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  await waitFor(() =>
    expect(toast.error).toHaveBeenCalledWith("File exceeds the 50 MB limit")
  );
});

test("falls back to a generic message when the error carries no detail", async () => {
  // A network failure has no response at all — the optional chaining is the
  // only thing standing between that and a TypeError inside the catch block.
  uploadFile.mockRejectedValue(new Error("Network Error"));
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  await waitFor(() => expect(toast.error).toHaveBeenCalledWith("Upload failed"));
});

test("a failed upload notifies nobody downstream and renders no schema", async () => {
  uploadFile.mockRejectedValue({ response: { data: { detail: "nope" } } });
  const onUploadSuccess = vi.fn();
  render(<FileUpload onUploadSuccess={onUploadSuccess} />);

  await drop(csv());

  await waitFor(() => expect(toast.error).toHaveBeenCalled());
  expect(onUploadSuccess).not.toHaveBeenCalled();
  expect(screen.queryByText(/detected schema/i)).not.toBeInTheDocument();
});

test("the dropzone recovers after a failure so the user can retry", async () => {
  uploadFile.mockRejectedValueOnce({ response: { data: { detail: "nope" } } });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());
  await waitFor(() => expect(toast.error).toHaveBeenCalled());

  // The `finally` block clears `uploading`; without it the dropzone stays
  // locked and the only way out of a transient error is a page reload.
  await waitFor(() => expect(dropzone()).not.toHaveClass("disabled"));

  uploadFile.mockResolvedValue({ data: okResult() });
  await drop(csv("retry.csv"));
  expect(await screen.findByText("Loaded sales.csv")).toBeInTheDocument();
  expect(uploadFile).toHaveBeenCalledTimes(2);
});

// ── Progress ─────────────────────────────────────────────────────────────────

test("reports upload progress and locks the dropzone while in flight", async () => {
  let reportProgress;
  let finish;
  uploadFile.mockImplementation((_file, onProgress) => {
    reportProgress = onProgress;
    return new Promise((resolve) => {
      finish = () => resolve({ data: okResult() });
    });
  });

  render(<FileUpload onUploadSuccess={vi.fn()} />);
  await drop(csv());

  await waitFor(() => expect(screen.getByText(/0% uploading/)).toBeInTheDocument());
  expect(dropzone()).toHaveClass("disabled");

  // The assertion that matters: a second file dropped mid-flight is ignored
  // rather than racing the first and overwriting its session.
  await drop(csv("second.csv"));
  expect(uploadFile).toHaveBeenCalledTimes(1);

  // These drive React state from outside an event handler, so they need act()
  // or the update lands after the assertion and React warns.
  await act(async () => reportProgress(60));
  expect(screen.getByText(/60% uploading/)).toBeInTheDocument();

  await act(async () => {
    finish();
  });
  await waitFor(() => expect(screen.queryByText(/uploading/)).not.toBeInTheDocument());
});

// ── Anomalies ────────────────────────────────────────────────────────────────

test("renders detected anomalies with their type, message and severity", async () => {
  uploadFile.mockResolvedValue({
    data: okResult({
      anomalies: [
        { type: "missing_values", message: "region has 3 nulls", severity: "high" },
        { type: "outlier", message: "total has 1 outlier", severity: "medium" },
      ],
    }),
  });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  expect(await screen.findByText(/anomalies detected/i)).toBeInTheDocument();
  expect(screen.getByText("missing_values")).toBeInTheDocument();
  expect(screen.getByText("region has 3 nulls")).toBeInTheDocument();
  expect(screen.getByText("high")).toBeInTheDocument();
  expect(screen.getByText("outlier")).toBeInTheDocument();
  expect(screen.getByText("medium")).toBeInTheDocument();
});

test("severity drives the colour, so high and low do not look alike", async () => {
  uploadFile.mockResolvedValue({
    data: okResult({
      anomalies: [
        { type: "a_high", message: "m", severity: "high" },
        { type: "a_medium", message: "m", severity: "medium" },
        { type: "a_low", message: "m", severity: "low" },
      ],
    }),
  });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  await screen.findByText("a_high");
  const colourOf = (type) =>
    screen.getByText(type).closest(".anomaly-item").style.borderLeftColor;

  // The exact custom properties matter less than all three being distinct —
  // a switch that fell through would make them identical.
  expect(colourOf("a_high")).toContain("--danger");
  expect(colourOf("a_medium")).toContain("--warning");
  expect(colourOf("a_low")).toContain("--text-muted");
  expect(new Set([colourOf("a_high"), colourOf("a_medium"), colourOf("a_low")]).size).toBe(3);
});

test("an unrecognised severity falls back rather than rendering no colour", async () => {
  uploadFile.mockResolvedValue({
    data: okResult({
      anomalies: [{ type: "odd", message: "m", severity: "catastrophic" }],
    }),
  });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  await screen.findByText("odd");
  expect(
    screen.getByText("odd").closest(".anomaly-item").style.borderLeftColor
  ).toContain("--text-muted");
});

test("a clean file shows no anomalies section at all", async () => {
  uploadFile.mockResolvedValue({ data: okResult({ anomalies: [] }) });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  await screen.findByText(/detected schema/i);
  expect(screen.queryByText(/anomalies detected/i)).not.toBeInTheDocument();
});

test("a response without an anomalies key does not crash the render", async () => {
  // The backend omits the key entirely on some paths; `result.anomalies &&`
  // is what keeps this from throwing on .length.
  const data = okResult();
  delete data.anomalies;
  uploadFile.mockResolvedValue({ data });
  render(<FileUpload onUploadSuccess={vi.fn()} />);

  await drop(csv());

  expect(await screen.findByText(/detected schema/i)).toBeInTheDocument();
  expect(screen.queryByText(/anomalies detected/i)).not.toBeInTheDocument();
});
