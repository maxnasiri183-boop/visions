import { ReactNode, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowLeftRight,
  Check,
  ChevronDown,
  Copy,
  Database,
  Images,
  ScanFace,
  ScanLine,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { useData } from "./hooks";
import { api, date, Decision, Face, fa, Frame, Link, Recording } from "./types";
import { nameFa } from "./Gallery";

export function Inspector({
  children,
  count,
  selected,
  onDismiss,
  mobile,
}: {
  children: ReactNode;
  count: number;
  selected?: string;
  onDismiss: () => void;
  mobile: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    if (!mobile || !selected || !dialog.current) return;
    const el = dialog.current;
    if (!el.open) el.showModal();
    return () => el.close();
  }, [mobile, Boolean(selected)]);
  const header = (
    <div className="inspector-heading">
      <ScanFace />
      <h2 id="inspector-title">شناسایی چهره</h2>
      {mobile ? (
        <button
          autoFocus
          className="icon-only push-end"
          onClick={onDismiss}
          aria-label="بستن شناسایی چهره"
        >
          <X />
        </button>
      ) : (
        <small className="push-end">{fa(count)} چهره</small>
      )}
    </div>
  );
  if (mobile && !selected)
    return (
      <div className="mobile-hint">
        <ScanFace size={18} />
        {children}
      </div>
    );
  return mobile ? (
    <dialog
      className="inspector sheet"
      aria-labelledby="inspector-title"
      ref={dialog}
      onCancel={(e) => {
        e.preventDefault();
        onDismiss();
      }}
      onClick={(e) => {
        if (
          e.target === dialog.current &&
          e.clientY < dialog.current.getBoundingClientRect().top
        )
          onDismiss();
      }}
    >
      <div className="sheet-grip" />
      {header}
      {children}
    </dialog>
  ) : (
    <aside className="inspector card" aria-labelledby="inspector-title">
      {header}
      {children}
    </aside>
  );
}

