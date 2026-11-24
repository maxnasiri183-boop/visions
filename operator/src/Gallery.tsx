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
  const shown = refs.filter((r) =>
    route.person
      ? r.person_id === route.person
      : [r.person_id, nameFa(r.display_name)]
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
        <div>
          <h1>{route.person ? "پروندهٔ مرجع" : "گالری مرجع"}</h1>
          <p>
            {route.person
              ? nameFa(shown[0]?.display_name) || "دریافت مشخصات…"
              : "افراد، تصاویر ثبت‌نام و نسخه‌های منتشرشده"}
          </p>
        </div>
        {route.person ? (
          <button onClick={() => history.back()}>بازگشت به صفحهٔ قبل</button>
        ) : (
          <button className="primary" onClick={() => setEditor(!editor)}>
            مدیریت گالری
          </button>
        )}
      </div>
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
          {!route.person && (
            <label>
              جست‌وجوی فرد
              <input
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="نام یا شناسهٔ فرد"
              />
            </label>
          )}
        </div>
        {active && (
          <p>
            نسخهٔ {fa(Number(active.version))} · {fa(active.people_count)} نفر ·
            آستانهٔ شباهت {fa(active.threshold, 3)} ·{" "}
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
              <button disabled={!key || busy}>افزودن تصویر به پیش‌نویس</button>
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
          <p className="empty">فرد یا تصویر مرجعی برای این انتخاب پیدا نشد.</p>
        )}
        {route.person ? (
          <div className="portrait-grid">
            {shown.map((r) => (
              <figure key={r.reference_id}>
                <img
                  src={r.portrait_url}
                  alt={"عکس ثبت‌نام " + nameFa(r.display_name)}
                />
                <figcaption>
                  {r.status === "accepted" ? "پذیرفته‌شده" : "ردشده"} ·{" "}
                  <bdi>{r.session_id}</bdi>
                  <p>{reasonFa(r.reason)}</p>
                </figcaption>
              </figure>
            ))}
          </div>
        ) : (
          <div className="portrait-grid">
            {Array.from(people.values()).map((r) => (
              <button
                key={r.person_id}
                onClick={() =>
                  go({
                    view: "gallery",
                    person: r.person_id,
                    version: route.version,
                  })
                }
              >
                <img src={r.portrait_url} alt="تصویر ثبت‌نام" />
                <strong>{nameFa(r.display_name)}</strong>
                <small>
                  {fa(shown.filter((x) => x.person_id === r.person_id).length)}{" "}
                  تصویر ·{" "}
                  {r.status === "accepted" ? "مرجع واجد شرایط" : "تصویر ردشده"}
                </small>
              </button>
            ))}
          </div>
        )}
      </section>
      {!route.person && (
        <section className="card archive">
          <h2>تاریخچهٔ انتشار</h2>
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
        </section>
      )}
    </>
  );
}
