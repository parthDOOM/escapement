import { useJson } from "../lib/util";

type Item = { name: string; status: string; secs?: number | null; text?: string };
type Check = { name: string; status: string; detail?: string; items?: Item[] };
type Wf = {
  workflow: string; what: string; running: boolean;
  conclusion?: string; sha?: string; date?: string; url?: string; checks?: Check[];
};
type Ci = { repo: string; generated: string; workflows: Wf[] };

const TITLE: Record<string, string> = {
  formal: "Formal proofs", equivalence: "Netlist equals RTL", mutation: "Planted faults",
  test: "Tests and co-simulation", gds: "Layout and sign-off",
};

const when = (iso?: string) =>
  iso ? new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";

function Status({ wf }: { wf: Wf }) {
  if (!wf.conclusion) return <span className="pill wait">{wf.running ? "running" : "no run yet"}</span>;
  const ok = wf.conclusion === "success";
  return <span className={"pill " + (ok ? "ok" : "bad")}>{ok ? "passed" : wf.conclusion}</span>;
}

function Mark({ s }: { s: string }) {
  const ok = s === "pass" || s === "PASS";
  return <span className={"mark " + (ok ? "ok" : "bad")} aria-label={ok ? "passed" : "failed"}>{ok ? "✓" : "✗"}</span>;
}

/** The latest master run of each verification workflow, as the site's build found it. */
export default function CiResults() {
  const [ci, err] = useJson<Ci>("data/ci.json");
  if (err) return (
    <p className="ci-note">The results of the latest CI runs are added when the site is built by CI;
      this copy was built without them. They are on <a href="https://github.com/parthDOOM/escapement/actions">GitHub Actions</a>.</p>
  );
  if (!ci) return null;
  const repo = "https://github.com/" + ci.repo;
  return (
    <div className="ci">
      <div className="ci-grid">
        {ci.workflows.map((wf) => (
          <div className="panel ci-wf" key={wf.workflow}>
            <div className="ci-head">
              <h3>{TITLE[wf.workflow] ?? wf.workflow}</h3>
              <Status wf={wf} />
            </div>
            <p className="ci-what">{wf.what}</p>
            {wf.conclusion && (
              <p className="ci-meta">
                <a href={`${repo}/commit/${wf.sha}`}>{wf.sha?.slice(0, 7)}</a> · {when(wf.date)} · <a href={wf.url}>run log</a>
                {wf.running && <> · a newer run is in progress</>}
              </p>
            )}
            {!!wf.checks?.length && (
              <ul className="ci-checks">
                {wf.checks.map((c, i) => (
                  <li key={i}>
                    <Mark s={c.status} />
                    <div>
                      <b>{c.name}</b>{c.detail && <span> {c.detail}</span>}
                      {!!c.items?.length && (
                        <details>
                          <summary>{c.items.length} tasks</summary>
                          <ul className="ci-items">
                            {c.items.map((it, j) => (
                              <li key={j} title={it.text}>
                                <Mark s={it.status} /> <span>{it.name}</span>
                                {it.secs != null && <span className="secs">{it.secs} s</span>}
                              </li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>
      <p className="ci-note">Collected from GitHub Actions when this page was built, {when(ci.generated)}.
        A failed run shows here as failed; nothing is filtered.</p>
    </div>
  );
}
