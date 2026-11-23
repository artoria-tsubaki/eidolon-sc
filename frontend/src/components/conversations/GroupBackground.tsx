import { useId, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Combobox, ComboboxContent, ComboboxInput, ComboboxItem, ComboboxList, ComboboxSelectTrigger } from "@/components/ui/combobox";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import { Field, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { copy } from "@/i18n";
import { presetTime, type GroupBackground, type MemberSetting } from "@/lib/groupChat";
import { listTimezones } from "@/lib/timezone";

import { useProducerTimezone } from "./useProducerTimezone";

const t = copy.groupChat.background;

function Choices({ label, options, value, select }: {
  label: string; options: readonly string[]; value: string; select: (value: string) => void;
}) {
  return <ToggleGroup aria-label={label} variant="outline" size="sm" className="flex-wrap"
    value={options.includes(value) ? [value] : []}
    onValueChange={(values) => select(values[0] ?? "")}>
    {options.map((option) => <ToggleGroupItem key={option} value={option}>{option}</ToggleGroupItem>)}
  </ToggleGroup>;
}

function Place({ label, value, change }: { label: string; value: string; change: (place: string) => void }) {
  const id = useId();
  return <Field>
    <FieldLabel htmlFor={id}>{label}</FieldLabel>
    <Choices label={label} options={t.places} value={value} select={change} />
    <Input id={id} value={value} maxLength={60} onChange={(e) => change(e.target.value)} />
  </Field>;
}

export function BackgroundEditor({ value, members, onChange, disabled }: {
  value: GroupBackground; members: { id: string; name: string }[];
  onChange: (value: GroupBackground) => void; disabled: boolean;
}) {
  const id = useId();
  const defaultZone = useProducerTimezone();
  const zone = value.time?.timezone ?? defaultZone;
  const zones = useMemo(() => Array.from(new Set([zone, ...listTimezones()])), [zone]);
  const [separate, setSeparate] = useState(Object.keys(value.members).length > 0);
  const hours = [9, 18, 21];
  const currentPreset = hours.findIndex((hour) => value.time?.local === presetTime(hour, zone));
  function memberChange(key: string, setting: MemberSetting) {
    onChange({ ...value, members: { ...value.members, [key]: setting } });
  }
  return (
    <fieldset disabled={disabled}>
      <FieldGroup>
        <Field>
          <FieldLabel htmlFor={id + "-time"}>{t.time}</FieldLabel>
          <Choices label={t.time} options={[t.now, ...t.hours]}
            value={value.time ? t.hours[currentPreset] ?? "" : t.now}
            select={(choice) => {
              const index = t.hours.findIndex((label) => label === choice);
              onChange({ ...value, time: index < 0 ? null : { local: presetTime(hours[index], zone), timezone: zone } });
            }} />
          <Input id={id + "-time"} type="datetime-local" value={value.time?.local ?? ""}
            onChange={(e) => onChange({ ...value, time: e.target.value ? { local: e.target.value, timezone: zone } : null })} />
        </Field>
        {value.time && <Field>
          <FieldLabel htmlFor={id + "-zone"}>{t.zone}</FieldLabel>
          <Combobox items={zones} value={zone} onValueChange={(timezone) => {
            if (timezone && value.time) onChange({ ...value, time: { ...value.time, timezone } });
          }}>
            <ComboboxSelectTrigger id={id + "-zone"} className="w-full" />
            <ComboboxContent><ComboboxInput showTrigger={false} placeholder={t.zone} />
              <ComboboxList>{(item: string) => <ComboboxItem key={item} value={item}>{item}</ComboboxItem>}</ComboboxList>
            </ComboboxContent>
          </Combobox>
        </Field>}
        <FieldLabel>
          <Checkbox checked={separate} onCheckedChange={(checked) => {
            setSeparate(checked);
            onChange({ ...value, place: "", members: {} });
          }} />{t.separate}
        </FieldLabel>
        {separate ? members.map(({ id: key, name }) => {
          const setting = value.members[key] ?? { place: "", activity: "" };
          return <FieldSet key={key}>
            <FieldLegend>{name}</FieldLegend>
            <Place label={t.location} value={setting.place} change={(place) => memberChange(key, { ...setting, place })} />
            <Field>
              <FieldLabel htmlFor={id + key}>{t.activity}</FieldLabel>
              <Input id={id + key} maxLength={100} value={setting.activity}
                onChange={(e) => memberChange(key, { ...setting, activity: e.target.value })} />
            </Field>
          </FieldSet>;
        }) : <Place label={t.place} value={value.place} change={(place) => onChange({ ...value, place })} />}
        <Field>
          <FieldLabel htmlFor={id + "-premise"}>{t.premise}</FieldLabel>
          <Choices label={t.premise} options={t.premises.map(([label]) => label)}
            value={t.premises.find(([, text]) => text === value.premise)?.[0] ?? ""}
            select={(choice) => onChange({ ...value, premise: t.premises.find(([label]) => label === choice)?.[1] ?? "" })} />
          <Textarea id={id + "-premise"} rows={3} maxLength={300} value={value.premise}
            onChange={(e) => onChange({ ...value, premise: e.target.value })} />
        </Field>
      </FieldGroup>
    </fieldset>
  );
}

export function BackgroundDetails({ value, memberName }: {
  value: GroupBackground; memberName: (id: string) => string;
}) {
  const lines = [
    ...(value.time ? [value.time.local.replace("T", " ") + " (" + value.time.timezone + ")"] : []),
    value.place,
    ...Object.entries(value.members).map(([id, setting]) =>
      memberName(id) + " · " + [setting.place, setting.activity].filter(Boolean).join(" · ")),
    value.premise,
  ].filter(Boolean);
  return <Dialog>
    <DialogTrigger render={<Button variant="link" size="sm" className="max-w-full" />}>
      <span className="truncate">{t.title} · {lines.join(" · ")}</span>
    </DialogTrigger>
    <DialogContent>
      <DialogHeader><DialogTitle>{t.title}</DialogTitle></DialogHeader>
      <div className="flex max-h-[60dvh] flex-col gap-2 overflow-y-auto whitespace-pre-wrap wrap-anywhere">
        {!value.time && <p>{t.time} · {t.now}</p>}
        {lines.map((line, index) => <p key={index}>{line}</p>)}
      </div>
    </DialogContent>
  </Dialog>;
}
