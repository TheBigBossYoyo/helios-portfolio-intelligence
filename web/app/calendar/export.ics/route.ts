import { getCalendar } from "@/lib/api";
import { toIcs } from "@/lib/ics";

export const dynamic = "force-dynamic";

/**
 * The calendar as an .ics file: every upcoming event, or one with `?id=`. Opening it on a phone
 * offers to add the events to its own calendar.
 */
export async function GET(request: Request): Promise<Response> {
  const result = await getCalendar();
  if (!result.ok) {
    return new Response("Calendar unavailable", { status: 502 });
  }
  const id = new URL(request.url).searchParams.get("id");
  const events = result.data.events.filter((event) => (id ? event.id === id : !event.past));
  if (id && events.length === 0) {
    return new Response("No such event", { status: 404 });
  }
  const stamp = `${new Date().toISOString().replace(/[-:]/g, "").slice(0, 15)}Z`;
  return new Response(toIcs(events, stamp), {
    headers: {
      "content-type": "text/calendar; charset=utf-8",
      "content-disposition": `attachment; filename="${id ? "helios-event" : "helios-calendar"}.ics"`,
      "cache-control": "no-store",
    },
  });
}
