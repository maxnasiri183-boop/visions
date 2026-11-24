import { GalleryPage } from "./Gallery";
import { Health } from "./Health";
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Route, View } from "./types";
import { VideoPage } from "./Video";
import { Search } from "./Search";
import "./style.css";
const labels: Record<View, string> = {
  live: "پخش زنده",
  recordings: "ویدیوها",
  search: "جست‌وجو با تصویر",
  gallery: "گالری مرجع",
  health: "وضعیت سامانه",
};
function read(): Route {
  const q = new URLSearchParams(location.search),
    view = q.get("view") as View;
  return {
    view: Object.hasOwn(labels, view) ? view : "live",
    occ: q.get("occ") || undefined,
    frame: q.has("frame") ? Number(q.get("frame")) : undefined,
    face: q.get("face") || undefined,
    person: q.get("person") || undefined,
    version: q.get("version") || undefined,
  };
}
function App() {
  const [route, setRoute] = useState(read);
  function go(r: Route) {
    const q = new URLSearchParams();
    Object.entries(r).forEach(([k, v]) => {
      if (v !== undefined) q.set(k, String(v));
    });
    history.pushState(null, "", "?" + q);
    setRoute(r);
  }
  useEffect(() => {
    const pop = () => setRoute(read());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    document.title = labels[route.view] + " | دیدبان";
  }, [route.view]);
  return (
    <>
      <header className="top">
        <div className="brand">
          <span aria-hidden="true">◉</span> دیدبان
        </div>
        <nav aria-label="ناوبری اصلی">
          {Object.entries(labels).map(([key, label]) => (
            <a
              key={key}
              href={"?view=" + key}
              aria-current={route.view === key ? "page" : undefined}
              onClick={(e) => {
                if (!e.ctrlKey && !e.metaKey) {
                  e.preventDefault();
                  go({ view: key as View });
                }
              }}
            >
              {label}
            </a>
          ))}
        </nav>
      </header>
      <main>
        {route.view === "gallery" ? (
          <GalleryPage route={route} go={go} />
        ) : route.view === "health" ? (
          <Health go={go} />
        ) : route.view === "search" ? (
          <Search go={go} />
        ) : (
          <VideoPage route={route} go={go} />
        )}
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
