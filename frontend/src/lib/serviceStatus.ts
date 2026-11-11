// The service status (GLOSSARY.md「服务状态」): whether each of the deployer's services works
// now, summed up by the backend over the latest minutes, of those the admin console shows.

import { api, json } from "@/lib/api";

/** The deployer's services, in the order they are shown. */
export const STATUS_SERVICES = ["chat_llm", "memory_llm", "vision_llm", "tts", "stt", "judge", "embedding", "rerank", "image"] as const;

export type StatusService = (typeof STATUS_SERVICES)[number];

/** Working; failing now and then; mostly failing; nothing to go by (no calls, not tried). */
export type Status = "ok" | "unsteady" | "down" | "idle";

export type ServiceStatus = {
  /** How many of the latest minutes it goes by. */
  window_minutes: number;
  /** Every service shown; none when the admin console shows none. */
  services: { service: StatusService; status: Status }[];
};

/** How often the list asks again, besides when it opens and when the status is opened. */
export const STATUS_REFRESH_MS = 5 * 60 * 1000;

export async function getServiceStatus(): Promise<ServiceStatus> {
  return json(await api("/api/service-status"));
}

/** What the button is marked with: down if any is, else unsteady if any is, else nothing. */
export function worst(status: ServiceStatus): "down" | "unsteady" | null {
  const statuses = new Set(status.services.map((s) => s.status));
  return statuses.has("down") ? "down" : statuses.has("unsteady") ? "unsteady" : null;
}
