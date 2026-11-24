import { ReferenceAppearances } from "./ReferenceAppearances";
import {
  ArrowRight,
  ChevronDown,
  History,
  Settings2,
  UsersRound,
  X,
} from "lucide-react";
import { useEffect, useState, useRef } from "react";
import {
  api,
  date,
  fa,
  Gallery as Data,
  Link,
  Reference,
  PeoplePage,
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
  const fixture = !!document.documentElement.dataset.galleryFixture;
  const [q, setQ] = useState(route.q || ""),
    [refresh, setRefresh] = useState(0),
    [editor, setEditor] = useState(false);
  const [key, setKey] = useState(""),
    [message, setMessage] = useState(""),
    [busy, setBusy] = useState(false);
  const status = route.status || "";
  const meta = new URLSearchParams({ r: String(refresh) });
  if (route.version && route.version !== "draft")
    meta.set("version", route.version);
  if (route.person) meta.set("person", route.person);
  const data = useData<Data>("/api/identity/versions?" + meta);
  const versions = data.value?.versions || [];
  const draft = route.version === "draft" || (fixture && !route.version);
  const version = draft
    ? undefined
    : route.version ||
      String(data.value?.person_version || data.value?.active_version || "") ||
      undefined;
  const scopeReady = draft || !!version || !!data.value;
  const query = new URLSearchParams({ r: String(refresh) });
  if (version) query.set("version", version);
  const personQuery = new URLSearchParams(query);
  if (route.q) personQuery.set("q", route.q);
  if (status) personQuery.set("status", status);
  if (route.after) personQuery.set("after", route.after);
  const page = useData<PeoplePage>(
    scopeReady ? "/api/identity/people?" + personQuery : null,
  );
  const [detailPage, setDetailPage] = useState({ person: "", after: "" });
  const detailQuery = new URLSearchParams(query);
  if (detailPage.person === route.person && detailPage.after)
    detailQuery.set("after", detailPage.after);
  const portraits = useData<{
    references: Reference[];
    next_cursor: string | null;
  }>(
    route.person && scopeReady
      ? "/api/identity/people/" +
          encodeURIComponent(route.person) +
          "?" +
          detailQuery
      : null,
  );
  const personRefs = portraits.value?.references || [];
  const people = page.value?.people || [];
  const goRef = useRef(go);
  goRef.current = go;
  const listKey = personQuery.toString();
  function showPerson(person: string) {
    sessionStorage.setItem(
      "visions-gallery-scroll",
      JSON.stringify([listKey, window.scrollY]),
    );
    go({ ...route, person });
  }
  useEffect(() => {
    setQ(route.q || "");
  }, [route.q]);
  useEffect(() => {
    if (q === (route.q || "")) return;
    const timer = window.setTimeout(
      () =>
        goRef.current({
          ...route,
          person: undefined,
          q: q || undefined,
          after: undefined,
        }),
      220,
    );
    return () => window.clearTimeout(timer);
  }, [q, route]);
  useEffect(() => {
    if (route.person || !page.value) return;
    const stored = JSON.parse(
      sessionStorage.getItem("visions-gallery-scroll") || "null",
    );
    if (stored?.[0] === listKey) window.scrollTo(0, stored[1]);
  }, [route.person, listKey, page.value]);
  const active = versions.find(
    (v) => String(v.version) === String(version ?? data.value?.active_version),
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
              else go({ ...route, person: undefined });
            }}
          >
            <ArrowRight />
            بازگشت به صفحهٔ قبل
          </button>
        ) : (
          !fixture && (
            <button className="primary" onClick={() => setEditor(!editor)}>
              <Settings2 />
              مدیریت گالری
            </button>
          )
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
                value={version || "draft"}
                onChange={(e) =>
                  go({
                    view: "gallery",
                    person: route.person,
                    version: e.target.value || undefined,
                  })
                }
              >
                <option value="draft">پیش‌نویس و همهٔ تصاویر</option>
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
                onChange={(e) =>
                  go({
                    ...route,
                    status: e.target.value || undefined,
                    after: undefined,
                    person: undefined,
                  })
                }
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
          {page.error && <p role="alert">{page.error}</p>}
          {!page.value && !page.error && <p role="status">در حال دریافت…</p>}
          {page.value && !people.length && (
            <p className="empty">
              فرد یا تصویر مرجعی برای این انتخاب پیدا نشد.
            </p>
          )}
          <div className="portrait-grid">
            {people.map((r) => (
              <button
                key={r.person_id}
                aria-pressed={r.person_id === route.person}
                onClick={() => showPerson(r.person_id)}
              >
                <img
                  src={r.thumbnail_url}
                  loading="lazy"
                  width="144"
                  height="144"
                  alt={"تصویر ثبت‌نام " + nameFa(r.display_name)}
                />
                <strong>{nameFa(r.display_name)}</strong>
                <small>
                  {fa(r.reference_count)} تصویر ثبت‌نام ·{" "}
                  {r.status === "accepted" ? "واجد شرایط" : "ردشده"}
                </small>
              </button>
            ))}
          </div>
          <nav className="row" aria-label="صفحه‌های گالری">
            {route.after && (
              <button
                aria-label="صفحهٔ قبل"
                onClick={() => {
                  const prev = sessionStorage.getItem(
                    "visions-gallery-prev:" + listKey,
                  );
                  go({ ...route, person: undefined, after: prev || undefined });
                }}
              >
                صفحهٔ قبل
              </button>
            )}
            {page.value?.next_cursor && (
              <button
                aria-label="صفحهٔ بعد"
                onClick={() => {
                  go({
                    ...route,
                    person: undefined,
                    after: page.value!.next_cursor!,
                  });
                  const next = new URLSearchParams(personQuery);
                  next.set("after", page.value!.next_cursor!);
                  sessionStorage.setItem(
                    "visions-gallery-prev:" + next.toString(),
                    route.after || "",
                  );
                  window.scrollTo(0, 0);
                }}
              >
                صفحهٔ بعد
              </button>
            )}
          </nav>
        </section>
        {route.person && (
          <aside className="card gallery-person" aria-label="پروندهٔ مرجع">
            <div className="bar">
              <h2>پروندهٔ مرجع</h2>
              <button
                className="icon-only"
                aria-label="بستن پرونده"
                onClick={() => go({ ...route, person: undefined })}
              >
                <X />
              </button>
            </div>
            <div className="inside">
              <h3>{nameFa(personRefs[0]?.display_name)}</h3>
              {!personRefs.length && (
                <p>
                  {portraits.error ||
                    (portraits.value
                      ? "تصویر مرجع در این نسخه پیدا نشد."
                      : "در حال دریافت…")}
                </p>
              )}
              {personRefs.map((r) => (
                <figure key={r.reference_id}>
                  <a
                    href={r.portrait_url}
                    target="_blank"
                    rel="noreferrer"
                    aria-label={
                      "باز کردن عکس مرجع در اندازهٔ اصلی · " +
                      nameFa(r.display_name)
                    }
                  >
                    <img
                      src={r.portrait_url}
                      alt={"عکس ثبت‌نام " + nameFa(r.display_name)}
                    />
                  </a>
                  <figcaption>
                    عکس ثبت‌نام ·{" "}
                    {r.status === "accepted" ? "پذیرفته‌شده" : "ردشده"}
                  </figcaption>
                  <small>
                    <bdi>{r.session_id}</bdi>
                  </small>
                  <p>{reasonFa(r.reason)}</p>
                  {!fixture && r.status === "accepted" && (
                    <ReferenceAppearances
                      reference={r.reference_id}
                      version={version}
                      go={go}
                    />
                  )}
                </figure>
              ))}
              {portraits.value?.next_cursor && (
                <button
                  onClick={() =>
                    setDetailPage({
                      person: route.person!,
                      after: portraits.value!.next_cursor!,
                    })
                  }
                >
                  تصاویر بعدی
                </button>
              )}
              {detailQuery.has("after") && (
                <button
                  onClick={() => setDetailPage({ person: "", after: "" })}
                >
                  نخستین تصاویر
                </button>
              )}
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
