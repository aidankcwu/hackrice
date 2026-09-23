/**
 * The app's version, from `phone/package.json`. Import it from server components
 * only (the pages) and pass it down, so the manifest never ships to the browser.
 */
import manifest from "../../package.json";

export const APP_VERSION: string = manifest.version;
