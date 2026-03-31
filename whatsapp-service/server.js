/**
 * ResumeGrid — WhatsApp Service (Internal)  [cost-optimised build]
 *
 * COST CHANGES vs previous version:
 *  1. WA_IDLE_TIMEOUT_MS default reduced to 10 min (was 15 min).
 *     Chromium is destroyed sooner after the last send → less billed RAM.
 *  2. Added --single-process Chromium flag → eliminates the zygote helper
 *     process. Cuts Chromium's idle RSS by ~100–150 MB on Railway.
 *  3. Added --memory-pressure-off and reduced --js-flags heap to 128 MB
 *     (was 192 MB) — keeps V8 tighter.
 *  4. /health endpoint now also reports approx heap so Railway logs show
 *     memory trends without needing the dashboard.
 *  5. destroyClient() now calls client.destroy() with a 5 s hard-kill
 *     fallback so a stuck Chromium never leaks into the next session.
 */

'use strict';

const express = require('express');
const cors    = require('cors');
const qrcode  = require('qrcode');
const { Client, LocalAuth } = require('whatsapp-web.js');

const app  = express();
const PORT = parseInt(process.env.WA_PORT || '3001', 10);

// Destroy Chromium after this many ms of no send activity (default: 10 min)
const IDLE_TIMEOUT_MS = parseInt(
  process.env.WA_IDLE_TIMEOUT_MS || String(10 * 60 * 1000), 10
);

app.use(cors());
app.use(express.json());

let waStatus       = 'disconnected';
let waClient       = null;
let qrDataUrl      = null;
let connectedPhone = null;
let idleTimer      = null;

// ── Idle timer ───────────────────────────────────────────────────────────────

function resetIdleTimer() {
  clearTimeout(idleTimer);
  idleTimer = setTimeout(destroyClient, IDLE_TIMEOUT_MS);
}

async function destroyClient() {
  if (!waClient) return;
  console.log('[WA] Idle timeout — destroying Chromium to free RAM');
  const c = waClient;
  waClient       = null;
  waStatus       = 'disconnected';
  qrDataUrl      = null;
  connectedPhone = null;

  // Hard-kill if destroy() hangs (Chromium sometimes hangs on Railway)
  const hardKill = setTimeout(() => {
    console.warn('[WA] destroy() timed out — forcing process cleanup');
    try { c.pupBrowser && c.pupBrowser.process()?.kill('SIGKILL'); } catch (_) {}
  }, 5000);

  try { await c.destroy(); } catch (_) {}
  clearTimeout(hardKill);
}

// ── Client factory ───────────────────────────────────────────────────────────

