/**
 * SLACK AVAILABILITY BOT — Cloudflare Worker
 * Supports both Cal.com and Calendly.
 *
 * Usage: /avail [name] [timezone] [days_offset]
 * Example: /avail darryl est
 * Example: /avail jared est 14   (starts 14 days from now)
 *
 * ---------------------------------------------------------------------------
 * NOTE ON SANITISATION
 * This is a redacted copy of code that ran in production. All client and
 * teammate names, Cal.com usernames, Calendly user UUIDs, event-type IDs and
 * API tokens have been replaced with fictional, non-functional placeholders.
 * The control flow, API contracts and business logic are unchanged — only the
 * sensitive values are stubbed. Anything marked `_XXX` or formatted as a dummy
 * UUID is a placeholder, not a real credential.
 *
 * In a real deployment the tokens below would NOT be inlined — they would be
 * stored as Cloudflare Worker secrets (see README "Productionisation").
 * ---------------------------------------------------------------------------
 */

// ============================================
// CONFIGURATION
// ============================================

// Org admin token — used for any Calendly user without an individual apiKey.
// [SANITISED] Real value stored as a Worker secret in production.
const CALENDLY_ORG_TOKEN = 'CALENDLY_ORG_TOKEN_XXX';

// [SANITISED] Per-user routing table. In production this held the live roster
// (~30 people across multiple client orgs). Names/slugs/UUIDs are fictional.
const DIRECTORY = {
  // ---- Cal.com users: need { calUser, eventSlug } from their booking URL ----
  roveria: {
    platform: 'calcom',
    calUser: 'roveria-alviotech',
    eventSlug: '30min',
    displayName: 'Roveria'
  },
  harlan: {
    platform: 'calcom',
    calUser: 'harlan-alviotech',
    eventSlug: '30min',
    displayName: 'Harlan'
  },
  alec: {
    platform: 'calcom',
    calUser: 'alec-alviotech',
    eventSlug: '30min',
    displayName: 'Alec'
  },
  kr: {
    platform: 'calcom',
    calUser: 'kr-atriuspartners',
    eventSlug: '30-min-introductory-partnership-meeting',
    displayName: 'KR'
  },
  nate: {
    platform: 'calcom',
    calUser: 'nate-gtra',
    eventSlug: '30min',
    displayName: 'Nate'
  },
  roshni: {
    platform: 'calcom',
    calUser: 'roshni-akmhealth',
    eventSlug: '30min',
    displayName: 'Roshni'
  },
  suki: {
    platform: 'calcom',
    calUser: 'suki-revolance',
    eventSlug: '30min',
    displayName: 'Suki'
  },
  kelvin: {
    platform: 'calcom',
    calUser: 'kelvin-bluered',
    eventSlug: '30min',
    displayName: 'Kelvin',
    // Custom business hours — overrides the global default below.
    businessHours: { start: 8, end: 15 },
    businessHoursTz: 'Europe/London'
  },
  maron: {
    platform: 'calcom',
    calUser: 'maron-carefyn',
    eventSlug: '30min',
    displayName: 'Maron'
  },
  harvey: {
    platform: 'calcom',
    calUser: 'harvey-inforacare',
    eventSlug: '30min',
    displayName: 'Harvey'
  },
  rohan: {
    platform: 'calcom',
    calUser: 'rohan-givegrain',
    eventSlug: '30min',
    displayName: 'Rohan'
  },
  faye: {
    platform: 'calcom',
    calUser: 'faye-givegrain',
    eventSlug: '30min',
    displayName: 'Faye'
  },
  jenna: {
    platform: 'calcom',
    calUser: 'jenna-wysdom',
    eventSlug: '45-min-meeting',
    displayName: 'Jenna'
  },
  andre: {
    platform: 'calcom',
    calUser: 'andre-wysdom',
    eventSlug: '45-min-meeting',
    displayName: 'Andre'
  },
  anton: {
    platform: 'calcom',
    calUser: 'anton-cavella',
    eventSlug: '30min',
    displayName: 'Anton'
  },
  cory: {
    platform: 'calcom',
    calUser: 'cory-mentari',
    eventSlug: '30min',
    displayName: 'Cory'
  },
  tara: {
    platform: 'calcom',
    calUser: 'tara-revolance',
    eventSlug: '30min',
    displayName: 'Tara'
  },
  cassie: {
    platform: 'calcom',
    calUser: 'cassie-revolance',
    eventSlug: '30min',
    displayName: 'Cassie'
  },
  jonah: {
    platform: 'calcom',
    calUser: 'jonah-gauntlow',
    eventSlug: '30min',
    displayName: 'Jonah'
  },
  mira: {
    platform: 'calcom',
    calUser: 'mira-gauntlow',
    eventSlug: '30min',
    displayName: 'Mira'
  },
  shane: {
    platform: 'calcom',
    calUser: 'shane-gauntlow',
    eventSlug: '30min',
    displayName: 'Shane'
  },
  kendall: {
    platform: 'calcom',
    calUser: 'kendall-wysdom',
    eventSlug: '45-min-meeting',
    displayName: 'Kendall'
  },

  // ---- Calendly users: individual PAT, or fall back to the org token ----
  darryl: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000001',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Darryl'
  },
  jared: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000002',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Jared'
  },
  mark: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000003',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Mark'
  },
  enrik: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000004',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Enrik'
  },
  debra: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000005',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Debra'
  },
  miles: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000006',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Miles'
  },
  jorden: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000007',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Jorden'
  },
  albie: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000008',
    apiKey: 'CALENDLY_PAT_XXX',
    displayName: 'Albie'
  },

  // External partner contact — pinned to a specific event type so the Worker
  // skips the /event_types lookup entirely (saves one API round trip).
  // Uses the org token (no individual apiKey).
  marlowe: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000009',
    eventTypeId: '00000000-0000-4000-8000-0000000000aa',
    displayName: 'Marlowe'
  },

  // New org member — only needs a userUuid because the org token resolves it.
  nico: {
    platform: 'calendly',
    userUuid: '00000000-0000-4000-8000-000000000010',
    displayName: 'Nico'
  }
};

