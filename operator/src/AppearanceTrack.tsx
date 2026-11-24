import { useEffect, useState } from "react";
import { Camera, ChevronLeft } from "lucide-react";
import { api, date, pts } from "./types";
import { bestObservation, Group, Hit, Results } from "./appearances";

export function AppearanceTrack({
  id,
  occ,
  assetId,
  onOpen,
}: {
  id: string;
  occ: string;
  assetId: string;
  onOpen: (hit: Hit) => void;
}) {
  const [groups, setGroups] = useState<Group[]>(),
    [error, setError] = useState(false),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setGroups(undefined);
    setError(false);
    api<Results>("/api/identity/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ detection_id: id, limit: 100 }),
    })
      .then((r) => {
        if (controller.signal.aborted) return;
        const other = r.groups.filter(
          (group) =>
            !(
              group.occurrence_id === occ &&
              group.observations.some(
                (observation) => observation.detection_id === id,
              )
            ),
        );
        // Show a distinct retained recording first when looping material dominates the top scores.
        const shownAssets = new Set([assetId]);
        const distinct: Group[] = [],
          remaining: Group[] = [];
        for (const group of other) {
          if (group.asset_id && !shownAssets.has(group.asset_id)) {
            shownAssets.add(group.asset_id);
            distinct.push(group);
          } else {
            remaining.push(group);
          }
        }
        setGroups([...distinct, ...remaining]);
      })
      .catch(() => {
        if (!controller.signal.aborted) setError(true);
      });
    return () => controller.abort();
  }, [id, occ, assetId, retry]);
  return (
    <section className="appearances" aria-label="مشاهده‌های دیگر">
      <h3>مشاهده‌های دیگر</h3>
      {error ? (
        <p role="alert">
          جست‌وجوی این مشاهده در دسترس نیست.{" "}
          <button onClick={() => setRetry((n) => n + 1)}>تلاش دوباره</button>
        </p>
      ) : !groups ? (
        <p role="status">در حال جست‌وجو…</p>
      ) : groups.length === 0 ? (
        <p>نتیجه‌ای یافت نشد</p>
      ) : (
        <div className="track-scroll">
          {groups.map((g) => {
            const hit = bestObservation(g);
            return (
              <button
                key={hit.detection_id}
                data-detection-id={hit.detection_id}
                onClick={() => onOpen(hit)}
              >
                <img
                  loading="lazy"
                  src={hit.thumbnail_url}
                  alt="چهره در مشاهدهٔ دیگر"
                />
                <span>
                  <b>
                    <Camera />
                    <bdi>{g.source_id}</bdi>
                    {g.asset_id && (
                      <>
                        {" "}
                        / <bdi>{g.asset_id}</bdi>
                      </>
                    )}
                  </b>
                  <small>
                    <bdi dir="ltr">{date(hit.observed_utc ?? null)}</bdi>
                  </small>
                  <small>
                    موقعیت <bdi dir="ltr">{pts(hit.media_pts_s)}</bdi>
                    {g.repeated && " · بازپخش"}
                  </small>
                </span>
                <ChevronLeft />
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}