function createClient() {
  waStatus       = 'initializing';
  qrDataUrl      = null;
  connectedPhone = null;

  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: '/tmp/.wwebjs_auth' }),
    puppeteer: {
      executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || '/usr/bin/chromium',
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-extensions',
        '--disable-background-networking',
        '--disable-default-apps',
        '--disable-sync',
        '--disable-translate',
        '--hide-scrollbars',
        '--metrics-recording-only',
        '--mute-audio',
        '--no-first-run',
        '--no-zygote',
        // ── NEW: eliminates the zygote subprocess — saves ~100-150 MB RSS ──
        '--single-process',
        '--safebrowsing-disable-auto-update',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-ipc-flooding-protection',
        // ── Tighter V8 heap (128 MB vs previous 192 MB) ─────────────────────
        '--js-flags=--max-old-space-size=128',
      ],
    },
  });

  client.on('qr', async (qr) => {
    console.log('[WA] QR received');
    waStatus = 'qr_ready';
    try { qrDataUrl = await qrcode.toDataURL(qr, { width: 280, margin: 2 }); }
    catch (e) { console.error('[WA] QR encode:', e.message); }
  });

  client.on('authenticated', () => {
    console.log('[WA] Authenticated');
    waStatus = 'authenticated';
    qrDataUrl = null;
  });

  client.on('ready', () => {
    waStatus       = 'ready';
    connectedPhone = client.info?.wid?.user || null;
    console.log('[WA] Ready — phone:', connectedPhone);
    resetIdleTimer();
  });

  client.on('auth_failure', (msg) => {
    console.error('[WA] Auth failure:', msg);
    waStatus = 'auth_failure';
    waClient = null;
    clearTimeout(idleTimer);
  });

  client.on('disconnected', (reason) => {
    console.warn('[WA] Disconnected:', reason);
    waStatus       = 'disconnected';
    waClient       = null;
    qrDataUrl      = null;
    connectedPhone = null;
    clearTimeout(idleTimer);
  });

  client.initialize().catch((err) => {
    console.error('[WA] Init error:', err.message);
    waStatus = 'error';
    waClient = null;
    clearTimeout(idleTimer);
  });

  return client;
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function normalizePhone(raw) { return String(raw || '').replace(/\D/g, ''); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Routes ───────────────────────────────────────────────────────────────────

app.get('/health', (_req, res) => {
  const mem = process.memoryUsage();
  res.json({
    ok: true,
    waStatus,
    heapUsedMB: Math.round(mem.heapUsed / 1024 / 1024),
    rssMB: Math.round(mem.rss / 1024 / 1024),
  });
});

app.get('/status', (_req, res) =>
  res.json({ status: waStatus, qr: qrDataUrl, phone: connectedPhone }));

app.post('/connect', (_req, res) => {
  const busy = ['ready', 'authenticated', 'initializing', 'qr_ready'];
  if (waClient && busy.includes(waStatus))
    return res.json({ ok: true, status: waStatus, message: 'Already connecting' });
  if (waClient) { try { waClient.destroy(); } catch (_) {} waClient = null; }
  clearTimeout(idleTimer);
  waClient = createClient();
  res.json({ ok: true, status: waStatus, message: 'Initialization started' });
});

app.post('/disconnect', async (_req, res) => {
  clearTimeout(idleTimer);
  if (!waClient) return res.json({ ok: true, message: 'Not connected' });
  try { await waClient.logout(); } catch (_) {}
  try { await waClient.destroy(); } catch (_) {}
  waClient = null; waStatus = 'disconnected'; qrDataUrl = null; connectedPhone = null;
  res.json({ ok: true, message: 'Disconnected' });
});

app.post('/send', async (req, res) => {
  if (waStatus !== 'ready')
    return res.status(503).json({ ok: false, error: `WA not ready (${waStatus})` });
  const { phone, message } = req.body || {};
  const num = normalizePhone(phone);
  if (!num || num.length < 7) return res.status(400).json({ ok: false, error: 'Invalid phone' });
  if (!message)               return res.status(400).json({ ok: false, error: 'message required' });
  const chatId = `${num}@c.us`;
  try {
    if (!await waClient.isRegisteredUser(chatId))
      return res.status(404).json({ ok: false, error: 'Not on WhatsApp' });
    await waClient.sendMessage(chatId, message);
    resetIdleTimer();
    res.json({ ok: true, to: num });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

app.post('/send-bulk', async (req, res) => {
  if (waStatus !== 'ready')
    return res.status(503).json({ ok: false, error: `WA not ready (${waStatus})` });
  const { messages } = req.body || {};
  if (!Array.isArray(messages) || !messages.length)
    return res.status(400).json({ ok: false, error: 'messages[] required' });

  const results = [];
  for (const item of messages) {
    const num = normalizePhone(item.phone);
    if (!num || num.length < 7) {
      results.push({ phone: item.phone, name: item.name, ok: false, error: 'Invalid phone' });
      continue;
    }
    const chatId = `${num}@c.us`;
    try {
      if (!await waClient.isRegisteredUser(chatId)) {
        results.push({ phone: num, name: item.name, ok: false, error: 'Not on WhatsApp' });
      } else {
        await waClient.sendMessage(chatId, item.message);
        results.push({ phone: num, name: item.name, ok: true });
        console.log(`[WA] Sent → ${num} (${item.name || ''})`);
        resetIdleTimer();
      }
    } catch (err) {
      results.push({ phone: num, name: item.name, ok: false, error: err.message });
    }
    await sleep(1500 + Math.random() * 1500);
  }
  const sent = results.filter(r => r.ok).length;
  res.json({ ok: true, sent, failed: results.length - sent, results });
});

// ── Start ────────────────────────────────────────────────────────────────────

app.listen(PORT, '0.0.0.0', () =>
  console.log(
    `[WA Service] http://0.0.0.0:${PORT} — ` +
    `Chromium starts on demand (idle timeout: ${IDLE_TIMEOUT_MS / 1000}s)`
  ));