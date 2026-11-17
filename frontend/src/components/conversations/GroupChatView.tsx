"use client";

import { ArrowLeftIcon, AtSignIcon, SendIcon, Trash2Icon, XIcon } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import {
  AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyDescription, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Message, MessageContent } from "@/components/ui/message";
import {
  MessageScroller, MessageScrollerButton, MessageScrollerContent, MessageScrollerItem,
  MessageScrollerProvider, MessageScrollerViewport,
} from "@/components/ui/message-scroller";
import { Popover, PopoverContent, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Spinner } from "@/components/ui/spinner";
import { Textarea } from "@/components/ui/textarea";
import { copy } from "@/i18n";
import { ApiError, getPhoto, type Character, type Photo, type PhotoStatus } from "@/lib/api";
import {
  applyGroupEvent, deleteGroup, groupMentions, newGroupRequestId, readGroup, sendGroupTurn,
  type GroupLog, type GroupMessage, type GroupSummary,
} from "@/lib/groupChat";

import { useDirectory, useWide } from "./ConversationDirectory";
import { GroupAvatars, useGroupDirectory } from "./GroupChatDirectory";
import { PhotoBlock } from "./PhotoBlock";

const t = copy.groupChat;


function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

export function GroupChatView({ id, userId }: { id: string; userId: string }) {
  const { characters, go } = useDirectory();
  const { refresh } = useGroupDirectory();
  const wide = useWide();
  const [group, setGroup] = useState<GroupSummary | null>(null);
  const [log, setLog] = useState<GroupLog>({ turns: [], draft: null });
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [text, setText] = useState("");
  const [mentions, setMentions] = useState<string[]>([]);
  const [quoted, setQuoted] = useState<GroupMessage | null>(null);
  const [sending, setSending] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const active = useRef<AbortController | null>(null);
  const pending = useRef<{ text: string; mentions: string[]; quote: string | null; request: string } | null>(null);
  const running = log.turns.at(-1)?.status === "running";
  const byId = new Map(characters?.map((c) => [c.id, c]));

  useEffect(() => {
    const controller = new AbortController();
    void readGroup(id, controller.signal).then(
      ({ turns, ...summary }) => {
        if (controller.signal.aborted) return;
        setGroup(summary);
        setLog({ turns, draft: null });
      },
      (err) => {
        if (!controller.signal.aborted) setError(errorMessage(err, t.loadFailed));
      },
    );
    return () => controller.abort();
  }, [id, reload]);

  // A refresh can land while a different request still holds the stream.
  useEffect(() => {
    if (sending || !running) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const { turns } = await readGroup(id, controller.signal);
        if (!controller.signal.aborted) {
          setLog({ turns, draft: null });
          if (turns.at(-1)?.status === "running") timer = setTimeout(poll, 1500);
          else refresh();
        }
      } catch {
        if (!controller.signal.aborted) timer = setTimeout(poll, 3000);
      }
    };
    timer = setTimeout(poll, 1000);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, sending, running, refresh]);

  useEffect(() => () => active.current?.abort(), []);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = text.trim();
    if (!value || active.current || running || deleting) return;
    const controller = new AbortController();
    active.current = controller;
    let completed = false;
    let accepted = false;
    let request = pending.current;
    if (!request || request.text !== value || request.mentions !== mentions || request.quote !== (quoted?.id ?? null)) {
      request = { text: value, mentions, quote: quoted?.id ?? null, request: newGroupRequestId() };
      pending.current = request;
    }
    setSending(true);
    setError(null);
    try {
      for await (const event of sendGroupTurn(id, userId, value, mentions, request.request, controller.signal, request.quote)) {
        if (event.type === "message" && event.message.character_id === null && !accepted) {
          accepted = true;
          pending.current = null;
          setText("");
          setMentions([]);
          setQuoted(null);
        }
        if (event.type === "done") {
          completed = true;
          if (event.detail) setError(event.detail);
        }
        setLog((old) => applyGroupEvent(old, event, mentions));
      }
      if (!completed) setError(t.interrupted);
    } catch (err) {
      if (!controller.signal.aborted) setError(errorMessage(err, t.interrupted));
    } finally {
      if (!controller.signal.aborted) {
        if (!completed) {
          setLog((old) => ({ ...old, draft: null }));
          try {
            const { turns } = await readGroup(id, controller.signal);
            if (!controller.signal.aborted) setLog({ turns, draft: null });
          } catch {
            // The local draft stays available for the same request ID on retry.
          }
        }
        if (!controller.signal.aborted) {
          setSending(false);
          refresh();
        }
      }
      active.current = null;
    }
  }

  async function remove() {
    setDeleting(true);
    try {
      await deleteGroup(id);
      setDeleteOpen(false);
      refresh();
      go("/conversations");
    } catch (err) {
      setError(errorMessage(err, t.deleteFailed));
      setDeleteOpen(false);
    } finally {
      setDeleting(false);
    }
  }

  if (!group) {
    if (!error) {
      return <div className="flex h-full items-center justify-center"><Spinner aria-label={t.loading} /></div>;
    }
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyTitle>{t.loadFailed}</EmptyTitle>
          <EmptyDescription>{error}</EmptyDescription>
        </EmptyHeader>
        <EmptyContent>
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => go("/conversations")}>{copy.conversation.backToList}</Button>
            <Button onClick={() => { setError(null); setReload((n) => n + 1); }}>{copy.conversationList.retry}</Button>
          </div>
        </EmptyContent>
      </Empty>
    );
  }

  const busy = sending || running || deleting;
  const memberName = (id: string) => byId.get(id)?.name ?? id;
  const messagesById = new Map(log.turns.flatMap((turn) => turn.messages.map((message) => [message.id, message] as const)));

  return (
    <section className="flex h-full min-h-0 flex-col bg-background">
      <header className="flex min-h-14 shrink-0 items-center gap-2 border-b border-border px-3">
        {!wide && (
          <Button variant="ghost" size="icon" type="button" aria-label={copy.conversation.backToList}
            onClick={() => go("/conversations")}>
            <ArrowLeftIcon />
          </Button>
        )}
        <GroupAvatars memberIds={group.member_ids} characters={byId} />
        <h1 className="min-w-0 flex-1 truncate text-header">{group.name}</h1>
        <AlertDialog open={deleteOpen} onOpenChange={(open) => { if (!deleting) setDeleteOpen(open); }}>
          <AlertDialogTrigger render={<Button size="icon" variant="ghost" aria-label={t.delete} disabled={busy} />}>
            <Trash2Icon />
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t.deleteQuestion(group.name)}</AlertDialogTitle>
              <AlertDialogDescription>{t.deleteDetail}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={deleting}>{t.cancel}</AlertDialogCancel>
              <Button variant="destructive" disabled={deleting} onClick={() => void remove()}>
                {deleting && <Spinner data-icon="inline-start" />}{t.delete}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </header>

      <div className="min-h-0 flex-1">
        <MessageScrollerProvider autoScroll defaultScrollPosition="end">
          <MessageScroller>
            <MessageScrollerViewport>
              <MessageScrollerContent className="gap-3 px-3 py-4">
                {log.turns.length === 0 && (
                  <MessageScrollerItem>
                    <p className="p-4 text-center text-sm text-muted-foreground">{t.start}</p>
                  </MessageScrollerItem>
                )}
                {log.turns.flatMap((turn) => [
                  ...turn.messages.map((message) =>
                    <GroupBubble key={message.id} message={message} character={byId.get(message.character_id ?? "")}
                      mentions={message.character_id === null ? groupMentions(turn).map(memberName) : []}
                      quoted={message.reply_to ? messagesById.get(message.reply_to) : undefined}
                      quoteName={message.reply_to ? memberName(messagesById.get(message.reply_to)?.character_id ?? "") : ""}
                      onQuote={!busy && message.character_id ? () => setQuoted(message) : undefined} />),
                  ...(log.draft?.turnId === turn.id
                    ? [<GroupBubble key={log.draft.message.id} message={log.draft.message}
                        character={byId.get(log.draft.message.character_id ?? "")} mentions={[]} />]
                    : []),
                  ...(turn.status === "failed" || turn.status === "interrupted"
                    ? [<MessageScrollerItem key={`status-${turn.id}`}>
                        <p className="text-center text-meta text-muted-foreground">{t.interrupted}</p>
                        {turn.messages[0]?.reply_to && <div className="flex justify-center">
                          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => {
                            setText(turn.messages[0].text ?? "");
                            setQuoted(messagesById.get(turn.messages[0].reply_to ?? "") ?? null);
                            setMentions(groupMentions(turn));
                          }}>重试引用追问</Button>
                        </div>}
                      </MessageScrollerItem>]
                    : []),
                ])}
                {(sending || running) && (
                  <MessageScrollerItem>
                    <div className="flex items-center gap-2 text-meta text-muted-foreground" role="status">
                      <Spinner />{t.replying}
                    </div>
                  </MessageScrollerItem>
                )}
              </MessageScrollerContent>
            </MessageScrollerViewport>
            <MessageScrollerButton aria-label={t.scrollLatest} />
          </MessageScroller>
        </MessageScrollerProvider>
      </div>

      <form onSubmit={(event) => void send(event)} className="flex shrink-0 flex-col gap-2 border-t border-border px-3 py-2">
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {quoted && <div className="flex items-center gap-2 text-meta text-muted-foreground">
          <span className="min-w-0 flex-1 truncate">引用 {memberName(quoted.character_id ?? "")}：{quoted.segments.map((s) => s.zh || s.ja).join(" ")}</span>
          <Button type="button" variant="ghost" size="icon" aria-label="取消引用" onClick={() => setQuoted(null)}><XIcon /></Button>
        </div>}
        {mentions.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {mentions.map((member) => (
              <Button key={member} type="button" variant="secondary" size="sm" disabled={busy}
                aria-label={t.removeMention(memberName(member))}
                onClick={() => setMentions((previous) => previous.filter((id) => id !== member))}>
                @{memberName(member)} <XIcon data-icon="inline-end" />
              </Button>
            ))}
          </div>
        )}
        <div className="flex items-center gap-2">
          <Popover>
            <PopoverTrigger render={<Button type="button" variant="outline" size="icon-composer" disabled={busy}
              aria-label={t.mention} />}>
              <AtSignIcon />
            </PopoverTrigger>
            <PopoverContent side="top" align="start">
              <PopoverTitle>{t.mention}</PopoverTitle>
              {group.member_ids.map((member) => (
                <label key={member} className="flex min-h-11 items-center gap-3 px-2">
                  <Checkbox checked={mentions.includes(member)} disabled={busy}
                    onCheckedChange={(checked) => setMentions((previous) => checked
                      ? [...previous.filter((id) => id !== member), member]
                      : previous.filter((id) => id !== member))} />
                  {memberName(member)}
                </label>
              ))}
            </PopoverContent>
          </Popover>
          <Textarea aria-label={t.message} placeholder={t.message} rows={1} maxLength={2000}
            className="min-h-11 min-w-0 flex-1" value={text} disabled={busy}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                event.currentTarget.form?.requestSubmit();
              }
            }} />
          <Button type="submit" size="icon-composer" aria-label={t.send} disabled={!text.trim() || busy}><SendIcon /></Button>
        </div>
      </form>
    </section>
  );
}

