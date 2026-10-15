/**
 * Wake-on-demand endpoint for the Discord transcription bot.
 *
 * A paused Upstash Box cannot hear Discord, so something always-on has to
 * observe the trigger. This Worker is that something: free, serverless, and
 * with two jobs -- prove the request came from Discord, and resume the Box.
 * The Box's initCommand then brings the bot back up.
 *
 * Measured: a paused box resumes and has the bot running 23 seconds later.
 *
 * GET  /                    health + key diagnostic (never leaks the key)
 * GET  /?diag=<DIAG_TOKEN>  performs a wake and returns the upstream status
 * POST /                    Discord interactions: PING, and /wake
 *
 * Secrets: DISCORD_PUBLIC_KEY, UPSTASH_BOX_API_KEY, BOX_ID, DIAG_TOKEN.
 */

const BOX_API = "https://us-east-1.box.upstash.com";

function hexToBytes(hex) {
  // Trim first: a secret piped from a shell can carry a trailing newline,
  // and an odd-length string would throw on the typed-array size.
  const clean = String(hex).trim();
  if (clean.length % 2 !== 0) throw new Error("odd-length hex string");
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = parseInt(clean.substr(i * 2, 2), 16);
  }
  return bytes;
}

async function verifyFromDiscord(request, rawBody, env) {
  const signature = request.headers.get("x-signature-ed25519");
  const timestamp = request.headers.get("x-signature-timestamp");
  if (!signature || !timestamp) return false;
  // Workers expose Ed25519 as NODE-ED25519 rather than a plain name.
  const key = await crypto.subtle.importKey(
    "raw",
    hexToBytes(env.DISCORD_PUBLIC_KEY),
    { name: "NODE-ED25519", namedCurve: "NODE-ED25519" },
    false,
    ["verify"],
  );
  return crypto.subtle.verify(
    { name: "NODE-ED25519" },
  key,
    hexToBytes(signature),
    new TextEncoder().encode(timestamp + rawBody),
  );
}

/** Resume the box. Returns the upstream status so failures are visible. */
async function wake(env) {
  const url = BOX_API + "/v2/box/" + String(env.BOX_ID).trim() + "/resume";
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "X-Box-Api-Key": String(env.UPSTASH_BOX_API_KEY).trim(),
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    const detail = (await res.text()).slice(0, 300);
    const ok = res.ok || res.status === 409;
    console.log("wake ->", res.status, detail);
    return { ok: ok, status: res.status, detail: detail };
  } catch (err) {
    console.log("wake threw:", String(err));
    return { ok: false, status: 0, detail: String(err) };
  }
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "GET") {
      const url = new URL(request.url);
      const diag = url.searchParams.get("diag");
      if (diag && env.DIAG_TOKEN && diag === String(env.DIAG_TOKEN).trim()) {
        // Diagnostic: run the real wake and report what Upstash answered.
        return Response.json(await wake(env));
      }

      // Health: proves the key imports as a 32-byte Ed25519 key without
      // revealing it, which is the first thing to check when Discord says
      // the endpoint could not be verified.
      let importOk = "yes";
      let keyBytes = -1;
      try {
        const raw = hexToBytes(env.DISCORD_PUBLIC_KEY);
        keyBytes = raw.length;
        await crypto.subtle.importKey(
          "raw",
          raw,
          { name: "NODE-ED25519", namedCurve: "NODE-ED25519" },
          false,
          ["verify"],
        );
      } catch (err) {
        importOk = "error: " + err.message;
      }
      return Response.json({
        service: "discord-transcriber-wake",
        key_bytes: keyBytes,
        import_ok: importOk,
        box_id_configured: Boolean(env.BOX_ID),
      });
    }

    if (request.method !== "POST") {
      return new Response("discord interactions endpoint", { status: 405 });
    }

    const rawBody = await request.text();
    if (!(await verifyFromDiscord(request, rawBody, env))) {
      return new Response("invalid request signature", { status: 401 });
    }

    let interaction;
    try {
      interaction = JSON.parse(rawBody);
    } catch (err) {
      return Response.json({ error: "bad json" }, { status: 400 });
    }

    // 1 = PING, Discord's endpoint verification handshake.
    if (interaction.type === 1) {
      return Response.json({ type: 1 });
    }

    // 2 = APPLICATION_COMMAND.
    if (interaction.type === 2 && interaction.data && interaction.data.name === "wake") {
      ctx.waitUntil(wake(env));
      return Response.json({
        type: 4,
        data: {
          content:
            "Waking the transcription bot - ready in about 30 seconds. " +
            "Then use !transcript in your voice channel.",
          flags: 64,
        },
      });
    }

    return Response.json({
      type: 4,
      data: { content: "Unknown command.", flags: 64 },
    });
  },
};
