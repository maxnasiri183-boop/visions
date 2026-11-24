import { GalleryPage } from "./Gallery";
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Route, View } from "./types";
import "./style.css";
const labels: Partial<Record<View, string>> = {
  gallery: "گالری مرجع",
};
function read(): Route {
  const q = new URLSearchParams(location.search),
    view = q.get("view") as View;
  return {
    view: Object.hasOwn(labels, view) ? view : "gallery",
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
        <GalleryPage route={route} go={go} />
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
