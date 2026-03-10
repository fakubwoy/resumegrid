/**
 * ResumeGrid — WhatsApp Service (Internal)
 * Runs as background process inside the same Railway container as Flask.
 * Flask proxies /wa/* requests here via WA_SERVICE_URL=http://localhost:3001
 */

'use strict';

const express = require('express');
const cors    = require('cors');
const qrcode  = require('qrcode');
const { Client, LocalAuth } = require('whatsapp-web.js');

const app  = express();
const PORT = parseInt(process.env.WA_PORT || '3001', 10);

app.use(cors());
app.use(express.json());

let waStatus       = 'disconnected';
let waClient       = null;
let qrDataUrl      = null;
let connectedPhone = null;

function createClient() {
  waStatus       = 'initializing';
  qrDataUrl      = null;
  connectedPhone = null;

  const client = new Client({
    authStrategy: new LocalAuth({ dataPath: '/app/.wwebjs_auth' }),
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
        '--safebrowsing-disable-auto-update',
        '--single-process',
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
  });

  client.on('auth_failure', (msg) => {
    console.error('[WA] Auth failure:', msg);
    waStatus = 'auth_failure';
    waClient = null;
  });

  client.on('disconnected', (reason) => {
    console.warn('[WA] Disconnected:', reason);
    waStatus = 'disconnected';
    waClient = null;
    qrDataUrl = null;
    connectedPhone = null;
  });

  client.initialize().catch((err) => {
    console.error('[WA] Init error:', err.message);
    waStatus = 'error';
    waClient = null;
  });

  return client;
}

function normalizePhone(raw) { return String(raw || '').replace(/\D/g, ''); }
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

app.get('/health', (_req, res) => res.json({ ok: true, waStatus }));

app.get('/status', (_req, res) =>
  res.json({ status: waStatus, qr: qrDataUrl, phone: connectedPhone }));

app.post('/connect', (_req, res) => {
  const busy = ['ready', 'authenticated', 'initializing', 'qr_ready'];
  if (waClient && busy.includes(waStatus))
    return res.json({ ok: true, status: waStatus, message: 'Already connecting' });
  if (waClient) { try { waClient.destroy(); } catch (_) {} waClient = null; }
  waClient = createClient();
  res.json({ ok: true, status: waStatus, message: 'Initialization started' });
});

app.post('/disconnect', async (_req, res) => {
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
    if (!num || num.length < 7) { results.push({ phone: item.phone, name: item.name, ok: false, error: 'Invalid phone' }); continue; }
    const chatId = `${num}@c.us`;
    try {
      if (!await waClient.isRegisteredUser(chatId)) {
        results.push({ phone: num, name: item.name, ok: false, error: 'Not on WhatsApp' });
      } else {
        await waClient.sendMessage(chatId, item.message);
        results.push({ phone: num, name: item.name, ok: true });
        console.log(`[WA] Sent → ${num} (${item.name || ''})`);
      }
    } catch (err) {
      results.push({ phone: num, name: item.name, ok: false, error: err.message });
    }
    await sleep(1500 + Math.random() * 1500);
  }
  const sent = results.filter(r => r.ok).length;
  res.json({ ok: true, sent, failed: results.length - sent, results });
});

app.listen(PORT, '0.0.0.0', () =>
  console.log(`[WA Service] http://0.0.0.0:${PORT}`));