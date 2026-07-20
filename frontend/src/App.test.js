import { render, screen } from "@testing-library/react";
import ErrorBoundary from "./components/ErrorBoundary";

// Note: App/Login pull in react-router-dom v7 (ESM), which CRA's frozen Jest
// resolver cannot load. We smoke-test the router-free ErrorBoundary instead,
// which still exercises the React render pipeline and our own component code.

function Boom() {
  throw new Error("boom");
}

test("renders children normally", () => {
  render(
    <ErrorBoundary>
      <div>hello world</div>
    </ErrorBoundary>
  );
  expect(screen.getByText("hello world")).toBeInTheDocument();
});

test("shows a recovery UI when a child throws", () => {
  // Silence the expected React error log for this test.
  const spy = jest.spyOn(console, "error").mockImplementation(() => {});
  render(
    <ErrorBoundary>
      <Boom />
    </ErrorBoundary>
  );
  expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Reload app/i })).toBeInTheDocument();
  spy.mockRestore();
});
