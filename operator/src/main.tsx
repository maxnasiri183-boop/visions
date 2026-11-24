import { GalleryPage } from "./Gallery";
import { Health } from "./Health";
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Route, View } from "./types";
import { VideoPage } from "./Video";
import { Search } from "./Search";
import "./style.css";
import { labels, ShellHeader } from "./Shell";
function read(): Route {
  const q = new URLSearchParams(location.search),
    view = q.get("view") as View;
  return {
    view: document.documentElement.dataset.galleryFixture
      ? "gallery"
      : Object.hasOwn(labels, view)
        ? view
        : "live",
    source: q.get("source") || undefined,
    occ: q.get("occ") || undefined,
    frame: q.has("frame") ? Number(q.get("frame")) : undefined,
    face: q.get("face") || undefined,
    q: q.get("q") || undefined,
    status: q.get("status") || undefined,
    after: q.get("after") || undefined,
    person: q.get("person") || undefined,
    version: q.get("version") || undefined,
    originOcc: q.get("originOcc") || undefined,
    originFrame: q.has("originFrame")
      ? Number(q.get("originFrame"))
      : undefined,
    originFace: q.get("originFace") || undefined,
    originView: q.has("originView")
      ? q.get("originView") === "live"
        ? "live"
        : "recordings"
      : undefined,
  };
}
function App() {
  const [route, setRoute] = useState(read);
  function go(r: Route) {
    const q = new URLSearchParams();
    Object.entries(r).forEach(([k, v]) => {
      if (v !== undefined) q.set(k, String(v));
    });
    history.pushState({ visions: true }, "", "?" + q);
    setRoute(r);
  }
  useEffect(() => {
    const pop = () => setRoute(read());
    window.addEventListener("popstate", pop);
    return () => window.removeEventListener("popstate", pop);
  }, []);
  useEffect(() => {
    document.title = labels[route.view] + " | Visions";
  }, [route.view]);
  return (
    <>
      {document.documentElement.dataset.galleryFixture ? (
        <header className="top">
          <strong dir="ltr">Visions</strong>
          <span>گالری آزمایشی · داده‌های ساختگی برای آزمون کارایی</span>
        </header>
      ) : (
        <ShellHeader route={route} go={go} />
      )}
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
