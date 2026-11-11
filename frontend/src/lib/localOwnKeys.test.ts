import { afterEach, describe, expect, it, vi } from "vitest";
import {
  callOwnKeys,
  canKeepOwnKeys,
  forgetOwnKey,
  forgetOwnKeys,
  keepOwnKey,
  localOwnKeys,
  subscribeOwnKeys,
  turnOwnKeys,
} from "@/lib/localOwnKeys";

const LLM = {
  api_key: "sk-my-own-key-1234",
  base_url: "https://llm.example.com/v1",
  model: "my-model",
  protocol: "chat-completions",
} as const;
const STT = { api_key: "sk-my-dashscope-dddd", provider: "bailian", region: "singapore" } as const;
/** An own LLM set to think hard in text turns and not at all in calls, with an extra body. */
const THINKING_LLM = {
  ...LLM,
  reasoning_effort: "high",
  call_reasoning_effort: "none",
  extra_body: { enable_thinking: true, thinking: { type: "enabled" } },
} as const;

/** This browser's local storage, in memory. */
function storage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  });
  return items;
}

afterEach(() => vi.unstubAllGlobals());

describe("own keys kept in this browser", () => {
  it("are kept for each user apart, in plain JSON under the user's own key", () => {
    const items = storage();

    keepOwnKey("u1", "llm", LLM);
    keepOwnKey("u1", "stt", STT);
    keepOwnKey("u2", "tts", { api_key: "fish-their-own-key" });

    expect(localOwnKeys("u1")).toEqual({ llm: LLM, stt: STT });
    expect(localOwnKeys("u2")).toEqual({ tts: { api_key: "fish-their-own-key" } });
    expect(localOwnKeys("u3")).toEqual({});
    expect(JSON.parse(items.get("eidolon.ownKeys.u1")!)).toEqual({ llm: LLM, stt: STT });
  });

  it("replace the one kept for a service, and forget one or all of a user's", () => {
    storage();
    keepOwnKey("u1", "llm", LLM);
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });
    keepOwnKey("u2", "llm", LLM);

    keepOwnKey("u1", "llm", { ...LLM, model: "another-model" });
    expect(localOwnKeys("u1").llm?.model).toBe("another-model");

    forgetOwnKey("u1", "llm");
    expect(localOwnKeys("u1")).toEqual({ tts: { api_key: "fish-my-own-key" } });

    forgetOwnKeys("u1");
    expect(localOwnKeys("u1")).toEqual({});
    expect(localOwnKeys("u2")).toEqual({ llm: LLM });
  });

  it("are read afresh each time, so a change in another tab holds for the next request", () => {
    const items = storage();
    keepOwnKey("u1", "llm", LLM);

    items.set("eidolon.ownKeys.u1", JSON.stringify({ llm: { ...LLM, api_key: "sk-changed-elsewhere" } }));

    expect(turnOwnKeys("u1")?.llm?.api_key).toBe("sk-changed-elsewhere");
  });

  it("leave out what is stored wrongly, keeping what is right", () => {
    storage({
      "eidolon.ownKeys.u1": JSON.stringify({
        llm: { ...LLM, protocol: "grpc" },
        tts: { api_key: 42 },
        stt: { ...STT, region: "mars" },
      }),
      "eidolon.ownKeys.u2": "{not json",
      "eidolon.ownKeys.u3": JSON.stringify({ llm: LLM, stt: { api_key: "soniox-key-bbbb", provider: "soniox" } }),
    });

    expect(localOwnKeys("u1")).toEqual({});
    expect(localOwnKeys("u2")).toEqual({});
    expect(localOwnKeys("u3")).toEqual({ llm: LLM, stt: { api_key: "soniox-key-bbbb", provider: "soniox" } });
  });
});

describe("an own Fish Audio key's model", () => {
  it("is kept with it, and goes with each call", () => {
    storage();
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key", model: "drama-3" });

    expect(localOwnKeys("u1")).toEqual({ tts: { api_key: "fish-my-own-key", model: "drama-3" } });
    expect(callOwnKeys("u1").ownKeys).toEqual({ tts: { api_key: "fish-my-own-key", model: "drama-3" } });
  });

  it("is left for the server to choose where stored wrongly, the key kept", () => {
    storage({ "eidolon.ownKeys.u1": JSON.stringify({ tts: { api_key: "fish-my-own-key", model: "drama3" } }) });

    expect(localOwnKeys("u1")).toEqual({ tts: { api_key: "fish-my-own-key" } });
  });
});

