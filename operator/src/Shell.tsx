import { useEffect, useRef, useState } from "react";
import {
  Activity,
  Aperture,
  Bell,
  Camera,
  Clapperboard,
  ExternalLink,
  Image,
  KeyRound,
  Radio,
  Search,
  ShieldCheck,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { api, date, PeoplePage, Link, Recording, Route, View } from "./types";
import { useData } from "./hooks";
import { nameFa } from "./Gallery";

export const labels: Record<View, string> = {
  recordings: "بازبینی ویدئو",
  live: "پخش زنده",
  search: "جست‌وجو با تصویر",
  gallery: "گالری مرجع",
  health: "وضعیت سامانه",
};
const nav = [
  { view: "recordings", icon: Clapperboard },
  { view: "live", icon: Radio },
  { view: "search", icon: Image },
  { view: "gallery", icon: UsersRound },
] as const;
const noticeNames: Record<string, string> = {
  retry: "تلاش مجدد پردازش",
  source_outage: "قطعی منبع",
  storage_guard: "توقف برای حفظ فضای دیسک",
  backpressure: "صف پردازش پر است",
  expired: "پایان زمان نگهداری",
};
type Panel = "search" | "notifications" | "access";
export function ShellHeader({ route, go }: { route: Route; go: Link }) {
  const [panel, setPanel] = useState<Panel | null>(null),
    dialog = useRef<HTMLDialogElement>(null);
  const close = () => setPanel(null);
  function navigate(r: Route) {
    close();
    go(r);
  }
  useEffect(() => {
    const el = dialog.current;
    if (!panel || !el) return;
    el.showModal();
    return () => el.close();
  }, [panel]);
  useEffect(() => {
    const shortcut = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPanel((p) => (p === "search" ? null : "search"));
      }
    };
    window.addEventListener("keydown", shortcut);
    return () => window.removeEventListener("keydown", shortcut);
  }, []);
  return (
    <>
      <header className="top">
        <a
          className="brand"
          href="?view=recordings"
          aria-label="Visions"
          onClick={(e) => {
            if (!e.metaKey && !e.ctrlKey) {
              e.preventDefault();
              navigate({ view: "recordings" });
            }
          }}
        >
          <Aperture />
          Visions
        </a>
        <nav aria-label="ناوبری اصلی">
          {nav.map(({ view, icon: Icon }) => (
            <a
              key={view}
              aria-current={route.view === view ? "page" : undefined}
              href={"?view=" + view}
              onClick={(e) => {
                if (!e.metaKey && !e.ctrlKey) {
                  e.preventDefault();
                  navigate({ view });
                }
              }}
            >
              <Icon />
              {labels[view]}
            </a>
          ))}
        </nav>
        <div className="utilities">
          <button
            className="icon-only"
            title="جست‌وجو (Ctrl+K)"
            aria-label="جست‌وجوی سراسری"
            onClick={() => setPanel("search")}
          >
            <Search />
          </button>
          <button
            className="icon-only"
            title="رویدادهای سامانه"
            aria-label="اعلان‌ها"
            onClick={() => setPanel("notifications")}
          >
            <Bell />
          </button>
          <button
            className="avatar icon-only"
            title="دسترسی و ابزارها"
            aria-label="نشست و دسترسی"
            onClick={() => setPanel("access")}
          >
            <UserRound />
          </button>
        </div>
      </header>
      {panel && (
        <dialog
          className="utility-dialog"
          ref={dialog}
          aria-labelledby="dialog-title"
          onCancel={close}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              const r = e.currentTarget.getBoundingClientRect();
              if (
                e.clientX < r.left ||
                e.clientX > r.right ||
                e.clientY < r.top ||
                e.clientY > r.bottom
              )
                close();
            }
          }}
        >
          <div className="bar">
            <h2 id="dialog-title">
              {
                {
                  search: "جست‌وجوی سراسری",
                  notifications: "رویدادهای سامانه",
                  access: "نشست و دسترسی",
                }[panel]
              }
            </h2>
            <button className="icon-only" aria-label="بستن" onClick={close}>
              <X />
            </button>
          </div>
          {panel === "search" ? (
            <GlobalSearch go={navigate} />
          ) : panel === "notifications" ? (
            <Notifications go={navigate} />
          ) : (
            <div className="inside access">
              <p>
                <ShieldCheck />
                دسترسی خصوصی از طریق تونل SSH
              </p>
              <p>
                هویت حساب کاربری در این سرویس ارائه نمی‌شود. برای تغییر گالری،
                کلید مدیریت لازم است.
              </p>
              <button onClick={() => navigate({ view: "gallery" })}>
                <KeyRound />
                مدیریت گالری مرجع
              </button>
              <button onClick={() => navigate({ view: "health" })}>
                <Activity />
                وضعیت سامانه
              </button>
              <div className="engineering-links" lang="en" dir="ltr">
                <a
                  href="http://127.0.0.1:8091/operations"
                  target="_blank"
                  rel="noreferrer"
                >
                  Operations <ExternalLink />
                </a>
                <a
                  href="http://127.0.0.1:13000/d/visions-operator/operator-feed-and-search"
                  target="_blank"
                  rel="noreferrer"
                >
                  Grafana <ExternalLink />
                </a>
              </div>
            </div>
          )}
        </dialog>
      )}
    </>
  );
}
function Notifications({ go }: { go: Link }) {
  const q = useData<{
    notices: { at: number; state: string; service: string }[];
  }>("/api/operator/health", 5000);
  return (
    <div className="inside">
      {q.error ? (
        <p role="alert">{q.error}</p>
      ) : !q.value ? (
        <p>در حال دریافت…</p>
      ) : !q.value.notices.length ? (
        <p>رویدادی ثبت نشده است.</p>
      ) : (
        <ul className="notice-list">
          {q.value.notices.map((n, i) => (
            <li key={n.at + "-" + i}>
              <span>{noticeNames[n.state] || "تغییر وضعیت"}</span>
              <small>
                <bdi>{n.service}</bdi> · <bdi dir="ltr">{date(n.at)}</bdi>
              </small>
            </li>
          ))}
        </ul>
      )}
      <button onClick={() => go({ view: "health" })}>
        <Activity />
        وضعیت سامانه
      </button>
    </div>
  );
}
function GlobalSearch({ go }: { go: Link }) {
  const [query, setQuery] = useState(""),
    [err, setErr] = useState("");
  const q = query.trim().toLowerCase();
  const gallery = useData<PeoplePage>(
      "/api/identity/people?limit=6&q=" + encodeURIComponent(q),
    ),
    recordings = useData<{ items: Recording[] }>("/api/operator/recordings");
  const people = gallery.value?.people || [];
  const clips = (recordings.value?.items || [])
    .filter((r) =>
      (r.asset_id + " " + r.source_id + " " + r.occurrence_id)
        .toLowerCase()
        .includes(q),
    )
    .slice(0, 6);
  const exact = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(q);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  async function observation() {
    request.current?.abort();
    const run = new AbortController();
    request.current = run;
    try {
      const r = await api<{
        event: { source_id: string; frame_index: number; detection_id: string };
      }>("/api/operator/details/" + q, { signal: run.signal });
      if (!run.signal.aborted)
        go({
          view: "recordings",
          occ: r.event.source_id,
          frame: r.event.frame_index,
          face: r.event.detection_id,
        });
    } catch {
      if (!run.signal.aborted)
        setErr("این مشاهده در داده‌های نگهداری‌شده پیدا نشد.");
    }
  }
  return (
    <div className="inside">
      <label className="search-input">
        <Search />
        <input
          autoFocus
          type="search"
          aria-label="جست‌وجو"
          placeholder="نام فرد، منبع، ضبط اخیر یا شناسهٔ مشاهده"
          value={query}
          onChange={(e) => {
            request.current?.abort();
            setQuery(e.target.value);
            setErr("");
          }}
        />
      </label>
      {err && <p role="alert">{err}</p>}
      {exact && (
        <button onClick={() => void observation()}>
          باز کردن مشاهده با این شناسه
        </button>
      )}
      <div className="global-results">
        {people.length > 0 && <h3>گالری مرجع</h3>}
        {people.map((r) => (
          <button
            key={r.person_id}
            onClick={() => go({ view: "gallery", person: r.person_id })}
          >
            <img src={r.thumbnail_url} loading="lazy" alt="" />
            {nameFa(r.display_name)}
          </button>
        ))}
        {clips.length > 0 && <h3>ضبط‌های اخیر</h3>}
        {clips.map((r) => (
          <button
            key={r.occurrence_id}
            onClick={() =>
              go({ view: "recordings", occ: r.occurrence_id, frame: 0 })
            }
          >
            <Camera />
            <bdi>{r.asset_id}</bdi>
            <small>
              <bdi>{r.source_id}</bdi> ·{" "}
              <bdi dir="ltr">{date(r.started_at)}</bdi>
            </small>
          </button>
        ))}
        {gallery.error || recordings.error ? (
          <p role="alert">بخشی از نتایج در دسترس نیست.</p>
        ) : !gallery.value || !recordings.value ? (
          <p>در حال دریافت…</p>
        ) : !clips.length && !people.length && !exact ? (
          <p>نتیجه‌ای یافت نشد</p>
        ) : null}
      </div>
    </div>
  );
}
