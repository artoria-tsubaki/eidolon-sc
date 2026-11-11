import { describe, expect, it } from "vitest";
import { contentSecurityPolicy } from "@/lib/contentSecurityPolicy";

/** The policy's directives, by name. */
function directives(policy: string): Map<string, string[]> {
  return new Map(
    policy.split(";").map((directive) => {
      const [name, ...sources] = directive.trim().split(/\s+/);
      return [name, sources];
    }),
  );
}

describe("contentSecurityPolicy", () => {
  it("runs only this site's scripts and talks only to this site", () => {
    const policy = directives(contentSecurityPolicy(undefined));

    expect(policy.get("default-src")).toEqual(["'self'"]);
    expect(policy.get("script-src")).toEqual(["'self'", "'unsafe-inline'"]);
    expect(policy.get("connect-src")).toEqual(["'self'"]);
    expect(policy.get("object-src")).toEqual(["'none'"]);
    expect(policy.get("base-uri")).toEqual(["'self'"]);
    expect(policy.get("form-action")).toEqual(["'self'"]);
    expect(policy.get("frame-ancestors")).toEqual(["'none'"]);
  });

  it("also loads the stands, their thumbnails and the greetings from where they are kept", () => {
    const policy = directives(contentSecurityPolicy("https://stand.example.com/bucket"));

    expect(policy.get("connect-src")).toEqual(["'self'", "https://stand.example.com"]);
    expect(policy.get("img-src")).toContain("https://stand.example.com");
    expect(policy.get("media-src")).toContain("https://stand.example.com");
    expect(policy.get("script-src")).toEqual(["'self'", "'unsafe-inline'"]);
  });

  it("allows eval only while React is running in development", () => {
    const policy = directives(contentSecurityPolicy(undefined, true));

    expect(policy.get("script-src")).toEqual(["'self'", "'unsafe-inline'", "'unsafe-eval'"]);
    expect(directives(contentSecurityPolicy("https://stand.example.com", true)).get("script-src")).toEqual([
      "'self'",
      "'unsafe-inline'",
      "'unsafe-eval'",
    ]);
  });

  it("allows the data: and blob: images the share card is drawn through, and the blob: audio her words are read aloud from", () => {
    const policy = directives(contentSecurityPolicy(undefined));

    expect(policy.get("img-src")).toEqual(["'self'", "data:", "blob:"]);
    expect(policy.get("media-src")).toEqual(["'self'", "blob:"]);
  });

  it.each(["not a url", "javascript:alert(1)"])(
    "leaves out a stand address that is not one (%j)",
    (url) => {
      expect(contentSecurityPolicy(url)).toBe(contentSecurityPolicy(undefined));
    },
  );
});
