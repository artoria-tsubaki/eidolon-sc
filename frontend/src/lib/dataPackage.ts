// 数据包（GLOSSARY.md「数据包」，格式见 docs/data-package-format.md）在设置页上的逻辑：导出、导入两边的勾选框，
// 选了文件后只读开头的 manifest.json 判断能不能导入、有哪几类，导入结果拼成的一句话，以及失败的提示。

import { Unzip, UnzipPassThrough } from "fflate";

import { copy } from "@/i18n";
import { ApiError, type DataPackageImported } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";

/** 数据包里可能有的几类，按清单里的顺序。照片挂在对话下面，不带对话就不带照片。 */
export const CATEGORIES = ["conversations", "photos", "memories", "profile", "cycles", "letters"] as const;

export type Category = (typeof CATEGORIES)[number];

/** 这一版能读的最新格式，和后端 `data_package.FORMAT` 一样。 */
export const FORMAT = 6;

const MANIFEST = "manifest.json";

/** 数据包最大多少字节，和后端的上限一样：超了选文件时就拦下，不用传上去。 */
export const MAX_PACKAGE_BYTES = 200 * 1024 * 1024;

/** manifest.json 最大多少字节，超了当坏包：它只有几行。 */
const MAX_MANIFEST_BYTES = 64 * 1024;

/** 每一类勾没勾；没列出的类别不在这一页上。 */
export type Choice = Partial<Record<Category, boolean>>;

/** 给出的几类全勾上（默认）。 */
export function selectAll(categories: readonly Category[]): Choice {
  return Object.fromEntries(categories.map((category) => [category, true]));
}

/** 勾上或取消一类。没勾对话时照片勾不上；取消对话时照片也一起取消。 */
export function choose(choice: Choice, category: Category, checked: boolean): Choice {
  if (isDisabled(choice, category)) return choice;
  const next = { ...choice, [category]: checked };
  if (category === "conversations" && !checked && "photos" in next) next.photos = false;
  return next;
}

/** 这一类现在勾不了：不勾对话时的照片。 */
export function isDisabled(choice: Choice, category: Category): boolean {
  return category === "photos" && !choice.conversations;
}

/** 勾了的几类，按顺序。 */
export function chosenCategories(choice: Choice): Category[] {
  return CATEGORIES.filter((category) => choice[category] && !isDisabled(choice, category));
}

/** 导出的地址：浏览器直接跳过去下载，不经过 JS 的内存。被拒时浏览器只拿到一页 JSON，所以先用 `checkDataPackageExport` 问一次。 */
export function exportHref(categories: readonly Category[]): string {
  return `/api/account/data-package?${new URLSearchParams({ include: categories.join(",") })}`;
}

/** 读了数据包开头以后知道的：能导入（包里有、这一页能导入的几类）、来自更新的版本、太大，或者坏了。 */
export type ManifestReading =
  | { kind: "package"; categories: Category[] }
  | { kind: "too_new" }
  | { kind: "too_large" }
  | { kind: "broken" };

const BROKEN: ManifestReading = { kind: "broken" };

/**
 * 流式读出数据包的第一个条目 manifest.json（它不压缩，读到就停），不把整个文件读进内存。
 * 第一个条目不是不压缩的 manifest.json、JSON 不对，都算坏包；格式比这一版新的是 too_new。
 * 超过 MAX_PACKAGE_BYTES 的不读，是 too_large。
 */
export async function readManifest(file: Blob): Promise<ManifestReading> {
  if (file.size > MAX_PACKAGE_BYTES) return { kind: "too_large" };
  const start = new Uint8Array(await file.slice(0, 4).arrayBuffer());
  if (!isZip(start)) return BROKEN;
  const reader = file.stream().getReader();
  let reading: ManifestReading | null = null;
  let seenFirst = false;
  const chunks: Uint8Array[] = [];
  let size = 0;
  const unzip = new Unzip((entry) => {
    if (seenFirst) return;
    seenFirst = true;
    if (entry.name !== MANIFEST || entry.compression !== 0) {
      reading = BROKEN;
      return;
    }
    entry.ondata = (error, data, final) => {
      if (reading) return;
      size += data?.length ?? 0;
      if (error || size > MAX_MANIFEST_BYTES) {
        reading = BROKEN;
        return;
      }
      chunks.push(data);
      if (final) reading = fromManifest(new TextDecoder("utf-8", { fatal: true }), chunks);
    };
    entry.start();
  });
  unzip.register(UnzipPassThrough);
  try {
    while (reading === null) {
      const { done, value } = await reader.read();
      if (done) break;
      unzip.push(value);
    }
  } catch {
    reading = BROKEN;
  } finally {
    await reader.cancel().catch(() => {});
  }
  return reading ?? BROKEN;
}

