"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/admin";
import { FormError } from "@/components/Selectors";

export default function UsersPage() {
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState("");
  const reload = () => api("/admin/users").then(setItems).catch((e: Error) => setError(String(e)));
  useEffect(() => {
    reload();
  }, []);

  async function toggle(u: any) {
    setError("");
    try {
      await api(`/admin/users/${u.id}`, { method: "PATCH", json: { is_admin: !u.is_admin } });
      reload();
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
  }

  return (
    <main>
      <h1 className="text-xl font-bold">کاربران</h1>
      <table className="mt-4 w-full text-sm">
        <thead>
          <tr className="text-right text-slate-500">
            <th className="p-2">email</th>
            <th className="p-2">نام</th>
            <th className="p-2">ادمین</th>
            <th className="p-2">عمل</th>
          </tr>
        </thead>
        <tbody>
          {items.map((u) => (
            <tr key={u.id} className="border-t">
              <td className="p-2" dir="ltr">{u.email}</td>
              <td className="p-2">{u.full_name}</td>
              <td className="p-2">{u.is_admin ? "بله" : "—"}</td>
              <td className="p-2">
                <button className="text-blue-700 hover:underline" onClick={() => toggle(u)}>
                  {u.is_admin ? "لغو ادمین" : "ادمین کن"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <FormError e={error} />
    </main>
  );
}
