import { createRoot } from "react-dom/client";
import App from "./App";
import { storedTheme } from "./lib/util";
import "./styles.css";
import "./play.css";

const saved = storedTheme();
if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);

createRoot(document.getElementById("root")!).render(<App />);
