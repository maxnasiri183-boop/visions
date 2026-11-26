import { useState } from "react";
import { api, fa } from "./types";
import { useData } from "./hooks";

type Speed = { configured: number; current: number | null; choices: number[] };
export function FeedSpeed({source}: {source:string}) {
  const path = "/api/operator/sources/" + encodeURIComponent(source) + "/speed";
  const [revision, refresh] = useState(0), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const data = useData<Speed>(path + "?r=" + revision, 3000);
  async function change(speed:number) {
    setBusy(true); setError("");
    try {
      await api(path, {method:"POST",headers:{"Content-Type":"application/json"}, body:JSON.stringify({speed})});
      refresh(n=>n+1);
    } catch {setError("سرعت ثبت نشد؛ دوباره تلاش کنید.");} finally {setBusy(false);}
  }
  return <div><label className="inline-filter">سرعت ارسال منبع
    <select aria-label="سرعت ارسال منبع" value={data.value?.configured ?? ""} disabled={busy || !data.value}
      onChange={e=>void change(Number(e.target.value))}>
      {!data.value && <option value="">—</option>}
      {data.value?.choices.map(s=><option key={s} value={s}>{fa(s, 1)}×</option>)}
    </select></label>
    {data.value && data.value.current !== data.value.configured && <small role="status">در انتظار نوبت پخش جدید</small>}
    {(error || data.error) && <small role="alert">{error || data.error}</small>}</div>;
}