describe("an own LLM's reasoning efforts and extra body", () => {
  it("are kept with it, for text turns and calls apart", () => {
    storage();
    keepOwnKey("u1", "llm", THINKING_LLM);

    expect(localOwnKeys("u1")).toEqual({ llm: THINKING_LLM });
  });

  it("are unset where stored wrongly, the LLM itself kept", () => {
    storage({
      "eidolon.ownKeys.u1": JSON.stringify({
        llm: { ...LLM, reasoning_effort: "turbo", call_reasoning_effort: 3, extra_body: ["enable_thinking"] },
      }),
    });

    expect(localOwnKeys("u1")).toEqual({ llm: LLM });
  });

  it("go with each text turn and call", () => {
    storage();
    keepOwnKey("u1", "llm", THINKING_LLM);
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });

    expect(turnOwnKeys("u1")).toEqual({ llm: THINKING_LLM });
    expect(callOwnKeys("u1").ownKeys).toEqual({ llm: THINKING_LLM, tts: { api_key: "fish-my-own-key" } });
  });
});

/** A cheaper model of the user's own for writing down their memories, at an effort and with an extra body of its own. */
const MEMORY_LLM = {
  api_key: "sk-my-memory-key-5678",
  base_url: "https://cheap.example.com/v1",
  model: "cheap-model",
  protocol: "chat-completions",
  reasoning_effort: "low",
  extra_body: { thinking: { type: "disabled" } },
} as const;

describe("an own model for memories", () => {
  it("is kept with the own LLM, and absent from what was kept before it could be", () => {
    storage({ "eidolon.ownKeys.u2": JSON.stringify({ llm: LLM }) });
    keepOwnKey("u1", "llm", LLM);
    keepOwnKey("u1", "memory_llm", MEMORY_LLM);

    expect(localOwnKeys("u1")).toEqual({ llm: LLM, memory_llm: MEMORY_LLM });
    expect(localOwnKeys("u2")).toEqual({ llm: LLM });
  });

  it("is left out alone where stored wrongly", () => {
    storage({
      "eidolon.ownKeys.u1": JSON.stringify({ llm: LLM, memory_llm: { ...MEMORY_LLM, protocol: "grpc" } }),
    });

    expect(localOwnKeys("u1")).toEqual({ llm: LLM });
  });

  it("is nothing without the own LLM it goes with, and forgotten with it", () => {
    storage({ "eidolon.ownKeys.u1": JSON.stringify({ memory_llm: MEMORY_LLM }) });
    expect(localOwnKeys("u1")).toEqual({});

    keepOwnKey("u2", "llm", LLM);
    keepOwnKey("u2", "memory_llm", MEMORY_LLM);
    keepOwnKey("u2", "tts", { api_key: "fish-my-own-key" });
    forgetOwnKey("u2", "llm");

    expect(localOwnKeys("u2")).toEqual({ tts: { api_key: "fish-my-own-key" } });
  });

  it("goes with each text turn and call, never named as a service of its own", () => {
    storage();
    keepOwnKey("u1", "llm", THINKING_LLM);
    keepOwnKey("u1", "memory_llm", MEMORY_LLM);
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });

    expect(turnOwnKeys("u1")).toEqual({ llm: THINKING_LLM, memory_llm: MEMORY_LLM });
    expect(callOwnKeys("u1")).toEqual({
      ownKeys: { llm: THINKING_LLM, tts: { api_key: "fish-my-own-key" }, memory_llm: MEMORY_LLM },
      services: ["llm", "tts"],
    });
  });
});

describe("what a text turn brings", () => {
  it("is the user's own LLM alone, leaving their voice keys behind", () => {
    storage();
    keepOwnKey("u1", "llm", LLM);
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });
    keepOwnKey("u1", "stt", STT);

    expect(turnOwnKeys("u1")).toEqual({ llm: LLM });
  });

  it("is nothing when the user keeps no LLM, even with voice keys kept", () => {
    storage();
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });
    keepOwnKey("u1", "stt", STT);

    expect(turnOwnKeys("u1")).toBeUndefined();
  });

  it("is the user's own image generation too, with their LLM or without", () => {
    storage();
    keepOwnKey("u1", "image", IMAGE);
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });
    expect(turnOwnKeys("u1")).toEqual({ image: IMAGE });

    keepOwnKey("u1", "llm", LLM);
    expect(turnOwnKeys("u1")).toEqual({ llm: LLM, image: IMAGE });
  });
});

/** The user's own image generation: a relay in OpenAI's image format. */
const IMAGE = {
  api_format: "openai",
  base_url: "https://my-relay.image.example/v1",
  api_key: "pst-my-own-image-key",
  model: "nai-diffusion-4-5-full",
} as const;

