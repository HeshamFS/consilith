import { createBrowserRouter, RouterProvider } from "react-router";
import { AuthProvider } from "@/features/auth";
import { usingMock } from "@/shared/http/client";
import { ThemeProvider } from "@/shared/theme/ThemeProvider";
import tm from "@/shared/theme/theme.module.css";
import { RouteError } from "./RouteError";
import { routes } from "./routes";

const router = createBrowserRouter(routes.map((r) => ({ ...r, errorElement: <RouteError /> })));

export function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <RouterProvider router={router} />
        {usingMock ? <div className={tm.mockBadge}>mock API · dev only</div> : null}
      </AuthProvider>
    </ThemeProvider>
  );
}
