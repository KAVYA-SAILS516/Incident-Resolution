import type { ThemeChoice } from "../../lib/theme";
import type { Health } from "../../types";

export type Tab = "dashboard" | "incidents" | "applications" | "activity" | "knowledge";

const TABS: { key: Tab; label: string }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "incidents", label: "Incidents" },
  { key: "applications", label: "Applications" },
  { key: "activity", label: "Agent Activity" },
  { key: "knowledge", label: "Knowledge" },
];

const THEMES: { key: ThemeChoice; label: string; icon: string }[] = [
  { key: "light", label: "Light", icon: "☀" },
  { key: "dark", label: "Dark", icon: "☾" },
];

export function Header({ tab, onTab, health, theme, onTheme }: {
  tab: Tab; onTab: (t: Tab) => void; health: Health | null; theme: ThemeChoice; onTheme: (t: ThemeChoice) => void;
}) {
  const state = !health ? "unknown" : health.llm_configured ? "available" : "unavailable";
  const label = { available: "Configured", unavailable: "Not configured", unknown: "Backend unreachable" }[state];
  return (
    <header className="app-header">
      <div className="header-row">
        <h1>AI Incident Resolution</h1>
        <div className="header-tools">
          <div className="theme-switch" role="radiogroup" aria-label="Color theme">
            {THEMES.map((t) => (
              <button key={t.key} type="button" role="radio" aria-checked={theme === t.key}
                      className={theme === t.key ? "active" : ""} onClick={() => onTheme(t.key)} title={`${t.label} theme`}>
                <span aria-hidden>{t.icon}</span> {t.label}
              </button>
            ))}
          </div>
          <span className={`llm-pill llm-${state}`}
                title="Whether the backend has Vertex AI settings for the two Google ADK agents.">
            <span className="dot" aria-hidden /> Vertex AI {health?.model ?? "Gemini"}: {label}
          </span>
        </div>
      </div>
      <nav className="tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button key={t.key} type="button" className={tab === t.key ? "tab active" : "tab"} onClick={() => onTab(t.key)}>
            {t.label}
          </button>
        ))}
      </nav>
    </header>
  );
}
