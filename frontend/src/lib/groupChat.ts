import { api, expectNoContent, json, serverSentEvents, type Photo } from "@/lib/api";
import { localOwnKeys } from "@/lib/localOwnKeys";

export type GroupSegment = { ja: string; zh: string };
export type GroupMessage = {
  id: string;
  character_id: string | null;
  text: string | null;
  segments: GroupSegment[];
  created_at: string;
  photo?: Photo | null;
  reply_to?: string | null;
};
export type GroupStatus = "running" | "done" | "failed" | "interrupted";
export type GroupTurn = {
  id: number;
  status: GroupStatus;
  mentioned_id?: string | null;
  mentioned_ids?: string[] | null;
  messages: GroupMessage[];
};
export type GroupSummary = {
  id: string;
  member_ids: string[];
  name: string;
  created_at: string;
  last_message: GroupMessage | null;
};
export type GroupDetail = GroupSummary & { turns: GroupTurn[] };

export type GroupEvent =
  | { type: "message"; turn_id: number; message: GroupMessage; mentioned_ids?: string[] }
  | { type: "delta"; turn_id: number; message_id: string; character_id: string; segment: number; lang: "ja" | "zh"; text: string }
  | { type: "discard"; message_id: string }
  | { type: "done"; turn_id: number; status: GroupStatus; detail: string | null };

export type GroupLog = {
  turns: GroupTurn[];
  draft: { turnId: number; message: GroupMessage } | null;
};

export function groupMentions(turn: GroupTurn): string[] {
  return turn.mentioned_ids ?? (turn.mentioned_id ? [turn.mentioned_id] : []);
}

export function applyGroupEvent(log: GroupLog, event: GroupEvent, mentions: string[]): GroupLog {
  if (event.type === "discard") {
    return log.draft?.message.id === event.message_id ? { ...log, draft: null } : log;
  }
  const turn = log.turns.find((item) => item.id === event.turn_id);
  if (event.type === "delta") {
    const previous = log.draft?.message.id === event.message_id ? log.draft.message.segments : [];
    const segments = [...previous];
    while (segments.length <= event.segment) segments.push({ ja: "", zh: "" });
    const line = segments[event.segment];
    segments[event.segment] = { ...line, [event.lang]: line[event.lang] + event.text };
    return {
      ...log,
      draft: { turnId: event.turn_id, message: {
        id: event.message_id, character_id: event.character_id, text: null, segments, created_at: "",
      } },
    };
  }
  if (event.type === "done") {
    return {
      turns: log.turns.map((item) => item.id === event.turn_id ? { ...item, status: event.status } : item),
      draft: log.draft?.turnId === event.turn_id ? null : log.draft,
    };
  }
  const message = event.message;
  const draft = log.draft?.message.id === message.id ? null : log.draft;
  if (!turn) {
    return {
      turns: [...log.turns, { id: event.turn_id, status: "running", mentioned_ids: event.mentioned_ids ?? mentions, messages: [message] }],
      draft,
    };
  }
  if (turn.messages.some((item) => item.id === message.id)) return { ...log, draft };
  return {
    turns: log.turns.map((item) => item === turn ? { ...item, messages: [...item.messages, message] } : item),
    draft,
  };
}

const resource = "/api/group-chats";
const groupPath = (id: string) => resource + "/" + encodeURIComponent(id);

export function groupChatPage(id: string): string {
  return "/group-chat/" + encodeURIComponent(id);
}

export async function listGroups(signal?: AbortSignal): Promise<GroupSummary[]> {
  return json(await api(resource, { signal }));
}

export async function createGroup(memberIds: string[]): Promise<GroupSummary> {
  return json(await api(resource, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ member_ids: memberIds }),
  }));
}

export async function readGroup(id: string, signal?: AbortSignal): Promise<GroupDetail> {
  return json(await api(groupPath(id), { signal }));
}

export async function deleteGroup(id: string): Promise<void> {
  await expectNoContent(await api(groupPath(id), { method: "DELETE" }));
}

export async function* sendGroupTurn(id: string, userId: string, text: string, mentions: string[],
  requestId: string, signal: AbortSignal, replyTo: string | null = null): AsyncGenerator<GroupEvent> {
  const { llm, image } = localOwnKeys(userId);
  const res = await api(groupPath(id) + "/turns", {
    method: "POST",
    headers: { "content-type": "application/json" },
    signal,
    body: JSON.stringify({ text, mention_ids: mentions, request_id: requestId, reply_to: replyTo ?? undefined,
      own_keys: llm || image ? { llm, image } : undefined }),
  });
  yield* serverSentEvents<GroupEvent>(res);
}

export function newGroupRequestId(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
