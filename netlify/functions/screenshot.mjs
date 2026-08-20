/* Herkent de vijftien spelers op een schermafdruk van je FPL-team.
 *
 * Waarom dit op de server draait en niet in de pagina: tekstherkenning in de
 * browser vraagt een OCR-bibliotheek van enkele megabytes plus taalbestanden,
 * en die mogen niet geladen worden in een gepubliceerd artifact. Bovendien
 * herkent losse OCR wel letters maar geen spelers: "SON" op een shirt kan de
 * naam zijn of de club. Een model dat de hele afbeelding ziet, leest het
 * elftal zoals jij het leest — inclusief wie op de bank staat.
 *
 * Aanzetten: dezelfde ANTHROPIC_API_KEY als de chat. Meer is er niet nodig.
 */
const MODEL = "claude-sonnet-5";
const MAX_BYTES = 5 * 1024 * 1024;

export default async (request) => {
  if (request.method !== "POST") return new Response("Alleen POST", { status: 405 });

  const sleutel = Netlify.env.get("ANTHROPIC_API_KEY");
  if (!sleutel) {
    return Response.json({ spelers: null, reden: "geen sleutel ingesteld" });
  }

  let body;
  try { body = await request.json(); }
  catch { return Response.json({ fout: "ongeldige aanvraag" }, { status: 400 }); }

  const { afbeelding, type } = body || {};
  if (!afbeelding || typeof afbeelding !== "string") {
    return Response.json({ fout: "geen afbeelding" }, { status: 400 });
  }
  if (afbeelding.length * 0.75 > MAX_BYTES) {
    return Response.json({ fout: "afbeelding te groot (max 5 MB)" }, { status: 413 });
  }
  const mime = ["image/png", "image/jpeg", "image/webp", "image/gif"].includes(type)
    ? type : "image/png";

  const opdracht = [
    "Op deze schermafdruk staat een Fantasy Premier League-team.",
    "Lees de spelersnamen af zoals ze er staan, in leesvolgorde: eerst de basiself",
    "van keeper naar aanval, daarna de vier bankspelers.",
    "",
    "Geef UITSLUITEND geldige JSON terug, zonder uitleg eromheen:",
    '{"basis":["naam", ...11], "bank":["naam", ...4], "aanvoerder":"naam of null",',
    ' "vervanger":"naam of null", "zeker":true of false}',
    "",
    "Regels:",
    "- Neem de naam over zoals hij op de afbeelding staat. Vul niets aan en gok niet.",
    "- Kun je een naam niet lezen, zet dan null op die plek in de lijst.",
    "- Staat er geen duidelijk elftal op, geef dan lege lijsten en zeker:false.",
    "- De aanvoerder is herkenbaar aan een C, de vervanger aan een V of VC.",
  ].join("\n");

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
        max_tokens: 700,
        messages: [{
          role: "user",
          content: [
            { type: "image", source: { type: "base64", media_type: mime, data: afbeelding } },
            { type: "text", text: opdracht },
          ],
        }],
      }),
    });
    if (!res.ok) {
      const t = await res.text();
      return Response.json({ spelers: null, reden: `API gaf ${res.status}: ${t.slice(0, 160)}` });
    }
    const j = await res.json();
    const tekst = (j.content || []).filter(b => b.type === "text").map(b => b.text).join("").trim();
    const m = tekst.match(/\{[\s\S]*\}/);
    if (!m) return Response.json({ spelers: null, reden: "geen leesbaar antwoord" });
    let uit;
    try { uit = JSON.parse(m[0]); }
    catch { return Response.json({ spelers: null, reden: "antwoord was geen geldige JSON" }); }
    return Response.json({ spelers: uit, model: MODEL });
  } catch (e) {
    return Response.json({ spelers: null, reden: String(e).slice(0, 160) });
  }
};

export const config = { path: "/api/screenshot" };
