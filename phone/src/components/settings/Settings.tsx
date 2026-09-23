"use client";

import { useId, type ReactNode } from "react";
import { API_BASE } from "@/lib/api";
import { VOICE_KEY, WIND_DOWN_DEFAULT, WIND_DOWN_KEY, useSetting } from "@/lib/settings";

const ROW = "flex min-h-[52px] items-center gap-4 border-t-[0.5px] border-line py-2";
const FOCUS = "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ink";

/**
 * SettingsView, pushed from the gear: a grouped list. Voice and Wind‑down are
 * kept on this phone; About names the build and the backend it talks to.
 */
export function Settings({ version }: { version: string }) {
  const [voice, setVoice] = useSetting(VOICE_KEY, "on");
  const [windDown, setWindDown] = useSetting(WIND_DOWN_KEY, WIND_DOWN_DEFAULT);

  return (
    <div className="mt-4">
      <Section title="Voice">
        <label className={`${ROW} cursor-pointer ${FOCUS}`}>
          <span className="type-body min-w-0 flex-1 text-text">Whisper through the glasses</span>
          <input
            type="checkbox"
            role="switch"
            checked={voice === "on"}
            onChange={(event) => setVoice(event.target.checked ? "on" : "off")}
            className="sr-only"
          />
          <Switch on={voice === "on"} />
        </label>
      </Section>

      <Section title="Wind‑down" footer="Not connected yet">
        <label className={`${ROW} ${FOCUS}`}>
          <span className="type-body min-w-0 flex-1 text-text">Time</span>
          <input
            type="time"
            value={windDown}
            onChange={(event) => {
              if (event.target.value) setWindDown(event.target.value);
            }}
            className="type-body bg-transparent text-right text-text tabular-nums focus:outline-none"
          />
        </label>
      </Section>

      <Section title="About">
        <dl className="m-0">
          <InfoRow label="Version" value={<span className="tabular-nums">{version}</span>} />
          <InfoRow label="Backend" value={<BreakableUrl url={API_BASE} />} />
        </dl>
      </Section>
    </div>
  );
}

/** A group: a muted heading, rows between full-width hairlines, an optional muted footer. */
function Section({ title, footer, children }: { title: string; footer?: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="mt-section first:mt-0">
      <h2 id={id} className="type-secondary m-0 mb-2 text-muted">
        {title}
      </h2>
      <div className="border-b-[0.5px] border-line">{children}</div>
      {footer ? <p className="type-caption m-0 mt-2 text-muted">{footer}</p> : null}
    </section>
  );
}

/** Label and read-only value on one line; at large text the value drops under the label, right-aligned. */
function InfoRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={`${ROW} flex-wrap gap-y-0`}>
      <dt className="type-body text-text">{label}</dt>
      <dd className="type-body m-0 ml-auto max-w-full text-right text-muted [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}

/** "http://10.0.0.5:8010", allowed to wrap after the scheme and before the port rather than mid-host. */
function BreakableUrl({ url }: { url: string }) {
  const [, scheme = "", host = url, port = ""] = /^([a-z]+:\/\/)?([^:/]+)(.*)$/i.exec(url) ?? [];
  return (
    <>
      {scheme}
      <wbr />
      {host}
      <wbr />
      {port}
    </>
  );
}

/**
 * The iOS switch, 51 × 31, drawn only: the checkbox beside it is what VoiceOver
 * and the keyboard reach. On is the ink fill, the same as the primary button; the
 * data colours stay reserved for data.
 */
function Switch({ on }: { on: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={`relative h-[31px] w-[51px] shrink-0 rounded-full transition-colors duration-200 ${
        on ? "bg-ink" : "bg-surface-2"
      }`}
    >
      <span
        className={`absolute top-[2px] left-[2px] size-[27px] rounded-full border-[0.5px] border-[var(--glass-edge)] transition-transform duration-200 ${
          on ? "translate-x-[20px] bg-page" : "bg-[light-dark(#ffffff,#f5f5f7)]"
        }`}
      />
    </span>
  );
}
