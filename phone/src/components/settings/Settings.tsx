"use client";

import { useId, useSyncExternalStore, type ReactNode } from "react";
import { SwitchRow } from "@/components/concierge/SwitchRow";
import { Field, InsetList, ListRow } from "@/components/ui";
import { accessToken, apiBase } from "@/lib/runtime";
import { VOICE_KEY, WIND_DOWN_DEFAULT, WIND_DOWN_KEY, useSetting } from "@/lib/settings";

/**
 * The backend address and whether a bearer token rides on every request, both
 * resolved in the browser (src/lib/runtime.ts: a hosted page derives them from
 * its URL). The prerendered HTML shows the build's defaults; the client swaps
 * in the real values without a hydration mismatch. The token itself never shows.
 */
const noSubscribe = () => () => {};
const serverBase = () => apiBase();
const serverTokenSet = () => Boolean(process.env.NEXT_PUBLIC_API_TOKEN);
const clientTokenSet = () => accessToken() !== null;

/**
 * Settings ("Account" in the menu), pushed from the gear: inset grouped lists.
 * Voice and Wind‑down are kept on this phone; Appearance follows the device;
 * Backend names the address the app talks to and whether a token is set;
 * About names the build.
 */
export function Settings({ version }: { version: string }) {
  const [voice, setVoice] = useSetting(VOICE_KEY, "on");
  const [windDown, setWindDown] = useSetting(WIND_DOWN_KEY, WIND_DOWN_DEFAULT);
  const windDownId = useId();
  const backend = useSyncExternalStore(noSubscribe, apiBase, serverBase);
  const tokenSet = useSyncExternalStore(noSubscribe, clientTokenSet, serverTokenSet);

  return (
    <div className="mt-4 flex flex-col gap-section">
      <InsetList label="Voice">
        <SwitchRow
          id="voice"
          label="Whisper through the glasses"
          checked={voice === "on"}
          onChange={(on) => setVoice(on ? "on" : "off")}
        />
      </InsetList>

      <div>
        <InsetList label="Wind‑down">
          <FieldRow>
            <Field
              id={windDownId}
              label="Time"
              type="time"
              value={windDown}
              onChange={(value) => {
                if (value) setWindDown(value);
              }}
            />
          </FieldRow>
        </InsetList>
        <p className="type-caption m-0 mt-2 px-4 text-muted">Not connected yet</p>
      </div>

      <InsetList label="Appearance">
        <ListRow title="Appearance" trailing={<Value>Follows the device</Value>} />
      </InsetList>

      <InsetList label="Backend">
        <ListRow
          title="Address"
          trailing={
            <Value>
              <BreakableUrl url={backend} />
            </Value>
          }
        />
        <ListRow title="Token" trailing={<Value>{tokenSet ? "Set" : "Not set"}</Value>} />
      </InsetList>

      <InsetList label="About">
        <ListRow title="Version" trailing={<Value>{version}</Value>} />
      </InsetList>
    </div>
  );
}

/**
 * A Field as one row of an InsetList: the list's surface bounds the row, so
 * the field's own bottom hairline is dropped, and its focus ring is drawn
 * inside the clipped corners.
 */
function FieldRow({ children }: { children: ReactNode }) {
  return <li className="px-4 [&>label]:after:hidden [&>label]:has-focus-visible:-outline-offset-2">{children}</li>;
}

/** A read-only value at the right of a row; a long one wraps inside its 60% rather than squeezing the title. */
function Value({ children }: { children: ReactNode }) {
  return <span className="type-body max-w-[60%] shrink-0 text-right text-muted tabular-nums [overflow-wrap:anywhere]">{children}</span>;
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
