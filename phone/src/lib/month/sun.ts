/**
 * Sunrise and sunset for a date and place, NOAA's general solar-position
 * approximation (good to a minute or two at Houston's latitude). Returned as
 * minutes after local midnight.
 */
export function sunTimes(date: string, lat: number, lon: number, utcOffsetHours: number): { sunrise: number; sunset: number } {
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
  const rad = Math.PI / 180;
  const cosHa = Math.cos(90.833 * rad) / (Math.cos(lat * rad) * Math.cos(decl)) - Math.tan(lat * rad) * Math.tan(decl);
  const ha = Math.acos(Math.max(-1, Math.min(1, cosHa))) / rad;
  const sunriseUtc = 720 - 4 * (lon + ha) - eqTime;
  const sunsetUtc = 720 - 4 * (lon - ha) - eqTime;
  const offset = utcOffsetHours * 60;
  return { sunrise: Math.round(sunriseUtc + offset), sunset: Math.round(sunsetUtc + offset) };
}
