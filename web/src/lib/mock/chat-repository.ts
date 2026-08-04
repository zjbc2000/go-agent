// ============================================================
// Mock Chat Repository — typed mock with all required scenarios
// ============================================================

import type {
  ChatEvent,
  Message,
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
let messages: Record<string, Message[]> = {
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

async function* simulateStream(content: string, messageId: string): AsyncIterable<ChatEvent> {
  yield { type: "message-start", messageId };

  const tokens = content.split("");
  for (let i = 0; i < tokens.length; i += 2) {
    await delay(30 + Math.random() * 40);
    yield { type: "token", messageId, text: tokens.slice(i, i + 2).join("") };
  }

  yield { type: "done", messageId };
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

    sendMessage(
      sessionId: string,
      content: string,
      requestId: string,
    ): AsyncIterable<ChatEvent> {
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
      const responseText = content.includes("规划") || content.includes("计划")
        ? MOCK_RESPONSES.plan
        : MOCK_RESPONSES.default;

      const assistantMsg: Message = {
        id: assistantMsgId,
        sessionId,
        role: "assistant",
        content: "",
        status: "streaming",
        createdAt: new Date().toISOString(),
      };

      async function* wrapStream(): AsyncGenerator<ChatEvent> {
        for await (const event of simulateStream(responseText, assistantMsgId)) {
          yield event;
        }
        // Persist final message
        messages[sessionId] = [
          ...messages[sessionId],
          { ...assistantMsg, content: responseText, status: "completed" },
        ];
      }

      return wrapStream();
    },

    async stopGeneration(_messageId: string): Promise<void> {
      // In mock, just mark as completed
    },

    async createSession(): Promise<Session> {
      const session: Session = {
        id: `sess_${generateId("s")}`,
        title: "新对话",
        lastMessageAt: new Date().toISOString(),
        createdAt: new Date().toISOString(),
      };
      sessions = [session, ...sessions];
      messages[session.id] = [];
      return session;
    },
  };
}
