/**
 * The sun for a date and place, NOAA's general solar-position approximation
 * (good to a minute or two at Houston's latitude): sunrise and sunset in
 * minutes after local midnight, and a clear-sky UV estimate.
 */
function solar(date: string): { eqTime: number; decl: number } {
  const [y, m, d] = date.split("-").map(Number);
  const start = Date.UTC(y, 0, 1);
  const dayOfYear = Math.floor((Date.UTC(y, m - 1, d) - start) / 86_400_000) + 1;
  const g = ((2 * Math.PI) / 365) * (dayOfYear - 1);
  const eqTime =
    229.18 *
    (0.000075 + 0.001868 * Math.cos(g) - 0.032077 * Math.sin(g) - 0.014615 * Math.cos(2 * g) - 0.040849 * Math.sin(2 * g));
  const decl =
    0.006918 -
    0.399912 * Math.cos(g) +
    0.070257 * Math.sin(g) -
    0.006758 * Math.cos(2 * g) +
    0.000907 * Math.sin(2 * g) -
    0.002697 * Math.cos(3 * g) +
    0.00148 * Math.sin(3 * g);
  return { eqTime, decl };
}

const rad = Math.PI / 180;

/**
 * Clear-sky UV index at a local minute, from the sun's height alone:
 * 12.5 · cos(zenith)^2.42 (Madronich's fit, ozone near 300 DU). No clouds, no
 * haze, so it is the ceiling of the day's UV, an estimate, never a reading.
 */
export function uvIndex(date: string, minutes: number, lat: number, lon: number, utcOffsetHours: number): number {
  const { eqTime, decl } = solar(date);
  const trueSolar = minutes + eqTime + 4 * lon - 60 * utcOffsetHours;
  const hourAngle = trueSolar / 4 - 180;
  const cosZenith = Math.sin(lat * rad) * Math.sin(decl) + Math.cos(lat * rad) * Math.cos(decl) * Math.cos(hourAngle * rad);
  return cosZenith > 0 ? 12.5 * cosZenith ** 2.42 : 0;
}

export function sunTimes(date: string, lat: number, lon: number, utcOffsetHours: number): { sunrise: number; sunset: number } {
  const { eqTime, decl } = solar(date);
  const cosHa = Math.cos(90.833 * rad) / (Math.cos(lat * rad) * Math.cos(decl)) - Math.tan(lat * rad) * Math.tan(decl);
  const ha = Math.acos(Math.max(-1, Math.min(1, cosHa))) / rad;
  const sunriseUtc = 720 - 4 * (lon + ha) - eqTime;
  const sunsetUtc = 720 - 4 * (lon - ha) - eqTime;
  const offset = utcOffsetHours * 60;
  return { sunrise: Math.round(sunriseUtc + offset), sunset: Math.round(sunsetUtc + offset) };
}
