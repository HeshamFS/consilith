import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { agentApi } from "@/features/agent/api";
import { useAuth } from "@/features/auth";
import { ApiError } from "@/shared/http/client";
import { Mark } from "@/shared/ui";
import { AgentChat } from "./AgentChat";
import { AgentDrawer } from "./AgentDrawer";
import { Arrival } from "./Arrival";
import { useAgentSession } from "./agentSession";
import { BringAgentSheet } from "./BringAgentSheet";
import { CursorLayer } from "./CursorLayer";
import { DecideView } from "./DecideView";
import { Deck, type View } from "./Deck";
import { Docks, waitingOnMe } from "./Docks";
import { agentOf } from "./describe";
import { LedgerSheet, LogSheet } from "./HistorySheets";
import { InviteSheet } from "./InviteSheet";
import { Overview } from "./Overview";
import { PeerPopup } from "./PeerChat";
import { PeerStrip } from "./PeerStrip";
import { PluginSheet } from "./PluginSheet";
import { peerThreadsOf } from "./peer";
import s from "./room.module.css";
import { StartSheet } from "./StartSheet";
import { StudioView } from "./StudioView";
import { useRoom } from "./store";
import { TopBar } from "./TopBar";
import type { CaseId } from "./types";
import { useSurfaceActions } from "./useSurfaceActions";
import { WiringView } from "./WiringView";

type SheetName =
  | "log"
  | "ledger"
  | "invite"
  | "agent"
  | "bring"
  | "start"
  | "chat"
  | "plugins"
  | null;