const TIMEZONES = {
  est: { iana: 'America/New_York', label: 'EST' },
  edt: { iana: 'America/New_York', label: 'EDT' },
  cst: { iana: 'America/Chicago', label: 'CST' },
  cdt: { iana: 'America/Chicago', label: 'CDT' },
  mst: { iana: 'America/Denver', label: 'MST' },
  mdt: { iana: 'America/Denver', label: 'MDT' },
  pst: { iana: 'America/Los_Angeles', label: 'PST' },
  pdt: { iana: 'America/Los_Angeles', label: 'PDT' },
  gmt: { iana: 'Europe/London', label: 'GMT' },
  bst: { iana: 'Europe/London', label: 'BST' },
  cet: { iana: 'Europe/Paris', label: 'CET' },
  ist: { iana: 'Asia/Kolkata', label: 'IST' },
  aest: { iana: 'Australia/Sydney', label: 'AEST' }
};

const DEFAULT_TIMEZONE = 'est';
const DEFAULT_OFFSET = 1;
const DAYS_TO_FETCH = 14;
const DAYS_TO_SHOW = 3;
const SLOTS_PER_DAY = 4;
const BUSINESS_HOURS = { start: 8, end: 18 };
const FRIDAY_CUTOFF = 14; // 2:00 PM in the output (client's) timezone

// ============================================
// MAIN HANDLER
// ============================================

export default {
  async fetch(request) {
    if (request.method !== 'POST') {
      return new Response('OK', { status: 200 });
    }

    try {
      const formData = await request.formData();
      const text = (formData.get('text') || '').trim().toLowerCase();
      const parts = text.split(/\s+/).filter(Boolean);

      if (parts.length === 0 || parts[0] === 'help') {
        return slackResponse(getHelpText());
      }

      const [nameKey, tzKey = DEFAULT_TIMEZONE, offsetStr] = parts;

      let daysOffset = DEFAULT_OFFSET;
      if (offsetStr && !isNaN(parseInt(offsetStr))) {
        daysOffset = parseInt(offsetStr);
        if (daysOffset < 0) daysOffset = 0;
        if (daysOffset > 60) daysOffset = 60;
      }

      const user = DIRECTORY[nameKey];
      if (!user) {
        const available = Object.keys(DIRECTORY).join(', ');
        return slackResponse(`❌ Unknown person: *${nameKey}*\n\nAvailable: ${available}`);
      }

      const tz = TIMEZONES[tzKey];
      if (!tz) {
        const available = Object.keys(TIMEZONES).join(', ');
        return slackResponse(`❌ Unknown timezone: *${tzKey}*\n\nAvailable: ${available}`);
      }

      let slots;
      if (user.platform === 'calendly') {
        slots = await fetchCalendlySlots(user, tz, daysOffset);
      } else {
        slots = await fetchCalComSlots(user, tz, daysOffset);
      }

      if (slots.error) {
        return slackResponse(`❌ API error: ${slots.error}`);
      }

      const output = formatAvailability(slots.data, user, tz, daysOffset);
      return slackResponse(output);

    } catch (err) {
      return slackResponse(`❌ Error: ${err.message}`);
    }
  }
};

