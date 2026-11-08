import { createRoot } from "react-dom/client";
import FpgaApp from "./FpgaApp";
import { storedTheme } from "../lib/util";
import "../styles.css";
import "./fpga.css";

const saved = storedTheme();
if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);

createRoot(document.getElementById("root")!).render(<FpgaApp />);
