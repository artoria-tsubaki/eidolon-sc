// 浏览器里存的自带密钥（GLOSSARY.md「自带密钥」，ADR-0017）：服务器不保存，只存在这个浏览器的 localStorage 里，
// 按用户分开存（键为 eidolon.ownKeys.<用户 ID>），值是明文 JSON。每次发请求都现读，别的标签页改了，下一次请求就用上。
// 存储不可用（隐私模式等）时不抛错：读到的是什么都没存，存的也存不下；`canKeepOwnKeys` 如实报告，设置页据此告诉用户。
// 不退而存在内存里。

import { REASONING_EFFORTS, type JsonObject, type ReasoningEffort } from "@/lib/reasoningEffort";

// 下面几份取值列表跟后端同步（不做代码生成）：后端改了，这里照着改。

/** 能自带 key 的服务：后端 `config.Service`。 */
export const OWN_SERVICES = ["llm", "tts", "stt", "image"] as const;
export type OwnService = (typeof OWN_SERVICES)[number];

/** 能选作免限额密钥的服务（GLOSSARY.md「免限额密钥」）：后端 `config.EXEMPTING_SERVICES`。生图从来不算，
 * 照片计不计入只看花的是谁的钱。 */
export const EXEMPTING_SERVICES = ["llm", "tts", "stt"] as const satisfies readonly OwnService[];

/** 自带的 LLM 能用的接口协议：后端 `config.LLM_PROTOCOLS`（`copy.settings.ownKeys.protocols` 给它们起名）。
 * 设置页和后台配置页的选项都从这里来。 */
export const LLM_PROTOCOLS = ["chat-completions", "responses", "anthropic"] as const;
export type LLMProtocol = (typeof LLM_PROTOCOLS)[number];

/** 语音识别的服务商：后端 `config.SttProvider`。设置页和后台配置页的选项都从这里来。 */
export const STT_PROVIDERS = ["soniox", "bailian"] as const;
export type SttProvider = (typeof STT_PROVIDERS)[number];

/** Fish Audio 的模型：后端 `config.FishAudioModel`。设置页和后台配置页的选项都从这里来。 */
export const FISH_AUDIO_MODELS = ["s2.1-pro-free", "s2.1-pro", "drama-3", "s2-pro", "s1"] as const;
export type FishAudioModel = (typeof FISH_AUDIO_MODELS)[number];

/** 自带的 key 没选模型时用的免费模型：后端 `config.DEFAULT_FISH_AUDIO_MODEL`。 */
export const DEFAULT_FISH_AUDIO_MODEL: FishAudioModel = "s2.1-pro-free";

/** 生图服务的接口形式：后端 `config.ImageFormat`。NovelAI 官方格式，或 OpenAI 图像格式。 */
export const IMAGE_FORMATS = ["novelai", "openai"] as const;
export type ImageFormat = (typeof IMAGE_FORMATS)[number];

/** 生图下拉里的官方模型（V4 以后），默认的放第一个：后端 `config.DEFAULT_NOVELAI_MODEL`。不在里面的可以手填。 */
export const IMAGE_MODELS = [
  "nai-diffusion-5-curated",
  "nai-diffusion-5-full",
  "nai-diffusion-4-5-curated",
  "nai-diffusion-4-5-full",
  "nai-diffusion-4-curated-preview",
  "nai-diffusion-4-full",
] as const;

/** 生图的默认接口地址：后端 `config.DEFAULT_NOVELAI_BASE_URL`，NovelAI 官方格式填到域名根。 */
export const DEFAULT_IMAGE_BASE_URL = "https://image.novelai.net";

/** 阿里云百炼的 key 能用的地域，校验时由服务器判断出来：后端 `config.BailianRegion`。 */
export const BAILIAN_REGIONS = ["beijing", "singapore"] as const;
export type BailianRegion = (typeof BAILIAN_REGIONS)[number];

/** 自带的一个模型（后端 `OwnLLMInput`）：连接的四项，加上思考强度和额外请求参数，这两项没有就是不设置。 */
export type LocalModel = {
  api_key: string;
  base_url: string;
  model: string;
  protocol: LLMProtocol;
  reasoning_effort?: ReasoningEffort;
  extra_body?: JsonObject;
};

/** 自带的 LLM，原样就是请求里 `own_keys` 的 `llm`（后端 `OwnChatLLMInput`）：`reasoning_effort` 用于文字对话和
 * 提炼记忆、起对话标题（没另配后台模型时），通话里的回合用 `call_reasoning_effort`。 */
export type LocalLLM = LocalModel & { call_reasoning_effort?: ReasoningEffort };

