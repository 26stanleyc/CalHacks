import { StrictMode } from "react";
import { flushSync } from "react-dom";
import { createRoot } from "react-dom/client";
import { PhishingLog } from "./PhishingLog";

// Render synchronously so the page has content on its first frame, which is the frame the
// page transition animates in.
const root = createRoot(document.getElementById("root")!);
flushSync(() =>
  root.render(
    <StrictMode>
      <PhishingLog />
    </StrictMode>,
  ),
);