function GroupPhotoAttachment({ photo }: { photo: Photo }) {
  const [outcome, setOutcome] = useState<PhotoStatus | null>(null);
  const pending = photo.status === "pending" && outcome === null;
  useEffect(() => {
    if (!pending) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      try {
        const latest = await getPhoto(photo.id);
        if (stopped) return;
        if (latest.status === "pending") timer = setTimeout(check, 2000);
        else setOutcome(latest);
      } catch (err) {
        if (stopped) return;
        if (err instanceof ApiError && err.status === 404) {
          setOutcome({ status: "failed", reason: "service_error" });
        } else timer = setTimeout(check, 3000);
      }
    };
    timer = setTimeout(check, 1000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [photo.id, pending]);
  return <PhotoBlock photo={photo.status === "pending" && outcome ? { ...photo, ...outcome } : photo} />;
}

function GroupBubble({ message, character, mentions, quoted, quoteName, onQuote }: {
  message: GroupMessage; character: Character | undefined; mentions: string[];
  quoted?: GroupMessage; quoteName?: string; onQuote?: () => void;
}) {
  const producer = message.character_id === null;
  const name = character?.name ?? message.character_id ?? t.producer;
  return (
    <MessageScrollerItem messageId={message.id}>
      <Message align={producer ? "end" : "start"}>
        <MessageContent>
          {!producer && (
            <div className="flex min-w-0 items-center gap-2">
              <MarkedAvatar size="bubble" name={name} src={character?.avatar_url} />
              <span className="truncate text-meta font-medium text-muted-foreground">{name}</span>
              {onQuote && <Button type="button" variant="ghost" size="sm" onClick={onQuote}>引用</Button>}
            </div>
          )}
          {message.photo && <GroupPhotoAttachment key={message.photo.id} photo={message.photo} />}
          <Bubble variant={producer ? "producer" : "her"} align={producer ? "end" : "start"}>
            <BubbleContent>
              {quoted && <p className="text-meta text-muted-foreground">引用 {quoteName}：{quoted.segments.map((s) => s.zh || s.ja).join(" ")}</p>}
              {producer ? (
                <p className="whitespace-pre-wrap text-body">{mentions.length ? mentions.map((name) => "@" + name).join(" ") + " " : ""}{message.text}</p>
              ) : message.segments.map((segment, index) => (
                <div key={index} className="flex flex-col gap-1">
                  <p lang="ja" className="whitespace-pre-wrap text-body">{segment.ja}</p>
                  {segment.zh && <p className="whitespace-pre-wrap text-preview text-muted-foreground">{segment.zh}</p>}
                </div>
              ))}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
    </MessageScrollerItem>
  );
}
