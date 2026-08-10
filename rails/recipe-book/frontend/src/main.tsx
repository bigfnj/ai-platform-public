// Standalone dev entry only (npm run dev). In the platform shell the exposed
// ./module is mounted directly, so this file is not part of the remote.
import React from "react";
import { createRoot } from "react-dom/client";
import RecipeBookModule from "./module";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RecipeBookModule />
  </React.StrictMode>,
);
