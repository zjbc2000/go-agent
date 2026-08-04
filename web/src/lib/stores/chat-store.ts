// ============================================================
// Chat store — manages chat UI state with Zustand
// ============================================================

import { create } from "zustand";
import type { ChatState, Message, PlanDraft, Session } from "@/lib/domain/types";
import type { ChatRepository } from "@/lib/domain/repositories";
import { generateRequestId } from "@/lib/utils/id";

interface ChatStore {
  // Sessions
  sessions: Session[];
  activeSessionId: string | null;
  loadSessions: (repo: ChatRepository) => Promise<void>;
  setActiveSession: (id: string) => void;
  addSession: (session: Session) => void;

  // Messages
  messages: Message[];
  loadMessages: (repo: ChatRepository, sessionId: string) => Promise<void>;
  addUserMessage: (content: string) => Message;

  // Streaming state
  chatState: ChatState;
  setChatState: (state: ChatState) => void;
  currentAssistantMessageId: string | null;

  // Drafts (pending plan cards in chat)
  drafts: PlanDraft[];
  addDraft: (draft: PlanDraft) => void;
  updateDraft: (id: string, updates: Partial<PlanDraft>) => void;
  removeDraft: (id: string) => void;
  getSessionDrafts: (sessionId: string) => PlanDraft[];

  // Scroll
  userScrolledUp: boolean;
  setUserScrolledUp: (v: boolean) => void;

  // Connection banner
  showConnectionBanner: boolean;
  setShowConnectionBanner: (v: boolean) => void;
}

export const useChatStore = create<ChatStore>((set, get) => ({
  sessions: [],
  activeSessionId: null,

  async loadSessions(repo: ChatRepository) {
    const sessions = await repo.listSessions();
    set({ sessions });
  },

  setActiveSession(id: string) {
    set({ activeSessionId: id, messages: [], drafts: [], chatState: "idle" });
  },

  addSession(session: Session) {
    set((s) => ({ sessions: [session, ...s.sessions] }));
  },

  messages: [],

  async loadMessages(repo: ChatRepository, sessionId: string) {
    const messages = await repo.getMessages(sessionId);
    set({ messages });
  },

  addUserMessage(content: string): Message {
    const sessionId = get().activeSessionId;
    const msg: Message = {
      id: `msg_${generateRequestId()}`,
      sessionId: sessionId ?? "unknown",
      role: "user",
      content,
      status: "sent",
      createdAt: new Date().toISOString(),
    };
    set((s) => ({ messages: [...s.messages, msg] }));
    return msg;
  },

  chatState: "idle",
  setChatState(chatState: ChatState) {
    set({ chatState });
  },

  currentAssistantMessageId: null,

  drafts: [],

  addDraft(draft: PlanDraft) {
    set((s) => ({ drafts: [...s.drafts, draft] }));
  },

  updateDraft(id: string, updates: Partial<PlanDraft>) {
    set((s) => ({
      drafts: s.drafts.map((d) => (d.id === id ? { ...d, ...updates } : d)),
    }));
  },

  removeDraft(id: string) {
    set((s) => ({ drafts: s.drafts.filter((d) => d.id !== id) }));
  },

  getSessionDrafts(sessionId: string): PlanDraft[] {
    return get().drafts.filter((d) => d.sessionId === sessionId);
  },

  userScrolledUp: false,
  setUserScrolledUp(userScrolledUp: boolean) {
    set({ userScrolledUp });
  },

  showConnectionBanner: false,
  setShowConnectionBanner(showConnectionBanner: boolean) {
    set({ showConnectionBanner });
  },
}));
