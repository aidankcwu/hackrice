import type { ProtocolToday } from "./types";

/**
 * Three days of protocol statuses in the shapes docs/API.md "The protocol"
 * documents: `GET /api/protocol/today` and `GET /api/protocol/export.csv?days=14`
 * (Python `csv.writer`: `\r\n` rows, a field quoted only when it holds a comma).
 * "Omega-3, with food" runs on weekdays only, so Sunday 2026-09-20 has no row.
 * Every item was added on 2026-09-20, so the eleven days before it have none.
 */

export const protocolTodayFixture: ProtocolToday = {
  day: "2026-09-22",
  items: [
    { id: "pi_morning", name: "Morning dose", kind: "dose", window_start: "07:00", window_end: "10:00", days: [0, 1, 2, 3, 4, 5, 6], created_t: 1758340000.0, status: "seen", seen_t: 1758526500.0, evidence_ref: "d_0012/f_00001742", updated_t: 1758526500.0 },
    { id: "pi_omega", name: "Omega-3, with food", kind: "meal", window_start: "11:30", window_end: "14:00", days: [0, 1, 2, 3, 4], created_t: 1758340000.0, status: "waiting", seen_t: null, evidence_ref: null, updated_t: null },
    { id: "pi_evening", name: "Evening dose", kind: "dose", window_start: "19:00", window_end: "22:00", days: [0, 1, 2, 3, 4, 5, 6], created_t: 1758340000.0, status: "waiting", seen_t: null, evidence_ref: null, updated_t: null },
  ],
};

export const protocolCsvFixture = [
  "day,item_id,name,kind,window_start,window_end,status,seen_t,evidence_ref,updated_t",
  "2026-09-20,pi_morning,Morning dose,dose,07:00,10:00,seen,1758353700.0,d_0003/f_00000410,1758353700.0",
  "2026-09-20,pi_evening,Evening dose,dose,19:00,22:00,missed,,,1758405600.0",
  "2026-09-21,pi_morning,Morning dose,dose,07:00,10:00,done,,,1758441000.0",
  "2026-09-21,pi_omega,\"Omega-3, with food\",meal,11:30,14:00,missed,,,1758477600.0",
  "2026-09-21,pi_evening,Evening dose,dose,19:00,22:00,undone,,,1758499200.0",
  "2026-09-22,pi_morning,Morning dose,dose,07:00,10:00,seen,1758526500.0,d_0012/f_00001742,1758526500.0",
  "2026-09-22,pi_omega,\"Omega-3, with food\",meal,11:30,14:00,waiting,,,",
  "2026-09-22,pi_evening,Evening dose,dose,19:00,22:00,waiting,,,",
  "",
].join("\r\n");
