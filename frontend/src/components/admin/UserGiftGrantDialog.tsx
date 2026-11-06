"use client";

import { useId, useReducer, useRef, useState, type FormEvent } from "react";
import { FailureNotice } from "@/components/app/FailureNotice";
import { GiftTile } from "@/components/gifts/GiftTile";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Field, FieldError, FieldGroup, FieldLabel, FieldDescription, FieldSet, FieldLegend } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import { emptyGrant, grantFlow, initialGrantFlow, grantInput, grantProblems, grantUserGifts, resourceSummary, type GrantResult } from "@/lib/adminUserGifts";
import { toFailure, type Failure } from "@/lib/failure";
import type { GiftInfo } from "@/lib/gifts";

const t = copy.admin.userGifts;

export function UserGiftGrantDialog({ user, catalog, open, onClose, onSaved }: {
  user: { id: string; name: string }; catalog: GiftInfo[]; open: boolean;
  onClose: () => void; onSaved: (result: GrantResult) => void;
}) {
  const prefix = useId();
  const [draft, setDraft] = useState(emptyGrant);
  const [search, setSearch] = useState("");
  const [checked, setChecked] = useState(false);
  const [flow, dispatch] = useReducer(grantFlow, initialGrantFlow);
  const [failure, setFailure] = useState<Failure | null>(null);
  const submitting = useRef(false);
  const busy = flow.stage === "submitting";
  const editing = flow.stage === "edit";
  const errors = grantProblems(draft, catalog);
  const available = catalog.filter((gift) => !gift.retired && !draft.gifts.some((g) => g.gift_id === gift.id)
    && gift.name.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));

  function review(event: FormEvent) {
    event.preventDefault();
    setChecked(true);
    if (Object.keys(errors).length) return;
    setFailure(null);
    dispatch({ type: "review", request: grantInput(draft, crypto.randomUUID()) });
  }

  async function submit() {
    if (!flow.request || submitting.current) return;
    submitting.current = true;
    dispatch({ type: "submit" });
    setFailure(null);
    try {
      const result = await grantUserGifts(user.id, flow.request);
      dispatch({ type: "saved" });
      setDraft(emptyGrant());
      setSearch("");
      setChecked(false);
      onSaved(result);
    } catch (error) {
      dispatch({ type: "failed", error });
      setFailure(toFailure(error));
    } finally {
      submitting.current = false;
    }
  }

  return <Dialog open={open} onOpenChange={(next) => { if (!next && !submitting.current) onClose(); }}>
    <DialogContent className="max-h-[90dvh] grid-rows-[auto_minmax(0,1fr)_auto] sm:max-w-2xl" showCloseButton={!busy}>
      <DialogHeader>
        <DialogTitle>{editing ? t.issue : t.confirmTitle}</DialogTitle>
        <DialogDescription>{t.recipient}：{user.name}<br /><span className="break-all">{user.id}</span></DialogDescription>
      </DialogHeader>
      <div className="min-h-0 overflow-y-auto">
        {failure && <FailureNotice failure={failure} />}
        {flow.stage === "uncertain" && <p role="status" className="text-sm text-muted-foreground">{t.uncertain}</p>}
        {editing ? <form id={`${prefix}-form`} onSubmit={review} noValidate>
          <FieldGroup>
            <FieldSet>
              <FieldLegend>{t.gifts}</FieldLegend>
              {draft.gifts.map((held) => {
                const gift = catalog.find((g) => g.id === held.gift_id);
                const invalid = checked && !!errors[held.gift_id];
                return <Field key={held.gift_id} data-invalid={invalid || undefined}>
                  <div className="flex flex-wrap items-center gap-3">
                    <GiftTile gift={gift} size="small" />
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <FieldLabel htmlFor={`${prefix}-${held.gift_id}`}>{gift?.name ?? held.gift_id}</FieldLabel>
                      {gift && <span className="text-xs text-muted-foreground">{copy.gifts.rarity[gift.rarity]}</span>}
                    </div>
                    <Input id={`${prefix}-${held.gift_id}`} className="w-20" type="number" inputMode="numeric" min={1} max={999} step={1}
                      value={held.count} aria-invalid={invalid || undefined} aria-describedby={invalid ? `${prefix}-${held.gift_id}-error` : undefined}
                      onChange={(event) => setDraft({ ...draft, gifts: draft.gifts.map((g) => g.gift_id === held.gift_id ? { ...g, count: event.target.value } : g) })} />
                    <Button type="button" variant="ghost" aria-label={copy.admin.actionOn(t.remove, gift?.name ?? held.gift_id)}
                      onClick={() => setDraft({ ...draft, gifts: draft.gifts.filter((g) => g.gift_id !== held.gift_id) })}>{t.remove}</Button>
                  </div>
                  {invalid && <FieldError id={`${prefix}-${held.gift_id}-error`}>{errors[held.gift_id]}</FieldError>}
                </Field>;
              })}
              <Field><FieldLabel htmlFor={`${prefix}-search`}>{t.search}</FieldLabel>
                <Input id={`${prefix}-search`} value={search} onChange={(event) => setSearch(event.target.value)} />
              </Field>
              <ul className="grid max-h-56 gap-2 overflow-y-auto sm:grid-cols-2">
                {available.map((gift) => <li key={gift.id} className="flex items-center gap-2">
                  <GiftTile gift={gift} size="small" />
                  <div className="min-w-0 flex-1"><p className="text-sm">{gift.name}</p><p className="text-xs text-muted-foreground">{copy.gifts.rarity[gift.rarity]}</p></div>
                  <Button type="button" variant="outline" size="sm" aria-label={copy.admin.actionOn(t.add, gift.name)}
                    onClick={() => setDraft({ ...draft, gifts: [...draft.gifts, { gift_id: gift.id, count: "1" }] })}>{t.add}</Button>
                </li>)}
              </ul>
              {!available.length && <p className="text-sm text-muted-foreground">{t.noMatches}</p>}
            </FieldSet>
            <FieldGroup className="grid gap-4 sm:grid-cols-2">
              {(["reset_cards", "draws"] as const).map((key) => {
                const invalid = checked && !!errors[key];
                return <Field key={key} data-invalid={invalid || undefined}>
                  <FieldLabel htmlFor={`${prefix}-${key}`}>{key === "draws" ? t.addDraws : t.addCards}</FieldLabel>
                  <Input id={`${prefix}-${key}`} type="number" inputMode="numeric" min={0} max={999} step={1} value={draft[key]}
                    aria-invalid={invalid || undefined} aria-describedby={invalid ? `${prefix}-${key}-error` : undefined}
                    onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} />
                  {invalid && <FieldError id={`${prefix}-${key}-error`}>{errors[key]}</FieldError>}
                </Field>;
              })}
            </FieldGroup>
            <Field data-invalid={checked && !!errors.note || undefined}>
              <FieldLabel htmlFor={`${prefix}-note`}>{t.note}</FieldLabel>
              <Textarea id={`${prefix}-note`} maxLength={500} value={draft.note} onChange={(event) => setDraft({ ...draft, note: event.target.value })} />
              <FieldDescription>{t.noteHint}</FieldDescription>
            </Field>
            {checked && errors.empty && <FieldError role="alert">{errors.empty}</FieldError>}
          </FieldGroup>
        </form> : flow.request && <div className="flex flex-col gap-4">
          <p className="text-sm">{resourceSummary(flow.request, catalog)}</p>
          <p className="whitespace-pre-wrap wrap-anywhere text-sm text-muted-foreground">{flow.request.note || t.noNote}</p>
          <p className="text-sm text-muted-foreground">{t.direct}</p>
        </div>}
      </div>
      <DialogFooter>
        <Button variant="ghost" disabled={busy} onClick={onClose}>{t.cancel}</Button>
        {editing ? <Button type="submit" form={`${prefix}-form`}>{t.next}</Button> : <>
          <Button variant="outline" disabled={busy || flow.stage === "uncertain"} onClick={() => { dispatch({ type: "edit" }); setFailure(null); }}>{t.back}</Button>
          <Button disabled={busy} onClick={submit}>{busy && <Spinner data-icon="inline-start" />}{busy ? t.submitting : flow.stage === "uncertain" ? t.retry : t.confirm}</Button>
        </>}
      </DialogFooter>
    </DialogContent>
  </Dialog>;
}