// ============================================
// CAL.COM API
// ============================================

async function fetchCalComSlots(user, tz, daysOffset) {
  const now = new Date();
  const start = new Date(now.getTime() + daysOffset * 86400000).toISOString();
  const end = new Date(now.getTime() + (daysOffset + DAYS_TO_FETCH) * 86400000).toISOString();

  const url = new URL('https://api.cal.com/v2/slots');
  url.searchParams.set('username', user.calUser);
  url.searchParams.set('eventTypeSlug', user.eventSlug);
  url.searchParams.set('start', start);
  url.searchParams.set('end', end);
  url.searchParams.set('timeZone', tz.iana);

  const response = await fetch(url.toString(), {
    headers: { 'cal-api-version': '2024-09-04' }
  });

  if (!response.ok) {
    const text = await response.text();
    return { error: `Cal.com HTTP ${response.status}: ${text.slice(0, 100)}` };
  }

  const json = await response.json();

  if (json.status === 'error') {
    return { error: json.error?.message || 'Cal.com API error' };
  }

  const slots = [];
  const data = json.data || {};

  for (const dateKey of Object.keys(data).sort()) {
    for (const slot of data[dateKey] || []) {
      if (slot.start) slots.push(slot.start);
    }
  }

  return { data: slots };
}

// ============================================
// CALENDLY API
// ============================================

async function fetchCalendlySlots(user, tz, daysOffset) {
  const userUri = `https://api.calendly.com/users/${user.userUuid}`;
  const token = user.apiKey || CALENDLY_ORG_TOKEN;

  // Determine event type URI — use pinned eventTypeId if specified, otherwise auto-detect
  let eventTypeUri;

  if (user.eventTypeId) {
    eventTypeUri = `https://api.calendly.com/event_types/${user.eventTypeId}`;
  } else {
    const eventTypesUrl = new URL('https://api.calendly.com/event_types');
    eventTypesUrl.searchParams.set('user', userUri);
    eventTypesUrl.searchParams.set('active', 'true');

    const eventTypesRes = await fetch(eventTypesUrl.toString(), {
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json'
      }
    });

    if (!eventTypesRes.ok) {
      const text = await eventTypesRes.text();
      return { error: `Calendly HTTP ${eventTypesRes.status}: ${text.slice(0, 100)}` };
    }

    const eventTypesJson = await eventTypesRes.json();
    const eventTypes = eventTypesJson.collection || [];

    if (eventTypes.length === 0) {
      return { error: 'No active event types found for this user' };
    }

    eventTypeUri = eventTypes[0].uri;
  }

  const now = new Date();
  const start = new Date(now.getTime() + Math.max(daysOffset, 0) * 86400000 + 60000).toISOString();
  const end = new Date(now.getTime() + (daysOffset + 7) * 86400000).toISOString();

  const availUrl = new URL('https://api.calendly.com/event_type_available_times');
  availUrl.searchParams.set('event_type', eventTypeUri);
  availUrl.searchParams.set('start_time', start);
  availUrl.searchParams.set('end_time', end);

  const availRes = await fetch(availUrl.toString(), {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  });

  if (!availRes.ok) {
    const text = await availRes.text();
    return { error: `Calendly HTTP ${availRes.status}: ${text.slice(0, 100)}` };
  }

  const availJson = await availRes.json();
  const collection = availJson.collection || [];

  const slots = collection.map(item => item.start_time).filter(Boolean);

  return { data: slots };
}

// ============================================
// FORMATTING
// ============================================

