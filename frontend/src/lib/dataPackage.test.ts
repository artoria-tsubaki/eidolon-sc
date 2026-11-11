import { strToU8, zipSync } from "fflate";
import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  choose,
  chosenCategories,
  FORMAT,
  exportHref,
  packageFailure,
  importSummary,
  isDisabled,
  readManifest,
  selectAll,
} from "@/lib/dataPackage";

describe("勾选框", () => {
  it("默认全选给出的几类", () => {
    expect(chosenCategories(selectAll(["conversations", "photos", "memories", "profile"]))).toEqual([
      "conversations",
      "photos",
      "memories",
      "profile",
    ]);
    expect(chosenCategories(selectAll(["profile"]))).toEqual(["profile"]);
  });

  it("不勾对话时照片既不能勾、也算没勾；再勾上对话，照片仍是没勾", () => {
    const all = selectAll(["conversations", "photos", "memories", "profile"]);

    const noConversations = choose(all, "conversations", false);
    expect(isDisabled(noConversations, "photos")).toBe(true);
    expect(chosenCategories(noConversations)).toEqual(["memories", "profile"]);
    expect(chosenCategories(choose(noConversations, "photos", true))).toEqual(["memories", "profile"]);

    const back = choose(noConversations, "conversations", true);
    expect(isDisabled(back, "photos")).toBe(false);
    expect(chosenCategories(back)).toEqual(["conversations", "memories", "profile"]);
  });

  it("导出地址按顺序列出勾了的几类", () => {
    const choice = choose(selectAll(["conversations", "photos", "memories", "profile"]), "memories", false);
    expect(exportHref(chosenCategories(choice))).toBe("/api/account/data-package?include=conversations%2Cphotos%2Cprofile");
  });
});

function zip(entries: Record<string, [string, number]>): Blob {
  const files = Object.fromEntries(
    Object.entries(entries).map(([name, [text, level]]) => [name, [strToU8(text), { level }]]),
  );
  return new Blob([zipSync(files as Parameters<typeof zipSync>[0])]);
}

const MANIFEST = { format: 1, exported_at: "2026-10-19T08:00:00.000000+00:00", categories: ["profile"], counts: { profile: 1 } };

describe("读数据包开头的 manifest.json", () => {
  it("列出包里有、这里能导入的几类", async () => {
    const manifest = { ...MANIFEST, categories: ["conversations", "photos", "profile", "cycles", "letters", "albums"] };
    const file = zip({ "manifest.json": [JSON.stringify(manifest), 0], "data.json": ["{}", 6] });

    expect(await readManifest(file)).toEqual({
      kind: "package",
      categories: ["conversations", "photos", "profile", "cycles", "letters"],
    });
  });

  it("格式比这里新时是 too_new", async () => {
    const file = zip({ "manifest.json": [JSON.stringify({ ...MANIFEST, format: FORMAT + 1 }), 0], "data.json": ["{}", 6] });

    expect(await readManifest(file)).toEqual({ kind: "too_new" });
  });

  it("超过 200 MB 时不读，直接是太大", async () => {
    const file = zip({ "manifest.json": [JSON.stringify(MANIFEST), 0], "data.json": ["{}", 6] });
    const large = { size: 200 * 1024 * 1024 + 1, slice: () => file.slice(), stream: () => file.stream() } as Blob;

    expect(await readManifest(large)).toEqual({ kind: "too_large" });
  });

  it("读不出来就是坏包", async () => {
    const cases: Record<string, Blob> = {
      "不是 zip": new Blob(["not a zip at all"]),
      "第一个条目不是 manifest.json": zip({ "data.json": ["{}", 6], "manifest.json": [JSON.stringify(MANIFEST), 0] }),
      "不是 JSON": zip({ "manifest.json": ["{", 0] }),
      "没有格式号": zip({ "manifest.json": [JSON.stringify({ categories: ["profile"] }), 0] }),
      "类别不是列表": zip({ "manifest.json": [JSON.stringify({ ...MANIFEST, categories: "profile" }), 0] }),
    };
    for (const [name, file] of Object.entries(cases)) {
      expect(await readManifest(file), name).toEqual({ kind: "broken" });
    }
  });
});

