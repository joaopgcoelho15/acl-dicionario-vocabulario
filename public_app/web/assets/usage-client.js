(() => {
  "use strict";

  const sessionKey = "acl_usage_session";
  const sequenceKey = "acl_usage_sequence";
  let session = "";
  let sequence = 0;

  try {
    session = window.sessionStorage.getItem(sessionKey) || "";
    sequence = Number(window.sessionStorage.getItem(sequenceKey) || 0);
  } catch {
    // O registo continua em memória quando sessionStorage está bloqueado.
  }

  if (!session) {
    session = window.crypto?.randomUUID?.() || fallbackSessionId();
    try {
      window.sessionStorage.setItem(sessionKey, session);
    } catch {
      // Neste caso a sessão termina ao recarregar a página.
    }
  }

  function fallbackSessionId() {
    const values = new Uint32Array(4);
    if (window.crypto?.getRandomValues) {
      window.crypto.getRandomValues(values);
    } else {
      values.forEach((_, index) => {
        values[index] = Math.floor(Math.random() * 0x100000000);
      });
    }
    return [...values]
      .map((value) => value.toString(16).padStart(8, "0"))
      .join("");
  }

  function nextSequence() {
    sequence += 1;
    try {
      window.sessionStorage.setItem(sequenceKey, String(sequence));
    } catch {
      // A sequência em memória permanece válida durante esta página.
    }
    return sequence;
  }

  function headers(eventSequence = null) {
    return {
      "X-ACL-Session": session,
      ...(eventSequence ? { "X-ACL-Seq": String(eventSequence) } : {}),
    };
  }

  function send(url, event, fields = {}, eventSequence = null) {
    const seq = eventSequence || nextSequence();
    return window.fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...headers(seq),
      },
      keepalive: true,
      body: JSON.stringify({
        ts: new Date().toISOString(),
        event,
        ...fields,
      }),
    }).catch(() => undefined);
  }

  function roundDuration(value) {
    return Math.round(Number(value || 0) * 100) / 100;
  }

  window.ACLUsage = Object.freeze({
    headers,
    nextSequence,
    roundDuration,
    send,
    session,
  });
})();
