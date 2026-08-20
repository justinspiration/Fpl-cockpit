/* De assistent van het FPL-dashboard.
 *
 * Waarom dit hier staat en niet in de pagina: een API-sleutel in een webpagina
 * is een sleutel die iedereen kan lezen die de pagina opent. Dan praat een
 * vreemde op jouw rekening. Deze functie draait op de server van Netlify; de
 * sleutel staat daar in een omgevingsvariabele en komt nooit in de browser.
 *
 * Aanzetten: zet in Netlify onder Site settings → Environment variables een
 * variabele ANTHROPIC_API_KEY met je sleutel erin. Meer hoeft er niet.
 */
const MODEL = "claude-sonnet-5";

export default async (request) => {
  if (request.method !== "POST") {
    return new Response("Alleen POST", { status: 405 });
  }
  const sleutel = Netlify.env.get("ANTHROPIC_API_KEY");
  if (!sleutel) {
    // Geen sleutel ingesteld: de pagina valt netjes terug op zijn eigen
    // patroonmotor. Daarom een gewone 200 met een lege uitkomst.
    return Response.json({ antwoord: null, reden: "geen sleutel ingesteld" });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return Response.json({ fout: "ongeldige aanvraag" }, { status: 400 });
  }
  const { vraag, context, historie } = body || {};
  if (!vraag || typeof vraag !== "string") {
    return Response.json({ fout: "geen vraag" }, { status: 400 });
  }

  const systeem = [
    "Je bent de assistent van Justins Fantasy Premier League-dashboard voor seizoen 2026/27.",
    "Antwoord in het Nederlands, kort en concreet, in Justins toon: zakelijk en to the point.",
    "Getallen boven de tien schrijf je als cijfer.",
    "",
    "Harde regel: verzin geen data. Je krijgt hieronder Justins actuele situatie.",
    "Weet je iets niet, zeg dat dan en verwijs naar het tabblad waar het staat.",
    "Noem nooit een cijfer dat je niet in de context ziet staan.",
    "Wees een kritische sparringpartner: Justin is Manchester City-fan en wil dat",
    "je die voorkeur tegenspreekt als de cijfers dat rechtvaardigen.",
    "",
    "Zijn situatie op dit moment:",
    JSON.stringify(context ?? {}, null, 1),
  ].join("\n");

  const berichten = [];
  for (const m of Array.isArray(historie) ? historie.slice(-8) : []) {
    if (!m || !m.tekst) continue;
    berichten.push({ role: m.rol === "jij" ? "user" : "assistant", content: String(m.tekst) });
  }
  if (berichten.at(-1)?.role !== "user" || berichten.at(-1)?.content !== vraag) {
    berichten.push({ role: "user", content: vraag });
  }

  try {
    const res = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": sleutel,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 900,
        system: systeem,
        messages: berichten,
      }),
    });
    if (!res.ok) {
      const tekst = await res.text();
      return Response.json({ antwoord: null, reden: `API gaf ${res.status}: ${tekst.slice(0, 160)}` });
    }
    const j = await res.json();
    const tekst = (j.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
    return Response.json({ antwoord: tekst || null, model: MODEL });
  } catch (e) {
    return Response.json({ antwoord: null, reden: String(e).slice(0, 160) });
  }
};

export const config = { path: "/api/chat" };
