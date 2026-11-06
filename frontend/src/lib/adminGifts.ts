// 后台的礼物（GLOSSARY.md「重置卡」）：管理者一次给所有现有用户各发一张重置卡，补偿判断模型误判的那段时间。
// 之后注册的拿不到，发出后不能收回。全站发放只记时刻和实际张数；单用户资源发放见 adminUserGifts。

import { api, json } from "@/lib/api";

/** 一次发放：时刻（ISO）和实际发出的张数（执行那一刻的现有用户数，可能和确认框里的不同）。 */
export type ResetCardGrant = { granted_at: string; count: number };

/** 配置页那一块读到的：现有用户数（确认框里的 n）、上次发放（从没发过是 null）、好感度和礼物是否开放。 */
export type AdminGifts = { users: number; last_grant: ResetCardGrant | null; open: boolean };

export async function getAdminGifts(): Promise<AdminGifts> {
  return json(await api("/api/admin/gifts"));
}

/** 给所有现有用户各发一张。没开放时抛 `giftsClosed` 的 ApiError。 */
export async function grantResetCards(): Promise<ResetCardGrant> {
  return json(await api("/api/admin/gifts/reset-card-grants", { method: "POST" }));
}
