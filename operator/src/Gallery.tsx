import { ReferenceAppearances } from "./ReferenceAppearances";
import {
  ArrowRight,
  ChevronDown,
  History,
  Settings2,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  api,
  date,
  fa,
  Gallery as Data,
  Link,
  Reference,
  Route,
} from "./types";
import { useData } from "./hooks";
export const nameFa = (s?: string) =>
  s?.replace(/^Participant /i, "شرکت‌کنندهٔ ");
const reasonFa = (s?: string) =>
  !s
    ? ""
    : s.includes("one face")
      ? "تصویر باید دقیقاً یک چهره داشته باشد."
      : s.includes("small")
        ? "چهره یا فاصلهٔ چشم‌ها کوچک است."
        : s.includes("detail")
          ? "وضوح تصویر کافی نیست."
          : s.includes("light") || s.includes("dark")
            ? "نور تصویر مناسب نیست."
            : "تصویر شرایط ثبت مرجع را ندارد.";
export function GalleryPage({ route, go }: { route: Route; go: Link }) {
  const [q, setQ] = useState(""),
    [refresh, setRefresh] = useState(0),
    [editor, setEditor] = useState(false);
  const [key, setKey] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const url =
    "/api/identity/gallery" +
    (route.version ? "/" + encodeURIComponent(route.version) : "") +
    "?r=" +
    refresh;
  const data = useData<Data>(url);
  const refs = data.value?.references || [],
    versions = data.value?.versions || [];
  const [status, setStatus] = useState("");
  const personRefs = refs.filter((r) => r.person_id === route.person);
  const shown = refs.filter(
    (r) =>
      (!status || r.status === status) &&
      [r.person_id, nameFa(r.display_name)]
        .join(" ")
        .toLowerCase()
        .includes(q.toLowerCase()),
  );
  const people = new Map<string, Reference>();
  shown.forEach((r) => {
    if (!people.has(r.person_id) || r.status === "accepted")
      people.set(r.person_id, r);
  });
  const active = versions.find(
    (v) =>
      String(v.version) === String(route.version ?? data.value?.active_version),
  );
  useEffect(() => {
    setMessage("");
    setEditor(false);
  }, [route.person, route.version]);
  async function mutate(kind: "reference" | "publish", form?: HTMLFormElement) {
    setBusy(true);
    setMessage("");
    try {
      const opts: RequestInit = {
        method: "POST",
        headers: { "X-Gallery-Key": key },
        body: form ? new FormData(form) : undefined,
      };
      let response = await api<{ status?: string }>(
        kind === "publish"
          ? "/api/identity/gallery/publish"
          : "/api/identity/gallery/reference",
        opts,
      );
      setMessage(
        kind === "publish"
          ? "نسخهٔ جدید منتشر شد."
          : response.status === "rejected"
            ? "تصویر در بخش ردشده‌ها ثبت شد."
            : "مرجع به پیش‌نویس اضافه شد.",
      );
      setRefresh((r) => r + 1);
      form?.reset();
    } catch {
      setMessage(
        "ثبت انجام نشد. کلید مدیریت، مشخصات فرد و فایل تصویر را بررسی کنید.",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-head">
        <div className="page-title">
          <span className="heading-icon">
            <UsersRound />
          </span>
          <div>
            <h1>گالری مرجع</h1>
            <p>تصاویر ثبت‌نام و نسخه‌های منتشرشده</p>
          </div>
        </div>
        {route.person ? (
          <button
            onClick={() => {
              if (history.state?.visions) history.back();
              else go({ view: "gallery", version: route.version });
            }}
          >
            <ArrowRight />
            بازگشت به صفحهٔ قبل
          </button>
        ) : (
          <button className="primary" onClick={() => setEditor(!editor)}>
            <Settings2 />
            مدیریت گالری
          </button>
        )}
      </div>
      <div
        className={
          route.person ? "gallery-layout with-person" : "gallery-layout"
        }
      >
        <section className="card inside">
          {data.error && <p role="alert">{data.error}</p>}
          <div className="gallery-tools">
            <label>
              نسخهٔ گالری
              <select
                value={route.version || ""}
                onChange={(e) =>
                  go({
                    view: "gallery",
                    person: route.person,
                    version: e.target.value || undefined,
                  })
                }
              >
                <option value="">پیش‌نویس و همهٔ تصاویر</option>
                {versions.map((v) => (
                  <option key={v.version} value={v.version}>
                    نسخهٔ {fa(Number(v.version))} · {date(v.created_at)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              جست‌وجوی فرد
              <input
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="نام یا شناسهٔ فرد"
              />
            </label>
            <label>
              وضعیت ثبت‌نام
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
              >
                <option value="">همهٔ تصاویر</option>
                <option value="accepted">پذیرفته‌شده</option>
                <option value="rejected">ردشده</option>
              </select>
            </label>
          </div>
          {active && (
            <p>
              نسخهٔ {fa(Number(active.version))} · {fa(active.people_count)} نفر
              · آستانهٔ شباهت {fa(active.threshold, 3)} ·{" "}
              {active.calibration?.split_hashes
                ? "تنظیم‌شده با داده‌های آزمایشی جداگانه"
                : "آستانهٔ اولیه؛ برای این گالری ارزیابی نشده است"}
            </p>
          )}
          {editor && (
            <section className="manage">
              <h2>ثبت و انتشار مرجع</h2>
              <label>
                کلید مدیریت
                <input
                  type="password"
                  autoComplete="off"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </label>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void mutate("reference", e.currentTarget);
                }}
              >
                <label>
                  شناسهٔ فرد
                  <input name="person_id" required maxLength={120} />
                </label>
                <label>
                  نام نمایشی
                  <input name="display_name" required maxLength={120} />
                </label>
                <label>
                  جلسهٔ ثبت تصویر
                  <input name="session_id" required maxLength={120} />
                </label>
                <label>
                  عکس ثبت‌نام
                  <input
                    name="file"
                    type="file"
                    required
                    accept="image/png,image/jpeg"
                  />
                </label>
                <button disabled={!key || busy}>
                  افزودن تصویر به پیش‌نویس
                </button>
              </form>
              <button
                disabled={!key || busy}
                className="primary"
                onClick={() => void mutate("publish")}
              >
                انتشار نسخهٔ جدید
              </button>
              <p role="status">{message}</p>
            </section>
          )}
          {!shown.length && (
            <p className="empty">
              فرد یا تصویر مرجعی برای این انتخاب پیدا نشد.
            </p>
          )}
          <div className="portrait-grid">
            {Array.from(people.values()).map((r) => (
              <button
                key={r.person_id}
                aria-pressed={r.person_id === route.person}
                onClick={() =>
                  go({
                    view: "gallery",
                    person: r.person_id,
                    version: route.version,
                  })
                }
              >
                <img
                  src={r.portrait_url}
                  alt={"تصویر ثبت‌نام " + nameFa(r.display_name)}
                />
                <strong>{nameFa(r.display_name)}</strong>
                <small>
                  {fa(shown.filter((x) => x.person_id === r.person_id).length)}{" "}
                  تصویر ثبت‌نام ·{" "}
                  {r.status === "accepted" ? "واجد شرایط" : "ردشده"}
                </small>
              </button>
            ))}
          </div>
        </section>
        {route.person && (
          <aside className="card gallery-person" aria-label="پروندهٔ مرجع">
            <div className="bar">
              <h2>پروندهٔ مرجع</h2>
              <button
                className="icon-only"
                aria-label="بستن پرونده"
                onClick={() => go({ view: "gallery", version: route.version })}
              >
                <X />
              </button>
            </div>
            <div className="inside">
              <h3>{nameFa(personRefs[0]?.display_name)}</h3>
              {!personRefs.length && (
                <p>
                  {data.value
                    ? "تصویر مرجع در این نسخه پیدا نشد."
                    : "در حال دریافت…"}
                </p>
              )}
              {personRefs.map((r) => (
                <figure key={r.reference_id}>
                  <img
                    src={r.portrait_url}
                    alt={"عکس ثبت‌نام " + nameFa(r.display_name)}
                  />
                  <figcaption>
                    عکس ثبت‌نام ·{" "}
                    {r.status === "accepted" ? "پذیرفته‌شده" : "ردشده"}
                  </figcaption>
                  <small>
                    <bdi>{r.session_id}</bdi>
                  </small>
                  <p>{reasonFa(r.reason)}</p>
                  {r.status === "accepted" && <ReferenceAppearances reference={r.reference_id} version={route.version} go={go} />}
                </figure>
              ))}
            </div>
          </aside>
        )}
      </div>
      {!route.person && (
        <details className="card archive record-archive">
          <summary>
            <History />
            تاریخچهٔ انتشار
            <ChevronDown />
          </summary>
          {versions.map((v) => (
            <button
              className="record-row"
              key={v.version}
              onClick={() =>
                go({ view: "gallery", version: String(v.version) })
              }
            >
              <strong>نسخهٔ {fa(Number(v.version))}</strong>
              <span>
                {date(v.created_at)} · {fa(v.people_count)} نفر
              </span>
            </button>
          ))}
        </details>
      )}
    </>
  );
}
