import { useState } from "react";
import { api } from "../../api";
import { Card } from "../shared/common";

const EXAMPLES = [
  "What is this application?",
  "What services exist?",
  "Which services are critical?",
  "What APIs exist?",
  "What telemetry exists?",
  "What failure scenarios are documented?",
];

/** Questions about the scanned application, answered only from what the scan found. */
export function KnowledgePage() {
  const [question, setQuestion] = useState("");
  const [answers, setAnswers] = useState<{ q: string; a: string }[]>([]);
  const [error, setError] = useState<string | null>(null);

  const ask = async (q: string) => {
    if (!q.trim()) return;
    setError(null);
    try {
      const r = await api.askKnowledge(q);
      setAnswers((prev) => [{ q, a: r.answer }, ...prev]);
      setQuestion("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Card question="What does the system know about the application?" title="Knowledge">
      <form className="filters" onSubmit={(e) => { e.preventDefault(); void ask(question); }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)} aria-label="Ask about the application"
               placeholder="e.g. What does payment depend on?" />
        <button type="submit" disabled={!question.trim()}>Ask</button>
      </form>
      <div className="chips">
        {EXAMPLES.map((q) => <button key={q} type="button" className="chip" onClick={() => void ask(q)}>{q}</button>)}
      </div>
      {error && <div className="alert alert-danger" role="alert">{error}</div>}
      <div className="stack" style={{ marginTop: "0.8rem" }}>
        {answers.map((a, n) => (
          <div key={n} className="panel">
            <div className="panel-title">{a.q}</div>
            <p>{a.a}</p>
          </div>
        ))}
        {answers.length === 0 && <p className="muted small">Answers come from the application scan only. Scan an application first on the Applications page.</p>}
      </div>
    </Card>
  );
}
