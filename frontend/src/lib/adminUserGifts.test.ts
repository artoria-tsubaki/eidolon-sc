import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { emptyGrant, grantProblems, grantInput, grantFlow, initialGrantFlow, resourceSummary,
  getUserGifts, getUserGrants, grantUserGifts, type GrantDraft } from "./adminUserGifts";
import type { GiftInfo } from "./gifts";

const catalog: GiftInfo[] = [
  { id: "cake", name: "蛋糕", category: "food", rarity: "common", retired: false },
  { id: "tea", name: "红茶", category: "drink", rarity: "rare", retired: false },
  { id: "old", name: "旧礼物", category: "food", rarity: "common", retired: true },
];
const draft: GrantDraft = { gifts: [{ gift_id: "cake", count: "3" }, { gift_id: "tea", count: "2" }], draws: "10", reset_cards: "1", note: " 补偿 " };
const request = grantInput(draft, "request-1");
afterEach(() => vi.unstubAllGlobals());

describe("grant validation and review", () => {
  it("reviews multiple gifts and independent resources, trimming the optional reason", () => {
    expect(grantProblems(draft, catalog)).toEqual({});
    expect(request).toEqual({ request_id: "request-1", gifts: [{ gift_id: "cake", count: 3 }, { gift_id: "tea", count: 2 }], draws: 10, reset_cards: 1, note: "补偿" });
    expect(resourceSummary(request, catalog)).toBe("蛋糕 × 3、红茶 × 2、重置卡 × 1、抽奖次数 × 10");
    draft.gifts[0].count = "4";
    expect(request.gifts[0].count).toBe(3);
    draft.gifts[0].count = "3";
  });
  it.each(["", "-1", "1.5", "1e2", "1000", "NaN"])("rejects invalid quantities %s", (draws) => {
    expect(grantProblems({ ...draft, draws }, catalog)).toHaveProperty("draws");
  });
  it("accepts the boundaries and resources without gifts", () => {
    expect(grantProblems({ ...emptyGrant(), draws: "999" }, catalog)).toEqual({});
    expect(grantProblems({ ...emptyGrant(), reset_cards: "1" }, catalog)).toEqual({});
    expect(grantProblems(emptyGrant(), catalog)).toHaveProperty("empty");
  });
  it("rejects zero gift quantities, duplicates, retired gifts and overlong notes", () => {
    expect(grantProblems({ ...draft, gifts: [{ gift_id: "cake", count: "0" }] }, catalog)).toHaveProperty("cake");
    expect(grantProblems({ ...draft, gifts: [draft.gifts[0], draft.gifts[0]] }, catalog)).toHaveProperty("cake");
    expect(grantProblems({ ...draft, gifts: [{ gift_id: "old", count: "1" }] }, catalog)).toHaveProperty("old");
    expect(grantProblems({ ...draft, note: "x".repeat(501) }, catalog)).toHaveProperty("note");
  });
});

describe("submission lifecycle", () => {
  const reviewed = grantFlow(initialGrantFlow, { type: "review", request });
  const submitting = grantFlow(reviewed, { type: "submit" });
  it("requires review, blocks edits and duplicate state transitions while submitting", () => {
    expect(grantFlow(initialGrantFlow, { type: "submit" })).toEqual(initialGrantFlow);
    expect(submitting.stage).toBe("submitting");
    expect(grantFlow(submitting, { type: "edit" })).toEqual(submitting);
    expect(grantFlow(submitting, { type: "submit" })).toEqual(submitting);
  });
  it.each([new TypeError("offline"), new ApiError("gateway", 502)])("retries an uncertain result using the identical request", (error) => {
    const uncertain = grantFlow(submitting, { type: "failed", error });
    expect(uncertain.stage).toBe("uncertain");
    expect(grantFlow(uncertain, { type: "edit" })).toEqual(uncertain);
    expect(grantFlow(uncertain, { type: "submit" }).request).toBe(request);
  });
  it("allows corrections after a definitive refusal and clears the request after success", () => {
    const refused = grantFlow(submitting, { type: "failed", error: new ApiError("closed", 409) });
    expect(refused.stage).toBe("review");
    expect(grantFlow(refused, { type: "edit" })).toEqual(initialGrantFlow);
    expect(grantFlow(submitting, { type: "saved" })).toEqual(initialGrantFlow);
  });
});

describe("admin resource transport", () => {
  it("sends the frozen grant once per call and reads balances and paged history for refresh", async () => {
    const result = { grant: { ...request, granted_at: "2026-11-06T00:00:00Z", actor: null }, resources: { backpack: request.gifts, draws: 12, reset_cards: 1 } };
    const fetch = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(result)))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...result.resources, open: true, catalog })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ grants: [result.grant], total: 21 })));
    vi.stubGlobal("fetch", fetch);
    expect(await grantUserGifts("u/1", request)).toEqual(result);
    expect(await getUserGifts("u/1")).toMatchObject(result.resources);
    expect(await getUserGrants("u/1", 20)).toMatchObject({ total: 21 });
    expect(fetch.mock.calls[0][0]).toBe("/api/admin/users/u%2F1/gift-grants");
    expect(fetch.mock.calls[0][1]).toMatchObject({ method: "POST", body: JSON.stringify(request) });
    expect(fetch.mock.calls[2][0]).toBe("/api/admin/users/u%2F1/gift-grants?offset=20");
  });
});