describe("导入结果的一句话", () => {
  const nothing = { conversations: 0, photos: 0, memories: 0, profile: false, skipped: [], cycles: 0, cycles_skipped: 0, letters: 0, letters_skipped: 0 };

  it("说导入了什么", () => {
    expect(importSummary({ ...nothing, profile: true })).toBe("导入了制作人档案。");
    expect(importSummary({ ...nothing, conversations: 3, photos: 2, memories: 12, profile: true })).toBe(
      "导入了 3 段对话、2 张照片、12 条记忆和制作人档案。",
    );
    expect(importSummary({ ...nothing, memories: 1 })).toBe("导入了 1 条记忆。");
    expect(importSummary({ ...nothing, memories: 1, cycles: 5, profile: true })).toBe(
      "导入了 1 条记忆、5 条周期记录和制作人档案。",
    );
    expect(importSummary({ ...nothing, conversations: 1, letters: 2 })).toBe("导入了 1 段对话、2 封信。");
  });

  it("什么也没新加时照实说", () => {
    expect(importSummary(nothing)).toBe("没有新的内容要导入。");
  });

  it("带上跳过的角色", () => {
    expect(
      importSummary({
        ...nothing,
        conversations: 1,
        skipped: [
          { character_name: "月岡恋鐘", conversations: 2 },
          { character_name: "櫻木真乃", conversations: 1 },
        ],
      }),
    ).toBe("导入了 1 段对话。跳过了和月岡恋鐘的 2 段、櫻木真乃的 1 段对话，这里没有她们。");
    expect(importSummary({ ...nothing, skipped: [{ character_name: "月岡恋鐘", conversations: 2 }] })).toBe(
      "没有新的内容要导入。跳过了和月岡恋鐘的 2 段对话，这里没有她。",
    );
  });

  it("带上跳过的周期记录", () => {
    expect(importSummary({ ...nothing, cycles: 3, cycles_skipped: 2 })).toBe("导入了 3 条周期记录。跳过了 2 条周期记录。");
    expect(importSummary({ ...nothing, letters: 1, letters_skipped: 2 })).toBe("导入了 1 封信。跳过了 2 封信。");
    expect(
      importSummary({ ...nothing, skipped: [{ character_name: "月岡恋鐘", conversations: 2 }], cycles_skipped: 1 }),
    ).toBe("没有新的内容要导入。跳过了和月岡恋鐘的 2 段对话，这里没有她。跳过了 1 条周期记录。");
  });
});

describe("导入、导出被拒时的提示", () => {
  it("按错误码给一句", () => {
    expect(packageFailure(new ApiError("x", 409, { code: "busy" })).message).toBe("已经有一个数据包在导入或导出");
    expect(packageFailure(new ApiError("x", 422, { code: "broken" })).message).toBe("这不是一个能用的数据包");
    expect(packageFailure(new ApiError("x", 422, { code: "too_new" })).message).toBe(
      "这个数据包来自更新的版本，请部署者升级后再导入",
    );
  });

  it("反向代理挡下的太大没有错误码，也提示不带照片重新导出", () => {
    expect(packageFailure(new ApiError("请求失败（413）", 413)).message).toBe(
      "数据包太大了（最大 200 MB），可以不带照片重新导出",
    );
  });

  it("今天的次数用完时说几点以后能再导，按这台设备的时区；不指去自带密钥，自带密钥绕不过导入的次数", () => {
    const usedUp = new ApiError("x", 429, { code: "used_up", retryAt: "2026-10-19T16:00:00+00:00" });

    expect(packageFailure(usedUp, "Asia/Shanghai").message).toBe("今天的导入次数用完了，00:00 以后可以再导入");
    expect(packageFailure(usedUp, "Asia/Tokyo").message).toBe("今天的导入次数用完了，01:00 以后可以再导入");
    expect(packageFailure(usedUp).toOwnKeys).toBeFalsy();
  });

  it("没有错误码时照后端说的；没登录时带上去登录的链接", () => {
    const signedOut = packageFailure(new ApiError("请先登录", 401));

    expect(signedOut.message).toBe("请先登录");
    expect(signedOut.signedOut).toBe(true);
    expect(packageFailure(new TypeError("Failed to fetch")).message).toBe("Failed to fetch");
  });
});
