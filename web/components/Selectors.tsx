"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";

export function useWorkspaces() {
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState("");
  const reload = () =>
    api("/workspaces").then(setItems).catch((e: Error) => setError(String(e)));
  useEffect(() => {
    reload();
  }, []);
  return { items, error, reload };
}

export function WorkspaceSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (id: string) => void;
}) {
  const { items } = useWorkspaces();
  return (
    <select
      className="rounded border px-2 py-1"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">— انتخاب ورک‌اسپیس —</option>
      {items.map((w) => (
        <option key={w.id} value={w.id}>
          {w.name} ({w.type})
        </option>
      ))}
    </select>
  );
}

export function KBSelect({
  wsId,
  value,
  onChange,
}: {
  wsId: string;
  value: string;
  onChange: (id: string) => void;
}) {
  const [items, setItems] = useState<any[]>([]);
  useEffect(() => {
    if (wsId) api(`/workspaces/${wsId}/knowledge-bases`).then(setItems).catch(() => {});
    else setItems([]);
  }, [wsId]);
  return (
    <select
      className="rounded border px-2 py-1"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">— انتخاب بیس دانش —</option>
      {items.map((k) => (
        <option key={k.id} value={k.id}>
          {k.title}
        </option>
      ))}
    </select>
  );
}

export function FormError({ e }: { e: string }) {
  if (!e) return null;
  return <p className="mt-2 text-sm text-red-700">{e}</p>;
}