function isZip(start: Uint8Array): boolean {
  return start.length === 4 && start[0] === 0x50 && start[1] === 0x4b && start[2] === 0x03 && start[3] === 0x04;
}

function fromManifest(decoder: TextDecoder, chunks: Uint8Array[]): ManifestReading {
  let manifest: unknown;
  try {
    manifest = JSON.parse(chunks.map((chunk) => decoder.decode(chunk, { stream: true })).join("") + decoder.decode());
  } catch {
    return BROKEN;
  }
  if (typeof manifest !== "object" || manifest === null) return BROKEN;
  const { format, categories } = manifest as { format?: unknown; categories?: unknown };
  if (typeof format !== "number" || !Number.isInteger(format)) return BROKEN;
  if (format > FORMAT) return { kind: "too_new" };
  if (!Array.isArray(categories)) return BROKEN;
  return { kind: "package", categories: CATEGORIES.filter((category) => categories.includes(category)) };
}

/** 导入完的一句话：导入了什么（只算这次新加的），跳过了和谁的几段对话、几条周期记录、几封信。 */
export function importSummary(result: DataPackageImported): string {
  const t = copy.settings.dataPackage;
  const counts = [
    result.conversations > 0 && t.count.conversations(result.conversations),
    result.photos > 0 && t.count.photos(result.photos),
    result.memories > 0 && t.count.memories(result.memories),
    result.cycles > 0 && t.count.cycles(result.cycles),
    result.letters > 0 && t.count.letters(result.letters),
  ].filter((count): count is string => Boolean(count));
  let imported: string;
  if (counts.length === 0) imported = result.profile ? t.importedProfile : t.nothingNew;
  else if (result.profile) imported = t.importedWithProfile(counts.join(t.separator));
  else imported = t.imported(counts.join(t.separator));
  if (result.skipped.length > 0) {
    const skipped = result.skipped.map((s) => t.skippedPart(s.character_name, s.conversations)).join(t.separator);
    imported += t.skipped(skipped, result.skipped.length > 1);
  }
  if (result.cycles_skipped > 0) imported += t.skippedCycles(result.cycles_skipped);
  return result.letters_skipped > 0 ? imported + t.skippedLetters(result.letters_skipped) : imported;
}

/**
 * 导入失败、导出前一问被拒时的提示：认得的错误码换成页面上的说法，今天的次数用完时带上几点以后能再导（按 `timeZone`，默认这台设备的时区，
 * 写成 HH:MM：不到一天，不写哪天）；其余照后端（或浏览器）说的。次数用完是 429，但自带密钥绕不过它，不指去自带密钥。
 */
export function packageFailure(error: unknown, timeZone?: string): Failure {
  return { ...toFailure(error), toOwnKeys: false, message: failureMessage(error, timeZone) };
}

function failureMessage(error: unknown, timeZone?: string): string {
  if (error instanceof ApiError && isRefusal(error.code)) return copy.settings.dataPackage.failures[error.code];
  // 反向代理先挡下的太大，回的 413 没有错误码。
  if (error instanceof ApiError && error.status === 413) return copy.settings.dataPackage.failures.too_large;
  if (error instanceof ApiError && error.code === "used_up" && error.retryAt) {
    const time = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone }).format(
      new Date(error.retryAt),
    );
    return copy.settings.dataPackage.usedUp(time);
  }
  return (error as Error).message;
}

/** 后端拒绝一个数据包、各有一句提示的错误码；前三种读开头时就看得出。 */
export type Refusal = keyof typeof copy.settings.dataPackage.failures;

function isRefusal(code: string | null): code is Refusal {
  return code !== null && Object.hasOwn(copy.settings.dataPackage.failures, code);
}