const pending = (value?: Decision) => value?.state === "pending";
const titles: Record<string, string> = {
  pending: "در حال بررسی",
  matched: "هویت پیشنهادی",
  unknown: "ناشناخته",
  poor_quality: "کیفیت تصویر کافی نیست",
  error: "خطا در شناسایی",
  empty: "نتیجه‌ای یافت نشد",
};
export function Evidence({
  face,
  frame,
  rec,
  go,
  onAppearances,
  appearancesOpen,
}: {
  face: Face;
  frame: Frame;
  rec: Recording;
  go: Link;
  onAppearances: () => void;
  appearancesOpen: boolean;
}) {
  const [revision, setRevision] = useState(0),
    [technical, setTechnical] = useState(false),
    [copy, setCopy] = useState("");
  const [pairCheck, setPairCheck] = useState("");
  const [checking, setChecking] = useState(false);
  const path =
    "/api/identity/observations/" + face.detection_id + "?review=" + revision;
  const query = useData<Decision>(path, 1500, pending);
  const result = query.error ? undefined : query.value;
  const state = query.error
    ? "unavailable"
    : !result
      ? "pending"
      : titles[result.state]
        ? result.state
        : "empty";
  const known = state === "matched" && result?.person_id;
  const detail = useData<{
    event: Record<string, unknown>;
    sql: Record<string, unknown> | null;
  }>(technical ? "/api/operator/details/" + face.detection_id : null);
  function compare() {
    setRevision((n) => n + 1);
  }
  return (
    <div className="evidence inside" data-detection-id={face.detection_id}>
      <div className="pair" aria-label="مقایسهٔ تصویرها">
        <figure>
          <div className="portrait">
            <img
              src={"/api/operator/crops/" + face.detection_id}
              alt="تصویر از ویدئو"
            />
          </div>
          <figcaption>تصویر از ویدئو</figcaption>
          <small>
            فریم <bdi>{fa(frame.frame_index)}</bdi>
          </small>
        </figure>
        <ArrowLeftRight className="comparison-symbol" aria-hidden="true" />
        <figure>
          <div className="portrait">
            {known && result?.portrait_url ? (
              <img
                src={result.face_url || result.portrait_url}
                alt="تصویر مرجع"
              />
            ) : (
              <div
                className="portrait-empty"
                aria-label="تصویر مرجع در دسترس نیست"
              >
                <UserRound />
              </div>
            )}
          </div>
          <figcaption>تصویر مرجع</figcaption>
          <small>
            {known ? (
              <>
                نسخهٔ <bdi>{fa(Number(result.version))}</bdi>
              </>
            ) : (
              "—"
            )}
          </small>
        </figure>
      </div>
      <div
        className={"decision decision-" + state}
        key={state}
        data-decision={state}
        aria-live="polite"
      >
        <p>
          <span className="status-mark" />
          {state === "unavailable"
            ? "ارتباط با سرویس برقرار نشد"
            : titles[state]}
        </p>
        {known && <h3>{nameFa(result.display_name)}</h3>}
        {result &&
          ["matched", "unknown", "poor_quality"].includes(result.state) && (
            <small>نتیجهٔ ثبت‌شدهٔ پردازش ویدئو</small>
          )}
        {state === "unknown" && (
          <small>
            {result?.score == null
              ? "مرجع واجد شرایطی ثبت نشده است."
              : "شباهت از آستانهٔ گالری کمتر است."}
          </small>
        )}
        {state === "unavailable" && (
          <small>برای دریافت دوباره، مقایسه با گالری را بزنید.</small>
        )}
        {state === "error" && (
          <small>پردازش این مشاهده با خطا ثبت شده است.</small>
        )}
      </div>
      <dl className="facts">
        <div>
          <dt>امتیاز شباهت</dt>
          <dd>
            <bdi dir="ltr">
              {result?.score == null ? "—" : fa(result.score, 3)}
            </bdi>
          </dd>
        </div>
        <div>
          <dt>زمان مشاهده</dt>
          <dd>
            <bdi dir="ltr">{date(frame.ingested_at)}</bdi>
          </dd>
        </div>
      </dl>
      <button
        className="primary inspector-action"
        onClick={compare}
        title="دریافت نتیجهٔ ثبت‌شده برای این مشاهده"
      >
        <ScanLine />
        مقایسه با گالری
      </button>
      <button
        className="inspector-action"
        onClick={onAppearances}
        aria-expanded={appearancesOpen}
      >
        <Images />
        مشاهده‌های دیگر
      </button>
      <div className="record-link-slot">
        {known && (
          <button
            className="text-action"
            onClick={() =>
              go({
                view: "gallery",
                person: result.person_id,
                version: String(result.version),
              })
            }
          >
            <UsersRound />
            پروندهٔ مرجع <ArrowLeft />
          </button>
        )}
      </div>
      <details
        className="technical"
        onToggle={(e) => setTechnical(e.currentTarget.open)}
      >
        <summary>
          <Database />
          جزئیات فنی
          <ChevronDown />
        </summary>
        {known && (
          <div>
            <button
              disabled={checking}
              onClick={async () => {
                setChecking(true);
                setPairCheck("");
                try {
                  const response = await api<{
                    score: number;
                    detection_id: string;
                    reference_id: string;
                  }>(
                    "/api/identity/observations/" +
                      face.detection_id +
                      "/compare",
                    { method: "POST" },
                  );
                  if (
                    response.detection_id !== face.detection_id ||
                    response.reference_id !== result.reference_id
                  )
                    throw Error();
                  setPairCheck(
                    "شباهت محاسبه‌شده در این لحظه: " + fa(response.score, 6),
                  );
                } catch {
                  setPairCheck("محاسبه انجام نشد؛ دوباره تلاش کنید.");
                } finally {
                  setChecking(false);
                }
              }}
            >
              محاسبهٔ مجدد شباهت دو تصویر
            </button>
            <p role="status">{pairCheck}</p>
          </div>
        )}
        <p>شناسهٔ مشاهده</p>
        <code>{face.detection_id}</code>
        <div className="row">
          <button
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(face.detection_id);
                setCopy("کپی شد");
              } catch {
                setCopy("شناسه را انتخاب و کپی کنید.");
              }
            }}
          >
            {copy ? <Check /> : <Copy />}
            {copy || "کپی شناسه"}
          </button>
          <a
            href={"/api/operator/original/" + face.detection_id + ".png"}
            target="_blank"
            rel="noreferrer"
          >
            فریم اصلی
          </a>
        </div>
        <dl className="facts">
          {[
            ["جلسهٔ ضبط", rec.session_id],
            ["تاریخ ضبط اصلی", "در مجموعه‌داده اعلام نشده است"],
            ["زمان شروع بازپخش", date(rec.scheduled_at)],
            ["منبع / فریم", rec.source_id + " / " + frame.frame_index],
            [
              "آستانهٔ تصمیم",
              result?.threshold == null ? "—" : fa(result.threshold, 3),
            ],
            ["نسخهٔ مدل", result?.model || "—"],
            ["پیش‌پردازش", result?.preprocessing || "—"],
            [
              "نسخهٔ گالری",
              result?.version == null ? "—" : fa(Number(result.version)),
            ],
            ["اطمینان تشخیص چهره", fa(face.confidence, 4)],
            ["موضوع کافکا", face.topic],
            [
              "پارتیشن / موقعیت نخست",
              face.partition_id + " / " + face.kafka_offset,
            ],
            ["آخرین موقعیت", String(face.latest_offset)],
            [
              "سابقهٔ بازپخش",
              face.latest_offset > face.kafka_offset
                ? "انتشار مجدد ثبت شده است"
                : "انتشار مجددی ثبت نشده است",
            ],
            [
              "زمان تا مشاهده در SQL",
              face.sql_visible_at == null
                ? "در انتظار"
                : fa(
                    1000 * face.sql_visible_at - Date.parse(frame.ingested_at),
                  ) + " ms",
            ],
          ].map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>
                <bdi dir="ltr">{v}</bdi>
              </dd>
            </div>
          ))}
        </dl>
        {technical &&
          (detail.error ? (
            <p role="alert">{detail.error}</p>
          ) : detail.value ? (
            <pre dir="ltr">{JSON.stringify(detail.value, null, 2)}</pre>
          ) : (
            <p>در حال دریافت شواهد…</p>
          ))}
      </details>
    </div>
  );
}