describe("an own image generation", () => {
  it("is kept with the rest, and goes with each call, named as a service", () => {
    storage();
    keepOwnKey("u1", "image", IMAGE);
    keepOwnKey("u1", "stt", STT);

    expect(localOwnKeys("u1")).toEqual({ image: IMAGE, stt: STT });
    expect(callOwnKeys("u1")).toEqual({ ownKeys: { image: IMAGE, stt: STT }, services: ["stt", "image"] });
  });

  it("is left out where stored wrongly", () => {
    storage({
      "eidolon.ownKeys.u1": JSON.stringify({ llm: LLM, image: { ...IMAGE, api_format: "chat" } }),
      "eidolon.ownKeys.u2": JSON.stringify({ image: { ...IMAGE, model: 5 } }),
    });

    expect(localOwnKeys("u1")).toEqual({ llm: LLM });
    expect(localOwnKeys("u2")).toEqual({});
  });

  it("is forgotten alone", () => {
    storage();
    keepOwnKey("u1", "llm", LLM);
    keepOwnKey("u1", "image", IMAGE);
    forgetOwnKey("u1", "image");

    expect(localOwnKeys("u1")).toEqual({ llm: LLM });
  });
});

describe("a browser that cannot keep own keys", () => {
  it("says so when its storage refuses to keep anything, and nothing it was given is brought", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => null,
      setItem: () => {
        throw new DOMException("quota", "QuotaExceededError");
      },
      removeItem: () => {},
    });

    expect(canKeepOwnKeys()).toBe(false);
    keepOwnKey("u1", "llm", LLM);
    expect(localOwnKeys("u1")).toEqual({});
    expect(turnOwnKeys("u1")).toBeUndefined();
  });

  it("says so when its storage cannot even be reached, without throwing", () => {
    const unreachable = () => {
      throw new DOMException("denied", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: unreachable, setItem: unreachable, removeItem: unreachable });

    expect(canKeepOwnKeys()).toBe(false);
    expect(() => keepOwnKey("u1", "llm", LLM)).not.toThrow();
    expect(() => forgetOwnKeys("u1")).not.toThrow();
    expect(localOwnKeys("u1")).toEqual({});
  });

  it("is not one whose storage works, which is left as it was", () => {
    const items = storage({ "eidolon.ownKeys.u1": JSON.stringify({ llm: LLM }) });

    expect(canKeepOwnKeys()).toBe(true);
    expect([...items.keys()]).toEqual(["eidolon.ownKeys.u1"]);
  });
});

describe("what a call brings", () => {
  it("is all the user keeps, with the services named for the check before dialing", () => {
    storage();
    keepOwnKey("u1", "stt", STT);
    keepOwnKey("u1", "tts", { api_key: "fish-my-own-key" });
    keepOwnKey("u1", "llm", LLM);

    expect(callOwnKeys("u1")).toEqual({
      ownKeys: { llm: LLM, tts: { api_key: "fish-my-own-key" }, stt: STT },
      services: ["llm", "tts", "stt"],
    });
  });

  it("is nothing when the user keeps none: no `own_keys`, and no services named", () => {
    storage();
    keepOwnKey("u2", "llm", LLM);

    expect(callOwnKeys("u1")).toEqual({ ownKeys: undefined, services: [] });
  });
});

describe("own keys changing", () => {
  it("is told as this tab keeps or forgets them, and as another tab changes them", () => {
    storage();
    const window = new EventTarget();
    vi.stubGlobal("window", window);
    const told = vi.fn();
    const stop = subscribeOwnKeys(told);

    keepOwnKey("u1", "llm", LLM);
    forgetOwnKey("u1", "llm");
    forgetOwnKeys("u1");
    window.dispatchEvent(Object.assign(new Event("storage"), { key: "eidolon.ownKeys.u1" }));
    window.dispatchEvent(Object.assign(new Event("storage"), { key: "eidolon.listPreferences.u1" }));
    stop();
    keepOwnKey("u1", "llm", LLM);

    expect(told).toHaveBeenCalledTimes(4);
  });
});

it("keeps an independent vision model without a chat key", () => {
  storage();
  keepOwnKey("u1", "vision_llm", LLM);
  expect(localOwnKeys("u1")).toEqual({ vision_llm: LLM });
  expect(turnOwnKeys("u1")).toEqual({ vision_llm: LLM });
  expect(localOwnKeys("u2")).toEqual({});
  forgetOwnKey("u1", "llm");
  expect(turnOwnKeys("u1")).toEqual({ vision_llm: LLM });
  forgetOwnKey("u1", "vision_llm");
  expect(turnOwnKeys("u1")).toBeUndefined();
});
