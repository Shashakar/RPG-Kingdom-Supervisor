(() => {
  const quotaAgeSeconds = observedAt => {
    if (!observedAt) return null;
    const timestamp = new Date(observedAt).getTime();
    return Number.isFinite(timestamp) ? Math.max(0, (Date.now() - timestamp) / 1000) : null;
  };

  const ageText = seconds => {
    if (seconds === null || seconds === undefined) return 'unknown age';
    if (seconds < 60) return `${Math.round(seconds)}s ago`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
    return `${Math.round(seconds / 86400)}d ago`;
  };

  const percent = value => value === null || value === undefined ? '—' : `${Math.round(Number(value) * 10) / 10}%`;
  const resetText = value => {
    if (!value) return null;
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  };

  const quotaValues = sample => {
    const limits = sample?.rateLimits || {};
    return {
      primary: limits.primary?.remainingPercent,
      weekly: limits.secondary?.remainingPercent,
      primaryReset: limits.primary?.resetsAtIso,
    };
  };

  const classify = quota => {
    if (!quota || !quota.observedAt) {
      return {state: 'not-sampled', age: null, reason: quota?.reason || 'no authoritative Codex quota sample has been recorded'};
    }
    const age = quotaAgeSeconds(quota.observedAt);
    if (quota.status !== 'available') {
      return {state: 'unavailable', age, reason: quota.reason || 'latest quota refresh failed'};
    }
    const threshold = Number(quota.staleAfterSeconds || 600);
    if (age !== null && age > threshold) {
      return {state: 'stale', age, reason: `last successful sample is older than ${Math.round(threshold / 60)}m`};
    }
    return {state: 'fresh', age, reason: null};
  };

  function renderQuotaState() {
    const quota = state?.operations?.quota || {};
    const classification = classify(quota);
    const values = quotaValues(quota);
    const card = document.getElementById('metric-quota');
    const note = document.getElementById('metric-quota-note');
    const header = document.getElementById('header-quota');
    if (!card || !note || !header) return;

    const reset = resetText(values.primaryReset);
    if (classification.state === 'fresh') {
      card.textContent = percent(values.primary);
      card.className = `metric-value ${Number(values.primary) < 15 ? 'bad' : Number(values.primary) < 35 ? 'warn' : 'good'}`;
      note.textContent = `weekly ${percent(values.weekly)} remaining · sampled ${ageText(classification.age)}${reset ? ` · 5h reset ${reset}` : ''}`;
      header.innerHTML = `Quota <strong>${esc(percent(values.primary))} / ${esc(percent(values.weekly))}</strong>`;
      return;
    }

    if (classification.state === 'stale') {
      card.textContent = 'stale';
      card.className = 'metric-value warn';
      note.textContent = `last ${ageText(classification.age)} · ${percent(values.primary)} / ${percent(values.weekly)} remaining${reset ? ` · 5h reset ${reset}` : ''}`;
      header.innerHTML = `Quota <strong>stale · ${esc(ageText(classification.age))}</strong>`;
      return;
    }

    if (classification.state === 'unavailable') {
      card.textContent = 'unavailable';
      card.className = 'metric-value warn';
      const previous = quota.lastSuccessful || null;
      const previousAge = previous ? quotaAgeSeconds(previous.observedAt) : null;
      const previousValues = quotaValues(previous);
      const previousText = previous
        ? ` · last good ${ageText(previousAge)}: ${percent(previousValues.primary)} / ${percent(previousValues.weekly)}`
        : '';
      note.textContent = `${classification.reason}${previousText}`;
      header.innerHTML = `Quota <strong>unavailable</strong>`;
      return;
    }

    card.textContent = 'not sampled';
    card.className = 'metric-value';
    note.textContent = classification.reason;
    header.innerHTML = `Quota <strong>not sampled yet</strong>`;
  }

  if (typeof renderOverview === 'function') {
    const baseRenderOverview = renderOverview;
    renderOverview = function() {
      baseRenderOverview();
      renderQuotaState();
    };
  }
})();
