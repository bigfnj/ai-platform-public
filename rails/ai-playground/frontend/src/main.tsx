import React from "react";
import ReactDOM from "react-dom/client";
import AiPlaygroundModule from "./module";
import "./standalone.css";

// Standalone dev shell only. In production the platform shell mounts ./module and owns
// the theme; for local dev, mirror the OS preference onto <html data-theme> so both
// light and dark render.
const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
document.documentElement.setAttribute("data-theme", prefersDark ? "dark" : "light");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AiPlaygroundModule />
  </React.StrictMode>
);
