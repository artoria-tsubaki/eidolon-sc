"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import { matchesSearch } from "@/lib/characterSearch";
import { unitsOf } from "@/lib/conversationList";
import { createGroup, groupChatPage, listGroups, type GroupSummary } from "@/lib/groupChat";
import { cn } from "@/lib/utils";

import { useDirectory } from "./ConversationDirectory";

const t = copy.groupChat;

type GroupDirectory = { groups: GroupSummary[]; failed: boolean; refresh: () => void };
const GroupContext = createContext<GroupDirectory>({ groups: [], failed: false, refresh: () => {} });

export function useGroupDirectory(): GroupDirectory {
  return useContext(GroupContext);
}

export function GroupChatDirectory({ children }: { children: ReactNode }) {
  const userId = useDirectory().account?.id;
  const [snapshot, setSnapshot] = useState<{ userId: string; groups: GroupSummary[]; failed: boolean }>();
  const pending = useRef<AbortController | null>(null);
  const refresh = useCallback(() => {
    if (!userId) return;
    pending.current?.abort();
    const request = new AbortController();
    pending.current = request;
    void listGroups(request.signal).then(
      (groups) => {
        if (!request.signal.aborted) setSnapshot({ userId, groups, failed: false });
      },
      () => {
        if (!request.signal.aborted) setSnapshot((previous) => ({
          userId, groups: previous?.userId === userId ? previous.groups : [], failed: true,
        }));
      },
    );
  }, [userId]);

  useEffect(() => {
    refresh();
    window.addEventListener("focus", refresh);
    return () => {
      pending.current?.abort();
      window.removeEventListener("focus", refresh);
    };
  }, [refresh]);

  return (
    <GroupContext.Provider value={{
      groups: snapshot?.userId === userId ? snapshot?.groups ?? [] : [],
      failed: snapshot?.userId === userId && snapshot?.failed === true,
      refresh,
    }}>
      {children}
    </GroupContext.Provider>
  );
}

export function GroupChatRows({ query, selected }: { query: string; selected: string | null }) {
  const { characters, go } = useDirectory();
  const { groups, failed, refresh } = useGroupDirectory();
  const byId = new Map(characters?.map((c) => [c.id, c]));
  const shown = groups.filter((group) => !query.trim() || group.name.includes(query.trim()) ||
    group.member_ids.some((id) => {
      const character = byId.get(id);
      return character && matchesSearch(character, query);
    }));
  if (!shown.length && !failed) return null;
  return (
    <section aria-label={t.title}>
      <h2 className="px-4 pt-2 pb-1 text-meta text-muted-foreground">{t.title}</h2>
      {failed && <Button className="mx-4" size="sm" variant="outline" onClick={refresh}>{t.retryList}</Button>}
      <ul>
        {shown.map((group) => {
          const message = group.last_message;
          const speaker = message?.character_id && byId.get(message.character_id);
          const preview = message?.text ?? message?.segments.map((line) => line.zh || line.ja).join(" ");
          return (
            <li key={group.id}>
              <button
                type="button"
                aria-current={selected === group.id ? "page" : undefined}
                className={cn("flex w-full items-center gap-3 px-4 py-2.5 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset",
                  selected === group.id && "bg-muted")}
                onClick={() => go(groupChatPage(group.id))}
              >
                <GroupAvatars memberIds={group.member_ids} characters={byId} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body font-medium">{group.name}</span>
                  <span className="block truncate text-preview text-muted-foreground">
                    {preview ? (speaker ? (speaker.short_name ?? speaker.name) + "： " : "") + preview : t.empty}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function GroupAvatars({ memberIds, characters }: { memberIds: string[]; characters: Map<string, Character> }) {
  return (
    <div className="flex shrink-0 items-center gap-0.5">
      {memberIds.map((id) => {
        const character = characters.get(id);
        return <MarkedAvatar key={id} size="sm" name={character?.name ?? id} src={character?.avatar_url} />;
      })}
    </div>
  );
}

export function NewGroupChat() {
  const { account, characters, go } = useDirectory();
  const { refresh } = useGroupDirectory();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idols = characters?.filter((c) => c.can_join_group === true) ?? [];
  const units = unitsOf(idols.filter((c) => matchesSearch(c, query)));

  async function create() {
    if (busy || selected.length < 2 || selected.length > 3) return;
    setBusy(true);
    setError(null);
    try {
      const group = await createGroup(selected);
      setOpen(false);
      refresh();
      go(groupChatPage(group.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : t.createFailed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(next) => {
      if (busy) return;
      setOpen(next);
      if (next) {
        setSelected([]);
        setQuery("");
        setError(null);
      }
    }}>
      <DialogTrigger render={<Button variant="outline" size="sm" disabled={!account || idols.length < 2} />}>
        {t.create}
      </DialogTrigger>
      <DialogContent className="flex max-h-[80dvh] min-h-0 flex-col">
        <DialogHeader>
          <DialogTitle>{t.create}</DialogTitle>
          <DialogDescription>{t.pickHint}</DialogDescription>
        </DialogHeader>
        <Input type="search" aria-label={copy.conversationList.search} placeholder={copy.conversationList.search}
          value={query} onChange={(event) => setQuery(event.target.value)} />
        <p className="text-meta text-muted-foreground" aria-live="polite">
          {selected.length ? t.selected(selected.map((id) => {
            const c = idols.find((idol) => idol.id === id);
            return c?.short_name ?? c?.name ?? id;
          }).join("、")) : t.pickNone}
        </p>
        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          {units.length === 0 && <p className="text-sm text-muted-foreground">{copy.conversationList.noMatch}</p>}
          {units.map((unit) => (
            <fieldset key={unit.unit ?? ""} className="flex flex-col gap-1">
              <legend className="text-meta text-muted-foreground">{unit.unit ?? t.otherIdols}</legend>
              {unit.members.map((c) => {
                const checked = selected.includes(c.id);
                return (
                  <label key={c.id} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 hover:bg-muted/60">
                    <Checkbox checked={checked} disabled={busy || (!checked && selected.length >= 3)}
                      onCheckedChange={(next) => setSelected((previous) => next
                        ? [...previous, c.id] : previous.filter((id) => id !== c.id))} />
                    <MarkedAvatar size="sm" name={c.name} src={c.avatar_url} />
                    <span className="truncate text-body">{c.name}</span>
                  </label>
                );
              })}
            </fieldset>
          ))}
        </div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button onClick={() => void create()} disabled={busy || selected.length < 2}>
            {busy && <Spinner data-icon="inline-start" />}
            {t.createConfirm}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