/** 语音合成（Fish Audio）的 key，连同选的模型；没有模型的是能选模型以前存的，后端按免费的默认模型合成。 */
export type LocalTtsKey = { api_key: string; model?: FishAudioModel };

/** 语音识别的 key，连同它所属的服务商；百炼的还有地域。 */
export type LocalSttKey = { api_key: string; provider: SttProvider; region?: BailianRegion };

/** 自带的生图（后端 `OwnImageInput`）：接口形式、地址、key、模型四项；同时生成几张和画风不归用户设。 */
export type LocalImage = { api_format: ImageFormat; base_url: string; api_key: string; model: string };

/** 一位用户在这个浏览器里存的自带密钥，每项都可以没有。原样就是请求里的 `own_keys`（后端 `OwnKeysInput`）。
 * `memory_llm` 是另配的后台模型：只跟着 `llm` 存在，不算一项服务（`ownServices` 不报它）。 */
export type LocalOwnKeys = {
  llm?: LocalLLM;
  tts?: LocalTtsKey;
  stt?: LocalSttKey;
  memory_llm?: LocalModel;
  vision_llm?: LocalModel;
  image?: LocalImage;
};

/** 浏览器里存的一项：一项服务的 key，或者后台模型。 */
export type OwnKeyItem = keyof LocalOwnKeys;

/** 存储键的前缀，后面接用户 ID。 */
const STORAGE_PREFIX = "eidolon.ownKeys.";

function storageKey(userId: string): string {
  return `${STORAGE_PREFIX}${userId}`;
}

/** 这位用户在这个浏览器里存的自带密钥；存错了的那一项当作没有。 */
export function localOwnKeys(userId: string): LocalOwnKeys {
  let stored: unknown;
  try {
    stored = JSON.parse(localStorage.getItem(storageKey(userId)) ?? "{}");
  } catch {
    return {};
  }
  if (!isRecord(stored)) return {};
  const keys: LocalOwnKeys = {};
  const { llm, tts, stt, memory_llm, image } = stored;
  const vision = localModel(stored.vision_llm);
  if (vision) keys.vision_llm = vision;
  const model = localModel(llm);
  if (model) {
    keys.llm = model;
    if (isRecord(llm) && isOneOf(llm.call_reasoning_effort, REASONING_EFFORTS)) {
      keys.llm.call_reasoning_effort = llm.call_reasoning_effort;
    }
    // 后台模型只附加在自带 LLM 上：没有自带 LLM（或它存错了）时当作没有。
    const memoryModel = localModel(memory_llm);
    if (memoryModel) keys.memory_llm = memoryModel;
  }
  if (isRecord(tts) && isString(tts.api_key)) {
    keys.tts = { api_key: tts.api_key };
    if (isOneOf(tts.model, FISH_AUDIO_MODELS)) keys.tts.model = tts.model;
  }
  if (
    isRecord(stt) &&
    isString(stt.api_key) &&
    isOneOf(stt.provider, STT_PROVIDERS) &&
    (stt.region === undefined || isOneOf(stt.region, BAILIAN_REGIONS))
  ) {
    keys.stt = { api_key: stt.api_key, provider: stt.provider };
    if (stt.region !== undefined) keys.stt.region = stt.region;
  }
  if (
    isRecord(image) &&
    isOneOf(image.api_format, IMAGE_FORMATS) &&
    isString(image.base_url) &&
    isString(image.api_key) &&
    isString(image.model)
  ) {
    keys.image = { api_format: image.api_format, base_url: image.base_url, api_key: image.api_key, model: image.model };
  }
  return keys;
}

/** 存下的一个自带模型；存错了就没有。存下它以前没有、或者存错了的思考强度和额外请求参数当作不设置。 */
function localModel(stored: unknown): LocalModel | undefined {
  if (
    !isRecord(stored) ||
    !isString(stored.api_key) ||
    !isString(stored.base_url) ||
    !isString(stored.model) ||
    !isOneOf(stored.protocol, LLM_PROTOCOLS)
  ) {
    return undefined;
  }
  const model: LocalModel = {
    api_key: stored.api_key,
    base_url: stored.base_url,
    model: stored.model,
    protocol: stored.protocol,
  };
  if (isOneOf(stored.reasoning_effort, REASONING_EFFORTS)) model.reasoning_effort = stored.reasoning_effort;
  if (isRecord(stored.extra_body)) model.extra_body = stored.extra_body;
  return model;
}

/** 存下（或换掉）这位用户的某一项。 */
export function keepOwnKey<K extends OwnKeyItem>(userId: string, item: K, key: NonNullable<LocalOwnKeys[K]>): void {
  write(userId, { ...localOwnKeys(userId), [item]: key });
}

