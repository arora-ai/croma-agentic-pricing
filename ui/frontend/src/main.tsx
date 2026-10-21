import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initAuth } from "./auth";
import "./styles.css";

// With sign-in switched on (auth.ts) this waits for the Microsoft sign-in to complete; without it, it returns at once.
initAuth()
  .catch((err) => console.error("Sign-in failed", err))
  .then(() =>
    ReactDOM.createRoot(document.getElementById("root")!).render(
      <React.StrictMode>
        <App />
      </React.StrictMode>,
    ),
  );
