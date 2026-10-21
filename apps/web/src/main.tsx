import "@fontsource-variable/instrument-sans";
import "@fontsource/instrument-serif/400.css";
import "@fontsource/instrument-serif/400-italic.css";
import "@fontsource-variable/jetbrains-mono";
import "@fontsource/stix-two-text/400.css";
import "@fontsource/stix-two-text/400-italic.css";
import "./shared/theme/global.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app/App";
import { loadCapabilities } from "./shared/domain/disciplines";

const root = document.getElementById("root");
if (!root) throw new Error("no #root element");
const appRoot = createRoot(root);
loadCapabilities()
  .then(() =>
    appRoot.render(
      <StrictMode>
        <App />
      </StrictMode>,
    ),
  )
  .catch((error: unknown) =>
    appRoot.render(
      <main role="alert">
        <h1>Capabilities unavailable</h1>
        <p>
          {error instanceof Error
            ? error.message
            : "The server did not provide its installed capabilities."}
        </p>
        <button type="button" onClick={() => window.location.reload()}>
          Retry
        </button>
      </main>,
    ),
  );
