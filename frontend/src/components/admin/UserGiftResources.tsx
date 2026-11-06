"use client";

import { useEffect, useState } from "react";
import { FailureNotice } from "@/components/app/FailureNotice";
import { Notice } from "@/components/app/admin/Notice";
import { Timestamp } from "@/components/app/admin/Timestamp";
import { GiftTile } from "@/components/gifts/GiftTile";
import { Button } from "@/components/ui/button";
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Spinner } from "@/components/ui/spinner";
import { Separator } from "@/components/ui/separator";
import { copy } from "@/i18n";
import { getUserGifts, getUserGrants, resourceSummary, type UserGifts, type GrantHistory, type GrantResult } from "@/lib/adminUserGifts";
import { toFailure, type Failure } from "@/lib/failure";
import { UserGiftGrantDialog } from "./UserGiftGrantDialog";

const t = copy.admin.userGifts;

export function UserGiftResources({ id, name }: { id: string; name: string }) {
  const [resources, setResources] = useState<UserGifts | null>(null);
  const [history, setHistory] = useState<GrantHistory | null>(null);
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [open, setOpen] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    setLoading(true);
    setHistory(null);
    setFailure(null);
    Promise.all([getUserGifts(id), getUserGrants(id, offset)]).then(([gifts, grants]) => {
      if (!current) return;
      setResources(gifts);
      setHistory(grants);
    }, (error) => { if (current) setFailure(toFailure(error)); }).finally(() => {
      if (current) setLoading(false);
    });
    return () => { current = false; };
  }, [id, offset, revision]);

  function saved(result: GrantResult) {
    setResources((old) => old && { ...old, ...result.resources });
    setSuccess(resourceSummary(result.grant, resources?.catalog ?? []));
    setOpen(false);
    setOffset(0);
    setRevision((n) => n + 1);
  }

  return <Card>
    <CardHeader>
      <CardTitle>{t.title}</CardTitle>
      <CardDescription>{t.description}</CardDescription>
      <CardAction><Button variant="outline" disabled={!resources?.open || loading} onClick={() => setOpen(true)}>{t.issue}</Button></CardAction>
    </CardHeader>
    <CardContent className="flex flex-col gap-4">
      {success && <Notice text={t.success} onDismiss={() => setSuccess(null)}>{success}</Notice>}
      {failure && <><FailureNotice failure={failure} /><Button variant="outline" onClick={() => setRevision((n) => n + 1)}>{t.retry}</Button></>}
      {loading && <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner />{t.loading}</p>}
      {resources && <>
        {!resources.open && <p role="status" className="text-sm text-muted-foreground">{t.closed}</p>}
        <dl className="grid grid-cols-2 gap-4">
          <div><dt className="text-sm text-muted-foreground">{t.draws}</dt><dd className="tabular-nums">{resources.draws}</dd></div>
          <div><dt className="text-sm text-muted-foreground">{t.cards}</dt><dd className="tabular-nums">{resources.reset_cards}</dd></div>
        </dl>
        <p className="text-sm text-muted-foreground">{t.accrual}</p>
        {resources.backpack.length ? <ul className="grid gap-3 sm:grid-cols-2">
          {resources.backpack.map((held) => {
            const gift = resources.catalog.find((g) => g.id === held.gift_id);
            return <li key={held.gift_id} className="flex items-center gap-3">
              <GiftTile gift={gift} size="small" />
              <span>{t.giftCount(gift?.name ?? held.gift_id, held.count)}</span>
            </li>;
          })}
        </ul> : <p className="text-sm text-muted-foreground">{t.empty}</p>}
        <Separator />
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-medium">{t.history}</h3>
          <Button variant="ghost" size="sm" disabled={loading} onClick={() => setRevision((n) => n + 1)}>{t.refresh}</Button>
        </div>
        {history && <>
          {history.grants.length ? <ul className="flex flex-col gap-4">
            {history.grants.map((grant) => <li key={grant.request_id} className="flex flex-col gap-1">
              <div className="flex flex-wrap gap-x-3 text-sm text-muted-foreground">
                <Timestamp value={grant.granted_at} /><span>{grant.actor?.name ?? t.deletedActor}</span>
              </div>
              <p className="text-sm">{resourceSummary(grant, resources.catalog)}</p>
              <p className="whitespace-pre-wrap wrap-anywhere text-sm text-muted-foreground">{grant.note || t.noNote}</p>
            </li>)}
          </ul> : <p className="text-sm text-muted-foreground">{t.noHistory}</p>}
          {history.total > 20 && <div className="flex flex-wrap items-center justify-between gap-2">
            <Button variant="outline" disabled={offset === 0} onClick={() => setOffset((n) => Math.max(0, n - 20))}>{t.previous}</Button>
            <span className="text-sm text-muted-foreground">{t.page(offset / 20 + 1, history.total)}</span>
            <Button variant="outline" disabled={offset + 20 >= history.total} onClick={() => setOffset((n) => n + 20)}>{t.nextPage}</Button>
          </div>}
        </>}
        <UserGiftGrantDialog user={{ id, name }} catalog={resources.catalog} open={open} onClose={() => setOpen(false)} onSaved={saved} />
      </>}
    </CardContent>
  </Card>;
}
