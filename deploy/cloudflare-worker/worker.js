/**
 * Wake-on-demand endpoint for the Discord transcription bot.
 *
 * A paused Upstash Box cannot hear Discord, so something always-on has to
 * observe the trigger. This Worker is that something. It is free, needs no
 * server, and has exactly two jobs: prove the request really came from
 * Discord, and resume the Box. The Box's initCommand brings the bot back up.
 *
 * Deploy: see deploy/README.md. Secrets required:
 *   DISCORD_PUBLIC_KEY   - app Public Key (Developer Portal -> General)
 *   UPSTASH_BOX_API_KEY  - a box_... key from console.upstash.com/box
 *   BOX_ID               - the box to wake
 */

const BOX_API = "https://us-east-1.box.upstash.com";

/* ---------- Discord signature verification (Ed25519) ---------- */

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = parseInt(hex.substr(i * 2, 2), 16);
  }
  return bytes;
}

async function fromDiscord(request, rawBody, env) {
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
  const message = new TextEncoder().encode(timestamp + rawBody);
  return crypto.subtle.verify(
    { name: "NODE-ED25519" },
    key,
    hexToBytes(signature),
    message,
  );
}

/* ---------- Upstash Box ---------- */

async function wake(env) {
  // POST /resume is idempotent in effect: an already-running box answers
  // with a conflict, which is just as good as a success here.
  const res = await fetch(`${BOX_API}/v2/box/${env.BOX_ID}/resume`, {
    method: "POST",
    headers: {
      "X-Box-Api-Key": env.UPSTASH_BOX_API_KEY,
      "Content-Type": "application/json",
    },
  });
  return res.ok || res.status === 409;
}

/* ---------- handler ---------- */

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("discord interactions endpoint", { status: 405 });
    }

    const rawBody = await request.text();
    if (!(await fromDiscord(request, rawBody, env))) {
      return new Response("invalid request signature", { status: 401 });
    }

    let interaction;
    try {
      interaction = JSON.parse(rawBody);
    } catch {
      return Response.json({ error: "bad json" }, { status: 400 });
    }

    // 1 = PING: Discord's endpoint verification handshake.
    if (interaction.type === 1) {
      return Response.json({ type: 1 });
    }

    // 2 = APPLICATION_COMMAND.
    if (interaction.type === 2 && interaction.data?.name === "wake") {
      // Answer inside Discord's 3 second window, wake in the background.
      ctx.waitUntil(wake(env));
      return Response.json({
        type: 4,
        data: {
          content:
            "Waking the transcription bot — give it about a minute, then " +
            "use !transcript in your voice channel.",
          flags: 64, // ephemeral: only the caller sees it
        },
      });
    }

    return Response.json({
      type: 4,
      data: { content: "Unknown command.", flags: 64 },
    });
  },
};
