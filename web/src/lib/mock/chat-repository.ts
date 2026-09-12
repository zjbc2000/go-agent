// ============================================================
// Mock Chat Repository — typed mock with all required scenarios
// ============================================================

import type {
  ChatEvent,
  CreatedRun,
  Message,
  RunSnapshot,
  RunStream,
  RunStreamOptions,
  Session,
} from "@/lib/domain/types";
import type { ChatRepository } from "@/lib/domain/repositories";
import { generateId } from "@/lib/utils/id";

// --- In-memory store ---

let sessions: Session[] = [
  {
    id: "sess_1",
    title: "今天想做什么？",
    lastMessageAt: new Date(Date.now() - 60_000).toISOString(),
    createdAt: new Date(Date.now() - 86_400_000).toISOString(),
  },
  {
    id: "sess_2",
    title: "帮我规划周末",
    lastMessageAt: new Date(Date.now() - 86_400_000).toISOString(),
    createdAt: new Date(Date.now() - 172_800_000).toISOString(),
  },
];

// deno-lint-ignore no-explicit-any
const messages: Record<string, Message[]> = {
  sess_1: [
    {
      id: "msg_1",
      sessionId: "sess_1",
      role: "user",
      content: "你好苟蛋，今天有什么推荐的？",
      status: "sent",
      createdAt: new Date(Date.now() - 120_000).toISOString(),
    },
    {
      id: "msg_2",
      sessionId: "sess_1",
      role: "assistant",
      content:
        "嗨！今天天气不错 ☀️ 我根据你的兴趣整理了这些推荐：\n\n1. **学习** — 你收藏的 Rust 教程有更新\n2. **运动** — 下午 4 点有空档可以跑步\n3. **娱乐** — 新上映的电影《沙丘 3》评分很高\n\n想深入了解哪一个？",
      status: "sent",
      createdAt: new Date(Date.now() - 110_000).toISOString(),
    },
  ],
  sess_2: [
    {
      id: "msg_3",
      sessionId: "sess_2",
      role: "user",
      content: "帮我规划一下这周末的安排",
      status: "sent",
      createdAt: new Date(Date.now() - 86_400_000).toISOString(),
    },
    {
      id: "msg_4",
      sessionId: "sess_2",
      role: "assistant",
      content: "好的，我已经帮你生成了一个周末规划草案，你看看合不合适？",
      status: "sent",
      createdAt: new Date(Date.now() - 86_390_000).toISOString(),
    },
  ],
};

// --- Helpers ---

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function pickResponse(content: string): string {
  if (content.includes("规划") || content.includes("计划")) return MOCK_RESPONSES.plan;
  return MOCK_RESPONSES.default;
}

// Predefined assistant responses for mock
const MOCK_RESPONSES: Record<string, string> = {
  default:
    "这是个好问题！让我想想…\n\n根据你之前的记录，我建议可以从这几个方向入手：\n\n- **记忆**：你之前提到过想学习 Rust\n- **兴趣**：跑步和摄影是你长期坚持的爱好\n- **任务**：明天下午有个会议需要准备\n\n要不要我帮你细化某一个？",
  plan: "好的，我已经帮你整理好了相关规划。\n\n**周末规划草案**\n\n- 🏃 周六上午 8:00 晨跑 5 公里\n- 📚 周六下午 2:00 Rust 学习 2 小时\n- 🎬 周日下午 3:00 看电影\n- 📝 周日晚上整理下周计划\n\n这个安排可以吗？需要调整的话告诉我。",
};

// --- Implementation ---

export function createMockChatRepository(): ChatRepository {
  return {
    async listSessions(): Promise<Session[]> {
      await delay(200);
      return [...sessions];
    },

    async getMessages(sessionId: string): Promise<Message[]> {
      await delay(150);
      return [...(messages[sessionId] ?? [])];
    },

    async createRun(sessionId: string, content: string): Promise<CreatedRun> {
      const userMsg: Message = {
        id: `msg_${generateId("u")}`,
        sessionId,
        role: "user",
        content,
        status: "sent",
        createdAt: new Date().toISOString(),
      };

      if (!messages[sessionId]) {
        messages[sessionId] = [];
      }
      messages[sessionId] = [...messages[sessionId], userMsg];

      // Update session
      sessions = sessions.map((s) =>
        s.id === sessionId
          ? { ...s, title: content.slice(0, 30), lastMessageAt: new Date().toISOString() }
          : s,
      );

      const assistantMsgId = `msg_${generateId("a")}`;
      const runId = `run_${generateId("r")}`;
      const responseText = pickResponse(content);

      const assistantMsg: Message = {
        id: assistantMsgId,
        sessionId,
        role: "assistant",
        content: "",
        status: "streaming",
        createdAt: new Date().toISOString(),
      };

      let seq = 0;
      let lastEventId: string | undefined;

      async function* events(): AsyncGenerator<ChatEvent> {
        lastEventId = String(++seq);
        yield { type: "message-start", messageId: assistantMsgId };

        const tokens = responseText.split("");
        for (let i = 0; i < tokens.length; i += 2) {
          await delay(30 + Math.random() * 40);
          lastEventId = String(++seq);
          yield { type: "token", messageId: assistantMsgId, text: tokens.slice(i, i + 2).join("") };
        }

        lastEventId = String(++seq);
        yield { type: "done", messageId: assistantMsgId };

        // Persist final message
        messages[sessionId] = [
          ...messages[sessionId],
          { ...assistantMsg, content: responseText, status: "completed" },
        ];
      }

      return { runId, events: events(), lastEventId: () => lastEventId };
    },

    subscribeRunEvents(options: RunStreamOptions): RunStream {
      // Mock streams never interrupt, so there is nothing to resume.
      async function* empty(): AsyncGenerator<ChatEvent> {
        // no events
      }
      return { events: empty(), lastEventId: () => options.lastEventId };
    },

    async getRun(runId: string): Promise<RunSnapshot | null> {
      return { runId, status: "completed", content: "" };
    },

    async createSession(title?: string): Promise<Session> {
      const session: Session = {
        id: `sess_${generateId("s")}`,
        title: title ?? "新对话",
        lastMessageAt: new Date().toISOString(),
        createdAt: new Date().toISOString(),
      };
      sessions = [session, ...sessions];
      messages[session.id] = [];
      return session;
    },

    async deleteSession(sessionId: string): Promise<void> {
      sessions = sessions.filter((s) => s.id !== sessionId);
      delete messages[sessionId];
    },

    async renameSession(sessionId: string, title: string): Promise<void> {
      sessions = sessions.map((s) => (s.id === sessionId ? { ...s, title } : s));
    },
  };
}
