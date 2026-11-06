import { api, json, ApiError } from "@/lib/api";
import { copy } from "@/i18n";
import type { GiftInfo, Owned } from "@/lib/gifts";

export type Resources = { backpack: Owned[]; draws: number; reset_cards: number };
export type UserGifts = Resources & { open: boolean; catalog: GiftInfo[] };
export type GrantInput = { request_id: string; gifts: Owned[]; draws: number; reset_cards: number; note: string };
export type UserGrant = GrantInput & { granted_at: string; actor: { id: string; name: string } | null };
export type GrantHistory = { grants: UserGrant[]; total: number };
export type GrantResult = { grant: UserGrant; resources: Resources };
export type GrantDraft = { gifts: { gift_id: string; count: string }[]; draws: string; reset_cards: string; note: string };
export const emptyGrant = (): GrantDraft => ({ gifts: [], draws: "0", reset_cards: "0", note: "" });
const path = (id: string) => `/api/admin/users/${encodeURIComponent(id)}`;

export async function getUserGifts(id: string): Promise<UserGifts> {
  return json(await api(`${path(id)}/gifts`));
}
export async function getUserGrants(id: string, offset: number): Promise<GrantHistory> {
  return json(await api(`${path(id)}/gift-grants?offset=${offset}`));
}
export async function grantUserGifts(id: string, body: GrantInput): Promise<GrantResult> {
  return json(await api(`${path(id)}/gift-grants`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  }));
}

export function grantProblems(draft: GrantDraft, catalog: GiftInfo[]): Record<string, string> {
  const t = copy.admin.userGifts;
  const errors: Record<string, string> = {};
  const valid = (value: string, min: number) => /^\d+$/.test(value) && Number(value) >= min && Number(value) <= 999;
  for (const key of ["draws", "reset_cards"] as const) {
    if (!valid(draft[key], 0)) errors[key] = t.quantityError;
  }
  const seen = new Set<string>();
  for (const gift of draft.gifts) {
    if (!valid(gift.count, 1)) errors[gift.gift_id] = t.giftQuantityError;
    if (!catalog.some((g) => g.id === gift.gift_id && !g.retired) || seen.has(gift.gift_id)) {
      errors[gift.gift_id] = t.giftError;
    }
    seen.add(gift.gift_id);
  }
  if (draft.note.length > 500) errors.note = t.noteError;
  if (!draft.gifts.length && Number(draft.draws) === 0 && Number(draft.reset_cards) === 0) errors.empty = t.emptyError;
  return errors;
}

export function grantInput(draft: GrantDraft, requestId: string): GrantInput {
  return { request_id: requestId, gifts: draft.gifts.map((g) => ({ gift_id: g.gift_id, count: Number(g.count) })),
    draws: Number(draft.draws), reset_cards: Number(draft.reset_cards), note: draft.note.trim() };
}

export function resourceSummary(resources: { gifts: Owned[]; draws: number; reset_cards: number }, catalog: GiftInfo[]): string {
  const t = copy.admin.userGifts;
  return [
    ...resources.gifts.map((g) => t.giftCount(catalog.find((item) => item.id === g.gift_id)?.name ?? g.gift_id, g.count)),
    ...(resources.reset_cards ? [t.cardCount(resources.reset_cards)] : []),
    ...(resources.draws ? [t.drawCount(resources.draws)] : []),
  ].join("、");
}

/** An uncertain response keeps the exact request locked until a retry resolves it. */
export type GrantFlow = { stage: "edit" | "review" | "submitting" | "uncertain"; request: GrantInput | null };
export const initialGrantFlow: GrantFlow = { stage: "edit", request: null };
export type GrantAction = { type: "review"; request: GrantInput } | { type: "submit" | "edit" | "saved" } | { type: "failed"; error: unknown };
export function grantFlow(state: GrantFlow, action: GrantAction): GrantFlow {
  if (action.type === "saved") return initialGrantFlow;
  if (action.type === "review" && state.stage === "edit") return { stage: "review", request: action.request };
  if (action.type === "submit" && (state.stage === "review" || state.stage === "uncertain")) return { ...state, stage: "submitting" };
  if (action.type === "edit" && state.stage === "review") return initialGrantFlow;
  if (action.type === "failed" && state.stage === "submitting") {
    const refused = action.error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(action.error.status);
    return { ...state, stage: refused ? "review" : "uncertain" };
  }
  return state;
}
