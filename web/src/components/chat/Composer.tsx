"use client";

import { useState, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Send, Square, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useChatStore } from "@/lib/stores/chat-store";
import { useAuthStore } from "@/lib/stores/auth-store";
import { useRepositories } from "@/lib/providers/repository-context";
import { ChatStreamError } from "@/lib/api/real-chat-repository";
import { generateRequestId } from "@/lib/utils/id";
import { PlanningPicker } from "./PlanningPicker";
import type { ChatEvent, ChatState, PlanningDocument, RunStream } from "@/lib/domain/types";

const MAX_RECONNECT_ATTEMPTS = 5;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export function Composer() {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [selectedDocs, setSelectedDocs] = useState<PlanningDocument[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const toggleDoc = useCallback((doc: PlanningDocument) => {
    setSelectedDocs((prev) =>
      prev.some((d) => d.id === doc.id)
        ? prev.filter((d) => d.id !== doc.id)
        : [...prev, doc],
    );
  }, []);

  const {
    activeSessionId,
    chatState,
    setChatState,
    setShowConnectionBanner,
    addUserMessage,
    addSession,
    addDraft,
    addEmployeeDraft,
    startRun,
    updateRunCursor,
    clearRunState,
    getRunState,
  } = useChatStore();

  const { chat: chatRepo, auth: authRepo } = useRepositories();
  const { logout } = useAuthStore();
  const router = useRouter();

  const isStreaming =
    chatState === "streaming" || chatState === "connecting" || chatState === "reconnecting";
  const canSend =
    (input.trim().length > 0 || selectedDocs.length > 0) && !sending && !isStreaming;

  // Transition chat state and keep the connection banner in sync (reconnecting/error).
  const setChatStatus = useCallback(
    (state: ChatState) => {
      setChatState(state);
      setShowConnectionBanner(state === "reconnecting" || state === "error");
    },
    [setChatState, setShowConnectionBanner],
  );

  // A 401/AUTH_REQUIRED from the BFF means the end-user's session expired. Terminate
  // the run and route to re-login so the user can recover, instead of retrying a
  // doomed stream as a network drop.
  const handleAuthRequired = useCallback(async () => {
    setChatState("stopped");
    setShowConnectionBanner(false);
    try {
      await logout(authRepo);
    } catch {
      // Even if the logout call fails, still route to login.
    }
    router.replace("/login");
  }, [logout, authRepo, router, setChatState, setShowConnectionBanner]);

  // Handle streaming events
  const handleChatEvent = useCallback(
    (event: ChatEvent) => {
      const state = useChatStore.getState();

      switch (event.type) {
        case "message-start":
          setChatStatus("streaming");
          break;

        case "token":
          // The first token also restores "streaming" after a reconnect (reconnects do
          // not re-deliver run.started, so there is no message-start event).
          setChatStatus("streaming");
          if (event.messageId) {
            const msgs = state.messages;
            const lastIdx = msgs.length - 1;
            if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
              const updated = [...msgs];
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: updated[lastIdx].content + event.text,
                status: "streaming",
              };
              useChatStore.setState({ messages: updated });
            } else {
              // First token — create new assistant message
              const newMsg = {
                id: event.messageId,
                sessionId: activeSessionId ?? "",
                role: "assistant" as const,
                content: event.text,
                status: "streaming" as const,
                createdAt: new Date().toISOString(),
              };
              useChatStore.setState({ messages: [...msgs, newMsg] });
            }
          }
          break;

        case "draft":
          addDraft(event.draft);
          // A draft ends the assistant stream (no run.completed follows): mark the
          // explanation bubble completed so it doesn't stay in "streaming".
          setChatStatus("completed");
          useChatStore.setState((s) => ({
            messages: s.messages.map((m) =>
              m.id === event.draft.messageId ? { ...m, status: "completed" as const } : m,
            ),
          }));
          break;

        case "employee-action":
          addEmployeeDraft(event.approval);
          // An employee action ends the assistant stream like a draft does.
          setChatStatus("completed");
          useChatStore.setState((s) => ({
            messages: s.messages.map((m) =>
              m.id === event.approval.messageId ? { ...m, status: "completed" as const } : m,
            ),
          }));
          break;

        case "done":
          setChatStatus("completed");
          // Mark assistant message as completed
          useChatStore.setState((s) => ({
            messages: s.messages.map((m) =>
              m.id === event.messageId ? { ...m, status: "completed" as const } : m,
            ),
          }));
          break;

        case "error":
          // Surface the provider-failure message (retriable terminal error) on the
          // streaming assistant message so the user sees why generation stopped.
          setChatStatus("error");
          useChatStore.setState((s) => {
            const msgs = [...s.messages];
            const lastIdx = msgs.length - 1;
            if (lastIdx >= 0 && msgs[lastIdx].role === "assistant") {
              msgs[lastIdx] = { ...msgs[lastIdx], status: "error", error: event.message };
            }
            return { messages: msgs };
          });
          break;
      }
    },
    [activeSessionId, addDraft, addEmployeeDraft, setChatStatus],
  );

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text && selectedDocs.length === 0) return;

    // Compose the outgoing content: user text + each referenced planning doc as
    // prompt context (the chip shows the title only; the content is appended here).
    const refs = selectedDocs.map((d) => `【参考规划：${d.title}】\n${d.content}`);
    const content = [text, ...refs].filter(Boolean).join("\n\n");

    let sessionId = activeSessionId;

    // Create new session if needed
    if (!sessionId) {
      const session = await chatRepo.createSession();
      addSession(session);
      sessionId = session.id;
      useChatStore.setState({ activeSessionId: sessionId });
    }

    setInput("");
    setSelectedDocs([]);
    setSending(true);

    // Add user message locally
    addUserMessage(content);

    const idempotencyKey = generateRequestId();
    const controller = new AbortController();
    abortRef.current = controller;
    const signal = controller.signal;

    let terminalError = false;

    // Consume a run stream, persisting the cursor and accumulated text. Returns
    // false when the stream drops so the caller can resume the same run.
    const consume = async (stream: RunStream): Promise<boolean> => {
      try {
        for await (const event of stream.events) {
          if (signal.aborted) return false;
          if (event.type === "error") terminalError = true;
          const cursor = stream.lastEventId();
          if (cursor !== undefined) updateRunCursor(sessionId, cursor);
          handleChatEvent(event);
        }
        return true;
      } catch (error) {
        // AUTH_REQUIRED is not a network drop — surface it to the caller so it can
        // route to re-login instead of retrying a doomed stream.
        if (error instanceof ChatStreamError && error.isAuthRequired) throw error;
        // Genuine network/stream interruption — the caller decides whether to reconnect.
        return false;
      }
    };

    try {
      // Start streaming
      setChatStatus("connecting");
      const run = await chatRepo.createRun(sessionId, content, idempotencyKey, { signal });
      startRun(sessionId, { runId: run.runId, content, idempotencyKey });

      let ok = await consume(run);
      let attempts = 0;
      // On interruption, resume the SAME run with the same idempotency key and the
      // persisted Last-Event-ID — the server resumes live generation without a second
      // user message.
      while (!ok && !signal.aborted) {
        attempts += 1;
        if (attempts > MAX_RECONNECT_ATTEMPTS) break;
        setChatStatus("reconnecting");
        const runState = getRunState(sessionId);
        const resumed = chatRepo.subscribeRunEvents({
          sessionId,
          content,
          idempotencyKey,
          lastEventId: runState?.lastEventId,
          signal,
        });
        ok = await consume(resumed);
        if (!ok && attempts < MAX_RECONNECT_ATTEMPTS) await sleep(300 * attempts);
      }

      if (signal.aborted) {
        setChatStatus("stopped");
        return;
      }
      clearRunState(sessionId);
      setChatStatus(ok && !terminalError ? "completed" : "error");
    } catch (error) {
      if (signal.aborted) {
        setChatStatus("stopped");
        return;
      }
      if (error instanceof ChatStreamError && error.isAuthRequired) {
        await handleAuthRequired();
        return;
      }
      setChatStatus("error");
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  }, [
    input,
    selectedDocs,
    activeSessionId,
    chatRepo,
    addUserMessage,
    addSession,
    addDraft,
    startRun,
    updateRunCursor,
    clearRunState,
    getRunState,
    setChatStatus,
    handleChatEvent,
    handleAuthRequired,
  ]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setChatState("stopped");
    setShowConnectionBanner(false);
    setSending(false);
  }, [setChatState, setShowConnectionBanner]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (canSend) handleSend();
    }
  };

  return (
    <div className="border-t border-border bg-background px-4 py-3">
      {selectedDocs.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2 max-w-4xl mx-auto">
          {selectedDocs.map((doc) => (
            <Badge key={doc.id} variant="secondary" className="gap-1">
              <span className="truncate max-w-40">{doc.title}</span>
              <button
                type="button"
                aria-label={`移除 ${doc.title}`}
                onClick={() =>
                  setSelectedDocs((prev) => prev.filter((d) => d.id !== doc.id))
                }
                className="shrink-0 rounded p-0.5 text-muted-foreground hover:text-destructive"
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      <div className="flex items-end gap-2 max-w-4xl mx-auto">
        <PlanningPicker selected={selectedDocs} onToggle={toggleDoc} />
        <Textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="输入你的需求..."
          rows={1}
          className="min-h-[40px] max-h-[160px] resize-none"
          disabled={isStreaming}
        />
        {isStreaming ? (
          <Button
            variant="outline"
            size="icon"
            onClick={handleStop}
            aria-label="停止生成"
            className="shrink-0"
          >
            <Square className="h-4 w-4" />
          </Button>
        ) : (
          <Button
            variant="default"
            size="icon"
            onClick={handleSend}
            disabled={!canSend}
            aria-label="发送消息"
            className="shrink-0"
          >
            <Send className="h-4 w-4" />
          </Button>
        )}
      </div>
    </div>
  );
}
