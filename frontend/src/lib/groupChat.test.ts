import { afterEach, describe, expect, it, vi } from "vitest";

import { applyGroupEvent, createGroup, emptyBackground, presetTime, sendGroupTurn, type GroupLog, type GroupMessage } from "@/lib/groupChat";

const sent: GroupMessage = {
  id: "sent", character_id: null, text: "今天聊什么？", segments: [], created_at: "2026-11-17T09:00:00Z",
};
const answer: GroupMessage = {
  id: "mano-1", character_id: "mano", text: null, segments: [{ ja: "はい", zh: "好呀" }],
  created_at: "2026-11-17T09:00:01Z",
};

afterEach(() => vi.unstubAllGlobals());

describe("群聊", () => {
  it("时间预设按选中的时区取当天，创建时发送最终编辑值", async () => {
    const now = new Date("2026-11-23T17:30:00Z");
    const local = presetTime(21, "Asia/Tokyo", now);
    expect(local).toBe("2026-11-24T21:00");
    expect(presetTime(9, "America/New_York", now)).toBe("2026-11-23T09:00");
    const fetch = vi.fn().mockResolvedValue(new Response("{}", { status: 201 }));
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("localStorage", { getItem: () => null });
    const background = { ...emptyBackground(), time: { local, timezone: "Asia/Tokyo" }, place: "后台走廊" };
    await createGroup(["mano", "kogane"], " 排练群 ", background);
    expect(JSON.parse(fetch.mock.calls[0][1].body).name).toBe("排练群");
    expect(JSON.parse(fetch.mock.calls[0][1].body).background).toEqual(background);
  });

  it("建群按选择顺序发角色 ID，发消息用结构化点名和请求 ID", async () => {
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ id: "group" }), { status: 201 }))
      .mockResolvedValueOnce(new Response('data: {"type":"done","turn_id":1,"status":"done","detail":null}\n\n'));
    vi.stubGlobal("fetch", fetch);
    vi.stubGlobal("localStorage", { getItem: () => null });
    expect(await createGroup(["kogane", "mano"], "恋鐘、真乃")).toEqual({ id: "group" });
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ member_ids: ["kogane", "mano"], name: "恋鐘、真乃" });
    const events = [];
    for await (const event of sendGroupTurn("group", "user", "在吗", ["mano", "kogane"], "request-12345678", new AbortController().signal)) {
      events.push(event);
    }
    expect(fetch.mock.calls[1][0]).toBe("/api/group-chats/group/turns");
    expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ text: "在吗", mention_ids: ["mano", "kogane"], request_id: "request-12345678" });
    expect(events).toHaveLength(1);
  });

  it("流式文本按发送者显示，失败只丢弃正在生成的气泡", () => {
    let log: GroupLog = { turns: [], draft: null };
    log = applyGroupEvent(log, { type: "message", turn_id: 1, message: sent }, []);
    log = applyGroupEvent(log, { type: "message", turn_id: 1, message: answer }, []);
    log = applyGroupEvent(log, { type: "delta", turn_id: 1, character_id: "kogane",
      message_id: "kogane-1", segment: 0, lang: "ja", text: "よか" }, []);
    log = applyGroupEvent(log, { type: "delta", turn_id: 1, character_id: "kogane",
      message_id: "kogane-1", segment: 0, lang: "ja", text: "よ！" }, []);
    expect(log.draft?.message).toMatchObject({ character_id: "kogane", segments: [{ ja: "よかよ！", zh: "" }] });
    log = applyGroupEvent(log, { type: "discard", message_id: "kogane-1" }, []);
    expect(log.draft).toBeNull();
    log = applyGroupEvent(log, { type: "done", turn_id: 1, status: "interrupted", detail: null }, []);
    expect(log.draft).toBeNull();
    expect(log.turns[0]).toMatchObject({ status: "interrupted", messages: [sent, answer] });
  });

  it("重进后收到重播时保留历史和点名，并且不重复插入消息", () => {
    const log: GroupLog = { turns: [{ id: 1, status: "done", mentioned_id: "mano", messages: [sent, answer] }], draft: null };
    const replay = applyGroupEvent(log, { type: "message", turn_id: 1, message: answer }, []);
    expect(replay).toEqual(log);
  });
});
