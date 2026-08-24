import { createRoot } from "react-dom/client";
import React from "react";

const releaseSha = import.meta.env.VITE_RELEASE_SHA || "unknown";

function App() {
  return React.createElement(
    "main",
    null,
    React.createElement("h1", null, "Release pipeline acceptance React app"),
    React.createElement("p", { "data-release-sha": releaseSha }, `release=${releaseSha}`),
  );
}

createRoot(document.getElementById("root")).render(React.createElement(App));
