import type { RouteObject } from "react-router";
import { FinishPage, RequireAuth, SignInPage, VerifyPage } from "@/features/auth";
import { AboutYouPage, YourAgentPage } from "@/features/onboarding";
import { RoomPage } from "@/features/room/RoomPage";
import { HomePage, JoinPage, NewRoomPage } from "@/features/rooms";
import { RoomSettingsPage } from "@/features/rooms/RoomSettingsPage";
import { NotFound } from "./NotFound";

export const routes: RouteObject[] = [
  { path: "/signin", element: <SignInPage /> },
  { path: "/auth/verify", element: <VerifyPage /> },
  { path: "/auth/finish", element: <FinishPage /> },
  {
    path: "/welcome/about",
    element: (
      <RequireAuth profile={false}>
        <AboutYouPage />
      </RequireAuth>
    ),
  },
  {
    path: "/welcome/agent",
    element: (
      <RequireAuth>
        <YourAgentPage />
      </RequireAuth>
    ),
  },
  {
    path: "/settings/agent",
    element: (
      <RequireAuth>
        <YourAgentPage settings />
      </RequireAuth>
    ),
  },
  {
    path: "/rooms/new",
    element: (
      <RequireAuth>
        <NewRoomPage />
      </RequireAuth>
    ),
  },
  {
    path: "/rooms/:id/settings",
    element: (
      <RequireAuth>
        <RoomSettingsPage />
      </RequireAuth>
    ),
  },
  {
    path: "/rooms/:id",
    element: (
      <RequireAuth>
        <RoomPage />
      </RequireAuth>
    ),
  },
  {
    path: "/invite/:token",
    element: (
      <RequireAuth>
        <JoinPage />
      </RequireAuth>
    ),
  },
  {
    path: "/join/:token",
    element: (
      <RequireAuth>
        <JoinPage />
      </RequireAuth>
    ),
  },
  {
    path: "/",
    element: (
      <RequireAuth>
        <HomePage />
      </RequireAuth>
    ),
  },
  { path: "*", element: <NotFound /> },
];
