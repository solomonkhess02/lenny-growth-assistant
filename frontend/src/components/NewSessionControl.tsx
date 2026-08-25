/**
 * Provider selection — which exists ONLY here, at session creation.
 *
 * A session's provider is immutable once it exists: there is no route that
 * mutates it and no per-message override, so offering a "switch provider"
 * control on an open session would be offering something the system cannot do.
 * Changing provider means starting a new session, and this control says so.
 *
 * Health is shown per provider before the choice is made, so an unreachable
 * provider is visible up front rather than discovered as a failed turn. The
 * unreachable option is NOT hidden or auto-replaced -- it is shown, marked,
 * and still selectable, because silently steering the user to a working
 * provider is substitution by another name.
 */
import { useEffect, useState } from "react";

import { checkProvider, getProviders } from "../api";
import type { ProviderHealth } from "../types";

export default function NewSessionControl({
  onCreate, disabled,
}: {
  onCreate: (provider: string) => void;
  disabled: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [available, setAvailable] = useState<string[]>([]);
  const [configured, setConfigured] = useState<string | null>(null);
  const [health, setHealth] = useState<Record<string, ProviderHealth>>({});

  useEffect(() => {
    if (!open) return;
    let cancelled = false;

    getProviders()
      .then((p) => {
        if (cancelled) return;
        setAvailable(p.available);
        setConfigured(p.selected);
        for (const name of p.available) {
          checkProvider(name)
            .then((h) => {
              if (!cancelled) setHealth((prev) => ({ ...prev, [name]: h }));
            })
            .catch(() => undefined); // a failed probe is reported by absence
        }
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
    };
  }, [open]);

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} disabled={disabled}>
        New session
      </button>
    );
  }

  return (
    <div className="picker" role="dialog" aria-label="Start a new session">
      <div className="picker-head">
        Start a session on:
        <button className="ghost" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
      <p className="picker-note">
        The provider is fixed for the life of a session. To use a different one, start
        another session — an existing session is never switched over.
      </p>
      <ul className="picker-list">
        {available.map((name) => {
          const h = health[name];
          const ready = h?.reachable && h?.model_available;
          const state = h === undefined ? "checking…" : ready ? "ready" : "unavailable";
          return (
            <li key={name}>
              <button
                className="picker-option"
                onClick={() => {
                  setOpen(false);
                  onCreate(name);
                }}
              >
                <span className="picker-name">
                  {name}
                  {name === configured && <span className="tag">default</span>}
                </span>
                <span className="picker-model">{h?.model ?? ""}</span>
                <span
                  className={`pill ${h === undefined ? "" : ready ? "ok" : "warn"}`}
                  title={h?.detail ?? ""}
                >
                  {state}
                </span>
              </button>
              {h && !ready && h.detail && <div className="picker-detail">{h.detail}</div>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