/** 删掉这位用户的某一项：一项服务的 key 删掉就改回用部署者的服务；删掉自带 LLM 时连同后台模型一起删，
 * 只删后台模型就改回由自带 LLM 提炼记忆、起对话标题。 */
export function forgetOwnKey(userId: string, item: OwnKeyItem): void {
  const keys = localOwnKeys(userId);
  delete keys[item];
  if (item === "llm") delete keys.memory_llm;
  write(userId, keys);
}

/** 删掉这位用户在这个浏览器里存的全部自带密钥。 */
export function forgetOwnKeys(userId: string): void {
  try {
    localStorage.removeItem(storageKey(userId));
  } catch {
    // 存储不可用：本来也什么都没存下。
  }
  changed();
}

/** 一次文字回合要带的 `own_keys`：这位用户存的 LLM（连同后台模型）和生图，文字回合用不上语音 key，不带出去；
 * 两样都没存就不带。 */
export function turnOwnKeys(
  userId: string,
): Pick<LocalOwnKeys, "llm" | "memory_llm" | "image" | "vision_llm"> | undefined {
  const { llm, memory_llm, image, vision_llm } = localOwnKeys(userId);
  if (!llm && !image && !vision_llm) return undefined;
  return { ...(llm && { llm, ...(memory_llm && { memory_llm }) }), ...(image && { image }), ...(vision_llm && { vision_llm }) };
}

/** 朗读（GLOSSARY.md「朗读」）要带的 `own_keys`：只带这位用户存的语音合成那一把，没存就不带。 */
export function readAloudOwnKeys(userId: string): Pick<LocalOwnKeys, "tts"> | undefined {
  const { tts } = localOwnKeys(userId);
  return tts && { tts };
}

/** 这位用户存的语音合成 key 用哪个模型（没选是免费的那个），没存就没有：文字回合只带这个名字、不带 key，
 * 她按朗读会用的模型写语气提示。 */
export function readAloudModel(userId: string): FishAudioModel | undefined {
  const { tts } = localOwnKeys(userId);
  return tts && (tts.model ?? DEFAULT_FISH_AUDIO_MODEL);
}

/** 存了 key 的那几项服务：通话预检时只报这些服务名，不带 key；也是判断计不计入限额的依据。 */
export function ownServices(keys: LocalOwnKeys): OwnService[] {
  return OWN_SERVICES.filter((service) => keys[service] !== undefined);
}

/** 这个标签页存、删自带密钥时发的事件；别的标签页改了，浏览器发 `storage`。 */
const CHANGED = "eidolon.ownKeys.changed";

/** 自带密钥一有改动（这个标签页或别的标签页）就调用 `onChange`，供 `useSyncExternalStore` 订阅；返回退订函数。 */
export function subscribeOwnKeys(onChange: () => void): () => void {
  const onStorage = (e: Event) => {
    const key = (e as StorageEvent).key;
    if (key === null || key.startsWith(STORAGE_PREFIX)) onChange(); // null：整个存储被清空
  };
  window.addEventListener(CHANGED, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(CHANGED, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

function changed(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CHANGED));
}

/** 这个浏览器能不能存自带密钥：试着写一下再删掉。隐私模式、禁用了本地存储时是不能。 */
export function canKeepOwnKeys(): boolean {
  const probe = `${STORAGE_PREFIX}probe`;
  try {
    localStorage.setItem(probe, "1");
    localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

/** 界面上再显示时，一把 key 只露出末 4 位。 */
export function last4(apiKey: string): string {
  return apiKey.slice(-4);
}

function write(userId: string, keys: LocalOwnKeys): void {
  try {
    localStorage.setItem(storageKey(userId), JSON.stringify(keys));
  } catch {
    // 存储不可用：这次存不下。
  }
  changed();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isOneOf<T extends string>(value: unknown, known: readonly T[]): value is T {
  return typeof value === "string" && (known as readonly string[]).includes(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

/** 一次通话要带的自带密钥：`ownKeys` 是 offer 带的 `own_keys`（存了的全部几项，什么都没存就不带），
 * `services` 是拨号前预检要报的服务名（只报名、不带 key）。两者出自同一次读取，预检报的服务就是 offer 带的。 */
export type CallOwnKeys = { ownKeys: LocalOwnKeys | undefined; services: OwnService[] };

/** 这位用户一次通话要带的自带密钥，调用时现读。 */
export function callOwnKeys(userId: string): CallOwnKeys {
  const keys = localOwnKeys(userId);
  const services = ownServices(keys);
  return { ownKeys: services.length > 0 ? keys : undefined, services };
}