function formatAvailability(slots, user, tz, daysOffset) {
  const name = user.displayName;
  const customHours = user.businessHours || BUSINESS_HOURS;
  const hourCheckTz = user.businessHoursTz || tz.iana;

  const byDay = new Map();

  for (const isoString of slots) {
    const utcDate = new Date(isoString);

    // Get hour in the FILTER timezone (user's custom tz or output tz)
    const hourInTz = parseInt(utcDate.toLocaleString('en-US', {
      timeZone: hourCheckTz,
      hour: 'numeric',
      hour12: false
    }));

    // Get day of week in the FILTER timezone
    const dayNum = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(
      utcDate.toLocaleString('en-US', { timeZone: hourCheckTz, weekday: 'short' })
    );

    // Skip weekends
    if (dayNum === 0 || dayNum === 6) continue;

    // Skip outside business hours
    if (hourInTz < customHours.start || hourInTz >= customHours.end) continue;

    // Friday cutoff — check in the OUTPUT (client's) timezone
    const outputDayNum = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(
      utcDate.toLocaleString('en-US', { timeZone: tz.iana, weekday: 'short' })
    );
    const outputHour = parseInt(utcDate.toLocaleString('en-US', {
      timeZone: tz.iana,
      hour: 'numeric',
      hour12: false
    }));

    if (outputDayNum === 5 && outputHour >= FRIDAY_CUTOFF) continue;

    // Group by date in OUTPUT timezone for display
    const dateKey = utcDate.toLocaleString('en-US', {
      timeZone: tz.iana,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit'
    });

    if (!byDay.has(dateKey)) {
      byDay.set(dateKey, { utcDate, times: [] });
    }
    byDay.get(dateKey).times.push(utcDate);
  }

  const days = [...byDay.entries()]
    .sort((a, b) => a[1].utcDate - b[1].utcDate)
    .slice(0, DAYS_TO_SHOW);

  if (days.length === 0) {
    const offsetNote = daysOffset > 1 ? ` (starting ${daysOffset} days out)` : '';
    return `No business-hours availability for *${name}* in the next ${DAYS_TO_FETCH} days${offsetNote}.`;
  }

  const lines = days.map(([_, { utcDate, times }]) => {
    times.sort((a, b) => a - b);
    const picks = spreadSlots(times, SLOTS_PER_DAY);

    // Format day in OUTPUT timezone
    const dayStr = utcDate.toLocaleDateString('en-US', {
      timeZone: tz.iana,
      weekday: 'long',
      month: 'short',
      day: 'numeric'
    });

    // Format times in OUTPUT timezone
    const timeStr = picks
      .map(t => t.toLocaleTimeString('en-US', {
        timeZone: tz.iana,
        hour: 'numeric',
        minute: '2-digit',
        hour12: true
      }).toLowerCase())
      .join(', ');

    return `– ${dayStr} — ${timeStr} ${tz.label}`;
  });

  const offsetNote = daysOffset > 1 ? `\n_(starting ${daysOffset} days out)_` : '';
  return `*Availability for ${name}:*\n\n${lines.join('\n')}${offsetNote}`;
}

function spreadSlots(times, count) {
  if (times.length === 0) return [];

  // Filter to only :00 or :30 slots for clean display times
  const cleanTimes = times.filter(t => {
    const mins = t.getMinutes();
    return mins === 0 || mins === 30;
  });

  // Fall back to original times if no clean slots available
  const pool = cleanTimes.length > 0 ? cleanTimes : times;

  if (pool.length <= count) return pool;

  // Pick slots at least 45 minutes apart
  const MIN_GAP_MS = 45 * 60 * 1000;
  const result = [pool[0]];

  for (let i = 1; i < pool.length && result.length < count; i++) {
    const lastPicked = result[result.length - 1];
    const current = pool[i];

    if (current.getTime() - lastPicked.getTime() >= MIN_GAP_MS) {
      result.push(current);
    }
  }

  return result;
}

function getHelpText() {
  const people = Object.entries(DIRECTORY)
    .map(([key, val]) => `• ${key} → ${val.displayName}`)
    .join('\n');

  const zones = Object.keys(TIMEZONES).join(', ');

  return `*Availability Bot*\n\n` +
    `*Usage:* \`/avail [name] [timezone] [days_offset]\`\n\n` +
    `*Examples:*\n` +
    `• \`/avail darryl est\` — availability starting tomorrow\n` +
    `• \`/avail jared est 14\` — availability starting 14 days out\n\n` +
    `*People:*\n${people}\n\n` +
    `*Timezones:* ${zones}\n\n` +
    `Default timezone: ${DEFAULT_TIMEZONE.toUpperCase()}`;
}

// ============================================
// SLACK RESPONSE
// ============================================

function slackResponse(text) {
  return new Response(
    JSON.stringify({
      response_type: 'in_channel',
      text: text
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json' }
    }
  );
}