export function RoomPage() {
  const { id = "" } = useParams();
  const { person } = useAuth();
  const { conn, state } = useRoom(id, person?.id ?? "");
  const [view, setView] = useState<View>({ kind: "builtin", id: "overview" });
  // The agent thread to open first in Agents at work (the chat strip links there).
  const [peerThread, setPeerThread] = useState<string | undefined>(undefined);
  const [viewAs, setViewAs] = useState<string>(person?.id ?? "");
  const [sheet, setSheet] = useState<SheetName>(null);
  const [startKind, setStartKind] = useState<string>("words");
  const [drawerFor, setDrawerFor] = useState<string | null>(null);
  const close = useCallback(() => setSheet(null), []);
  const doc = state.doc;
  // The transcript lives here, above the sheet: it keeps its messages when the sheet closes and
  // loads the server's history once, when the room opens.
  const chat = useAgentSession(id, person?.id ?? "");
  // When my agent comes to rest after a turn this tab did not run, show what it did.
  const myStatus = doc?.room.agents?.[`${person?.id ?? ""}.agent`]?.status;
  const busy = useRef(false);
  const ranHere = useRef(false);
  useEffect(() => {
    if (chat.running) ranHere.current = true;
  }, [chat.running]);
  useEffect(() => {
    if (myStatus && myStatus !== "idle" && myStatus !== "paused") {
      busy.current = true;
      return;
    }
    if (!busy.current) return;
    busy.current = false;
    if (!ranHere.current && !chat.running) void chat.reload();
    ranHere.current = chat.running;
  }, [myStatus, chat.running, chat.reload]);
  // The person's own actions and artifact downloads, for the surfaces their agent shows in chat.
  const mine = person?.id ?? "";
  const { act: actInChat, loadArtifact: loadInChat } = useSurfaceActions({
    conn,
    roomId: id,
    me: mine,
    viewer: mine,
  });
  const fixChat = useCallback(
    (action: string) => {
      if (action === "retry") void chat.refresh();
      else if (action === "continue") void chat.send("Continue where you stopped.");
      else {
        setDrawerFor(null);
        setSheet("agent");
      }
    },
    [chat],
  );

  // A room opens on its active studio, the work itself, not on the overview. Once only: after
  // that the person's own choice of view stands.
  const activeCase = doc?.room.activeCase;
  const [opened, setOpened] = useState(false);
  useEffect(() => {
    if (opened || !activeCase) return;
    setOpened(true);
    if (doc?.room.capabilities?.some((c) => c.id === activeCase))
      setView({ kind: "capability", id: activeCase });
  }, [opened, activeCase, doc]);

  useEffect(() => {
    if (doc) document.title = `${doc.room.title} · Consilith`;
    return () => {
      document.title = "Consilith";
    };
  }, [doc]);

  if (!person) return null;

  if (!doc) {
    return (
      <div className={s.room}>
        <div className={s.loading} role="status">
          {state.connection === "offline" ? (
            <>
              <Mark size={56} label="Consilith" />
              <h1 className={s.loadTitle}>This room did not open.</h1>
              <p className={s.loadText}>{state.error}</p>
              <Link to="/">Back to your rooms</Link>
            </>
          ) : (
            <>
              <Mark size={56} loading label="Opening the room" />
              <p className={s.loadText}>Opening the room…</p>
            </>
          )}
        </div>
      </div>
    );
  }

  const me = doc.room.people[person.id];
  const myAgent = agentOf(doc, person.id);
  const capabilities = doc.room.capabilities ?? [];
  const caseId: CaseId = view.kind === "capability" ? view.id : doc.room.activeCase;
  const open = Math.max(0, 4 - Object.keys(doc.room.people).length);
  const drawerPerson = drawerFor ? doc.room.people[drawerFor] : me;
  const drawerAgent = drawerPerson ? agentOf(doc, drawerPerson.id) : null;

  return (
    <div className={s.room} data-docked={sheet === "chat" ? "chat" : undefined}>
      <TopBar
        doc={doc}
        me={person.id}
        viewAs={viewAs}
        onViewAs={setViewAs}
        connection={state.connection}
        onInvite={() => setSheet("invite")}
      />
      {state.connection === "reconnecting" && state.error ? (
        <div className={s.banner} role="status">
          Reconnecting to the room… {state.error}
        </div>
      ) : null}
      <Arrival doc={doc} me={person.id} />
      <Deck
        capabilities={capabilities}
        onPlugins={() => setSheet("plugins")}
        view={view}
        onView={setView}
        onLog={() => setSheet("log")}
        onLedger={() => setSheet("ledger")}
        onAgent={() => {
          setDrawerFor(null);
          setSheet("agent");
        }}
        onChat={() => setSheet("chat")}
        me={{ name: me?.name || person.name, color: me?.color ?? person.color }}
        waiting={waitingOnMe(doc, person.id)}
      />
      <main className={s.canvas} aria-label="Room canvas">
        <CursorLayer
          doc={doc}
          me={person.id}
          caseId={caseId}
          active={view.kind === "capability"}
          presence={state.presence}
          onMove={conn.presence}
        />
        {view.kind === "builtin" && view.id === "overview" ? (
          <Overview
            doc={doc}
            me={person.id}
            conn={conn}
            caseId={caseId}
            onStart={(k) => {
              setStartKind(k);
              setSheet("start");
            }}
          />
        ) : view.kind === "builtin" && view.id === "decide" ? (
          <DecideView doc={doc} me={person.id} conn={conn} />
        ) : view.kind === "builtin" && view.id === "agents" ? (
          <WiringView
            doc={doc}
            wiring={state.wiring}
            tools={state.tools}
            me={person.id}
            thread={peerThread}
            onOpenLane={(c) => setView({ kind: "capability", id: c })}
            onAgent={(pid) => {
              setDrawerFor(pid);
              setSheet("agent");
            }}
          />
        ) : (
          <StudioView
            doc={doc}
            me={person.id}
            viewAs={viewAs}
            caseId={caseId}
            conn={conn}
            surfaces={state.surfaces}
            tools={state.tools}
          />
        )}
      </main>
      <Docks
        doc={doc}
        me={person.id}
        log={state.log}
        onWaiting={() => setView({ kind: "builtin", id: "decide" })}
        onLog={() => setSheet("log")}
      />
      {sheet !== "chat" ? (
        <PeerPopup doc={doc} me={person.id} onOpenChat={() => setSheet("chat")} />
      ) : null}

      {sheet === "plugins" ? (
        <PluginSheet
          conn={conn}
          active={doc.room.activePlugins ?? (doc.room.plugins ?? []).map((p) => p.id)}
          pinned={doc.room.plugins ?? []}
          onClose={close}
        />
      ) : null}
      {sheet === "chat" ? (
        <AgentChat
          session={chat}
          person={{ id: person.id, name: person.name, color: me?.color ?? person.color }}
          agentName={
            myAgent?.brought ? (myAgent.card?.name ?? "your A2A agent") : `${person.id}.agent`
          }
          peerStrip={
            <PeerStrip
              threads={peerThreadsOf(doc)}
              me={person.id}
              doc={doc}
              onOpen={(id) => {
                setPeerThread(id);
                setView({ kind: "builtin", id: "agents" });
                close();
              }}
            />
          }
          onFix={fixChat}
          surfaces={{
            doc,
            onAction: actInChat,
            loadArtifact: loadInChat,
            onOpenStudio: (c) => {
              setView({ kind: "capability", id: c });
              close();
            },
          }}
          onClose={close}
        />
      ) : null}
      {sheet === "log" ? (
        <LogSheet doc={doc} log={state.log} wiring={state.wiring} onClose={close} />
      ) : null}
      {sheet === "ledger" ? <LedgerSheet doc={doc} onClose={close} /> : null}
      {sheet === "invite" ? <InviteSheet conn={conn} open={open} onClose={close} /> : null}
      {sheet === "start" ? (
        <StartSheet
          conn={conn}
          doc={doc}
          me={person.id}
          initial={startKind}
          onRouted={(c) => {
            setView({ kind: "capability", id: c });
            close();
          }}
          onClose={close}
        />
      ) : null}
      {sheet === "agent" && drawerPerson && drawerAgent ? (
        <AgentDrawer
          conn={conn}
          person={drawerPerson}
          agent={drawerAgent}
          plugins={doc.room.plugins ?? []}
          editable={drawerPerson.id === person.id}
          onBring={() => setSheet("bring")}
          onClose={close}
        />
      ) : null}
      {sheet === "bring" && myAgent ? (
        <BringAgentSheet
          person={{ id: person.id, name: person.name, color: person.color }}
          current={{
            name: myAgent.brought ? (myAgent.card?.name ?? "your A2A agent") : myAgent.id,
            kind: myAgent.brought ? "brought" : "consilith",
            note: myAgent.brought
              ? `Brought over A2A · ${myAgent.cardUrl ?? ""}`
              : "Consilith agent · your skills, your model",
          }}
          onFetch={async (url) => {
            try {
              const card = await conn.bring(myAgent.id, url);
              return card;
            } catch (e) {
              throw new Error(e instanceof ApiError ? e.message : "The room refused that card.");
            }
          }}
          onReplace={async (url) => {
            // The room already took the card over in onFetch; this makes it your default too.
            await agentApi.save({
              kind: "brought",
              provider: "scripted",
              model: null,
              autonomy: myAgent.config.autonomy,
              cardUrl: url,
            });
          }}
          onBack={async () => {
            const a = await agentApi.get();
            await agentApi.save({ ...a, kind: "consilith", cardUrl: null });
          }}
          onClose={close}
        />
      ) : null}
    </div>
  );
}
