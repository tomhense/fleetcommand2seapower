/* Simple SCS JSON map viewer. Leaflet supplies the basemap; all scenario
   symbols and corridor shapes are SVG/Leaflet layers. */
(() => {
  const COLORS = { friendly: '#42d6a5', enemy: '#ff6e6e', neutral: '#f1c75b' };
  const map = L.map('map', { worldCopyJump: true, zoomControl: true }).setView([20, 0], 2);
  L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}', {
    maxZoom: 19,
    attribution: 'Tiles &copy; Esri'
  }).addTo(map);
  const overlays = {
    friendly: L.layerGroup().addTo(map), enemy: L.layerGroup().addTo(map), neutral: L.layerGroup().addTo(map),
    waypoints: L.layerGroup().addTo(map), formations: L.layerGroup().addTo(map), corridors: L.layerGroup().addTo(map), goals: L.layerGroup().addTo(map), zones: L.layerGroup().addTo(map)
  };
  const scenarios = [];
  const bounds = [];
  const unitEntries = [];
  const filterState = { type: 'all', country: 'all', class: 'all' };
  const dropzone = document.getElementById('dropzone');
  const input = document.getElementById('file-input');
  const message = document.getElementById('map-message');

  const scalar = value => Array.isArray(value) ? value[0] : value;
  const position = item => item && item.position && Number.isFinite(Number(item.position.latitude)) && Number.isFinite(Number(item.position.longitude))
    ? [Number(item.position.latitude), Number(item.position.longitude)] : null;

  map.on('mousemove', event => {
    document.getElementById('coordinate-readout').textContent = `${event.latlng.lat.toFixed(5)}, ${event.latlng.lng.toFixed(5)}`;
  }).on('mouseout', () => { document.getElementById('coordinate-readout').textContent = '—'; });

  function allianceSets(data) {
    const sets = { friendly: new Set(), enemy: new Set(), neutral: new Set() };
    const own = scalar(data.metadata && data.metadata.ownalliance);
    if (own !== null && own !== undefined) sets.friendly.add(Number(own));
    (data.formations || []).forEach(group => {
      const type = String(group.type || '').toLowerCase();
      const set = type.includes('threat') ? sets.enemy : type.includes('neutral') ? sets.neutral : sets.friendly;
      (group.alliance_ids || []).forEach(id => set.add(Number(id)));
    });
    return sets;
  }

  function allegiance(item, sets) {
    if (item && ['friendly', 'enemy', 'neutral'].includes(item.alliance_role)) return item.alliance_role;
    const id = Number(scalar(item && item.alliance));
    if (sets.friendly.has(id)) return 'friendly';
    if (sets.enemy.has(id)) return 'enemy';
    if (sets.neutral.has(id)) return 'neutral';
    return 'neutral';
  }

  function symbolClass(item, kind) {
    const text = [item && item.entity_type, item && item.class, item && item.name,
      item && item.shipname, item && item.uniquename].filter(Boolean).join(' ').toLowerCase();
    if (/naval mine|sea mine|mine warfare|\bmine\b/.test(text)) return 'mine';
    if (/sam|surface[- ]to[- ]air|missile site|aa site/.test(text)) return 'sam';
    if (/ssbn|ballistic missile submarine/.test(text)) return 'ssbn';
    if (/ssgn|guided missile submarine/.test(text)) return 'ssgn';
    if (/ssn|attack submarine/.test(text)) return 'ssn';
    if (/ssk|diesel[- ]electric|conventional submarine/.test(text)) return 'ssk';
    if (/submarine|\bss[nkbn]|\bssg|\bss/.test(text)) return 'submarine';
    if (/carrier|\bcvn\b|\bcva\b|\bcv\b/.test(text)) return 'carrier';
    if (/cruiser|\bcg\b|\bca\b/.test(text)) return 'cruiser';
    if (/frigate|\bffg\b|\bfg\b/.test(text)) return 'frigate';
    if (/destroyer|\bddg\b|\bdd\b/.test(text)) return 'destroyer';
    if (/\bfsg\b|guided missile corvette/.test(text)) return 'fsg';
    if (/corvette/.test(text)) return 'corvette';
    if (/patrol|fast attack craft|\bpt\b/.test(text)) return 'patrol';
    if (/bomber|\bb-\d|\btu-\d|\bbackfire/.test(text)) return 'bomber';
    if (/helicopter|helo|\bsh-\d|\bmh-\d|\bmi-\d|\bka-\d/.test(text)) return 'helicopter';
    if (/transport|tanker|airlift|\bc-\d|\ban-\d|\bil-\d/.test(text)) return 'transport';
    if (/uav|uas|drone|unmanned|reaper|predator/.test(text)) return 'uav';
    if (/attack aircraft|ground attack|strike|\ba-\d|\bsu-\d/.test(text)) return 'attack';
    if (kind === 'aircraft' || /fighter|interceptor|fighter\/attack|\bf-\d|\bmig|\bfa-\d/.test(text)) return 'fighter';
    if (/armor|armoured|tank|\bmbt\b|\bifv\b|\tapc\b/.test(text)) return 'armor';
    if (/artillery|howitzer|rocket artillery|mlrs/.test(text)) return 'artillery';
    if (kind === 'land') return 'land';
    return 'vessel';
  }

  function symbol(kind, color, subtype) {
    const iconType = ['fighter', 'bomber', 'attack', 'helicopter', 'transport', 'uav',
      'carrier', 'cruiser', 'destroyer', 'frigate', 'corvette', 'patrol', 'submarine',
      'fsg', 'ssbn', 'ssgn', 'ssn', 'ssk', 'sam', 'mine', 'armor', 'artillery', 'land', 'airbase', 'vessel'].includes(subtype)
      ? subtype : 'vessel';
    const affiliation = ['friendly', 'enemy', 'neutral'].includes(color) ? color : 'neutral';
    const src = `assets/symbols/${affiliation}-${iconType}.svg`;
    return `<div class="unit-icon symbol-${iconType}" title="${iconType}"><img src="${src}" alt="${affiliation} ${iconType} symbol"></div>`;
  }

  function tooltip(item, kind, color, filename) {
    const title = item.uniquename || item.name || item.shipname || kind;
    const exactType = [item.entity_type, item.class].filter(Boolean).join(' · ') || kind;
    const country = item.alliance_country || item.country || (item.alliance_countries || []).join(', ') || 'Unknown';
    const alliance = item.alliance_id ?? item.alliance ?? '—';
    const probability = item.prob;
    const probabilityLine = probability !== null && probability !== undefined && probability !== ''
      ? `<br>Probability: ${escapeHtml(String(probability))}%` : '';
    const aircraft = (item.aircraft || []).map(air => {
      const name = air.variant || air.name || 'Aircraft';
      const qty = air.quantity === null || air.quantity === undefined ? '' : ` × ${air.quantity}`;
      const operator = air.country ? ` · ${air.country}` : '';
      return `${name}${qty}${operator}`;
    });
    const aircraftBlock = aircraft.length
      ? `<div class="tooltip-aircraft"><b>Aircraft</b>${aircraft.map(air => `<div>${escapeHtml(air)}</div>`).join('')}</div>` : '';
    return `<div class="unit-tooltip"><strong>${escapeHtml(title)}</strong><span>Type: ${escapeHtml(exactType)}<br>Country: ${escapeHtml(country)}${probabilityLine}<br>${escapeHtml(color)} · alliance ${escapeHtml(String(alliance))}<br>${escapeHtml(filename)}</span>${aircraftBlock}</div>`;
  }

  function showUnitDetails(item, kind, color, filename) {
    const panel = document.getElementById('unit-details');
    const title = item.uniquename || item.name || item.shipname || kind;
    const coords = position(item);
    const country = item.alliance_country || item.country || (item.alliance_countries || []).join(', ') || 'Unknown';
    const aircraft = (item.aircraft || []).map(air => `${air.variant || air.name || 'Aircraft'}${air.quantity == null ? '' : ` × ${air.quantity}`}${air.country ? ` · ${air.country}` : ''}`);
    panel.innerHTML = `<strong>${escapeHtml(title)}</strong><dl><dt>Type</dt><dd>${escapeHtml([item.entity_type, item.class].filter(Boolean).join(' · ') || kind)}</dd><dt>Country</dt><dd>${escapeHtml(country)}</dd><dt>Alliance</dt><dd>${escapeHtml(String(item.alliance_id ?? item.alliance ?? '—'))} · ${escapeHtml(color)}</dd><dt>Coordinates</dt><dd>${coords ? `${coords[0].toFixed(5)}, ${coords[1].toFixed(5)}` : '—'}</dd>${item.formationleader ? `<dt>Formation leader</dt><dd>${escapeHtml(item.formationleader)}</dd>` : ''}${item.sourceobject ? `<dt>Source object</dt><dd>${escapeHtml(item.sourceobject)}</dd>` : ''}</dl>${aircraft.length ? `<div class="detail-aircraft"><b>Aircraft</b>${aircraft.map(value => `<div>${escapeHtml(value)}</div>`).join('')}</div>` : ''}`;
    panel.classList.remove('empty');
  }

  function addUnit(item, kind, data, filename, registry) {
    const latLng = position(item); if (!latLng) return false;
    const color = allegiance(item, allianceSets(data));
    const subtype = symbolClass(item, kind);
    const marker = L.marker(latLng, { icon: L.divIcon({ className: '', html: symbol(kind, color, subtype), iconSize: [28, 28], iconAnchor: [14, 14] }) });
    marker.bindTooltip(tooltip(item, kind, color, filename), { direction: 'top', offset: [0, -10] });
    marker.on('click', () => showUnitDetails(item, kind, color, filename));
    marker.addTo(overlays[color]);
    const entry = { item, latLng, kind, color, marker, filename, subtype };
    unitEntries.push(entry); bounds.push(latLng); registry.push(entry); return true;
  }

  function airStationColor(station, registry, data) {
    const source = registry.find(unit => normalizedName(unitLabel(unit.item)) === normalizedName(station.sourceobject));
    return source ? source.color : 'friendly';
  }

  function addAirStation(station, data, filename, registry) {
    const latLng = position(station); if (!latLng) return false;
    const color = airStationColor(station, registry, data);
    const item = { ...station, alliance_role: color, class: 'AIR STATION', country: station.aircraft && station.aircraft[0] ? station.aircraft[0].country : null };
    const marker = L.marker(latLng, { icon: L.divIcon({ className: '', html: symbol('airbase', color, 'airbase'), iconSize: [28, 28], iconAnchor: [14, 14] }) });
    marker.bindTooltip(tooltip(item, 'air station', color, filename), { direction: 'top', offset: [0, -10] });
    marker.on('click', () => showUnitDetails(item, 'air station', color, filename));
    marker.addTo(overlays[color]);
    const entry = { item, latLng, kind: 'airbase', color, marker, filename, subtype: 'airbase' };
    unitEntries.push(entry); bounds.push(latLng); registry.push(entry); return true;
  }

  function normalizedName(value) {
    return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function unitLabel(item) {
    return item.uniquename || item.shipname || item.name || 'unit';
  }

  function drawFormations(data, registry, filename) {
    const byName = new Map();
    registry.forEach(unit => {
      [unitLabel(unit.item), unit.item.name, unit.item.shipname].forEach(name => {
        const key = normalizedName(name);
        if (key && !byName.has(key)) byName.set(key, unit);
      });
    });
    const formationNames = new Map((data.formations || [])
      .filter(formation => formation.type === 'formation')
      .map(formation => [normalizedName(formation.leader), formation.name]));
    registry.forEach(member => {
      const leaderName = member.item.formationleader;
      const leader = byName.get(normalizedName(leaderName));
      if (!leader || leader === member) return;
      const formationName = formationNames.get(normalizedName(leaderName));
      const label = formationName ? `${formationName} · leader ${unitLabel(leader.item)}` : `Formation leader: ${unitLabel(leader.item)}`;
      const color = allegiance(member.item, allianceSets(data));
      L.polyline([member.latLng, leader.latLng], {
        renderer: L.svg(), color: COLORS[color], weight: 1.5, opacity: .8,
        dashArray: '4 4', interactive: true
      }).bindTooltip(`<div class="unit-tooltip"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(unitLabel(member.item))} → ${escapeHtml(unitLabel(leader.item))}<br>${escapeHtml(filename)}</span></div>`)
        .addTo(overlays.formations);
    });
  }

  function drawWaypoints(data, registry, filename) {
    registry.forEach(unit => {
      const waypoints = (unit.item.waypoints || []).map(waypoint => {
        const lat = Number(waypoint.latitude), lon = Number(waypoint.longitude);
        return Number.isFinite(lat) && Number.isFinite(lon) ? {
          latLng: [lat, lon], waypoint
        } : null;
      }).filter(Boolean);
      if (!waypoints.length) return;
      const points = [unit.latLng, ...waypoints.map(entry => entry.latLng)];
      const color = allegiance(unit.item, allianceSets(data));
      L.polyline(points, {
        renderer: L.svg(), color: COLORS[color], weight: 2, opacity: .65,
        interactive: false
      }).addTo(overlays.waypoints);
      waypoints.forEach((entry, index) => {
        const waypoint = entry.waypoint;
        const details = [
          `Waypoint ${index + 1}`,
          waypoint.speed !== null && waypoint.speed !== undefined ? `speed ${waypoint.speed}` : null,
          waypoint.altitude !== null && waypoint.altitude !== undefined ? `altitude ${waypoint.altitude}` : null,
          filename
        ].filter(Boolean).join(' · ');
        L.circleMarker(entry.latLng, {
          radius: 4, color: COLORS[color], weight: 1.5, fillColor: '#071015',
          fillOpacity: 1, renderer: L.svg()
        }).bindTooltip(`<div class="unit-tooltip"><strong>${escapeHtml(unitLabel(unit.item))}</strong><span>${escapeHtml(details)}</span></div>`, { direction: 'top', offset: [0, -4] }).addTo(overlays.waypoints);
        bounds.push(entry.latLng);
      });
    });
  }

  function drawGoals(data, filename, registry) {
    let count = 0;
    const byName = new Map();
    registry.forEach(unit => [unitLabel(unit.item), unit.item.name, unit.item.shipname].forEach(name => {
      const key = normalizedName(name); if (key && !byName.has(key)) byName.set(key, unit);
    }));
    (data.triggers_goals || []).forEach(goal => {
      const lat = Number(goal.position && goal.position.latitude);
      const lon = Number(goal.position && goal.position.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const title = goal.goalname || goal.message || `Goal ${goal.goalid ?? ''}`;
      const target = goal.uniquename || goal.shipname || goal.class || '—';
      const timing = [goal.starttime !== null && goal.starttime !== undefined ? `start ${goal.starttime}s` : null,
        goal.endtime !== null && goal.endtime !== undefined ? `end ${goal.endtime}s` : null].filter(Boolean).join(' · ');
      const details = [
        `Goal type: ${goal.goaltype ?? '—'}`,
        `Goal ID: ${goal.goalid ?? '—'} · parent: ${goal.parentgoalid ?? '—'}`,
        `Target: ${target}`,
        timing || null,
        goal.damage !== null && goal.damage !== undefined ? `damage ${goal.damage}%` : null,
        goal.points !== null && goal.points !== undefined ? `points ${goal.points}` : null,
        filename
      ].filter(Boolean).map(escapeHtml).join('<br>');
      const message = goal.message || goal.successmessage || '';
      const html = `<div class="unit-tooltip"><strong>${escapeHtml(title)}</strong><span>${details}</span>${message ? `<div class="tooltip-aircraft">${escapeHtml(message)}</div>` : ''}</div>`;
      const icon = L.divIcon({ className: '', html: '<div class="goal-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2l9 5v6c0 5-4 8-9 9-5-1-9-4-9-9V7z"/><path d="M12 7v7M12 17h.01"/></svg></div>', iconSize: [26, 26], iconAnchor: [13, 13] });
      L.marker([lat, lon], { icon }).bindTooltip(html, { direction: 'top', offset: [0, -10] }).addTo(overlays.goals);
      const targetUnit = byName.get(normalizedName(goal.uniquename || goal.shipname));
      if (targetUnit) {
        L.polyline([[lat, lon], targetUnit.latLng], { renderer: L.svg(), color: '#c084fc', weight: 1.5, opacity: .75, dashArray: '2 5', interactive: false }).addTo(overlays.goals);
      }
      bounds.push([lat, lon]); count++;
    });
    return count;
  }

  function drawZones(data, registry, filename) {
    registry.forEach(unit => {
      [['box', 'Box'], ['random_start_box', 'Random start box']].forEach(([key, label]) => {
        const corners = unit.item[key] && unit.item[key].corners;
        if (!corners || corners.length < 2) return;
        const points = corners.map(corner => [Number(corner.latitude), Number(corner.longitude)]);
        if (!points.every(point => point.every(Number.isFinite))) return;
        L.rectangle(points, { renderer: L.svg(), color: COLORS[unit.color], weight: 1.5, opacity: .75, fillOpacity: .08, dashArray: key === 'box' ? null : '5 5', interactive: true })
          .bindTooltip(`<div class="unit-tooltip"><strong>${escapeHtml(label)}</strong><span>${escapeHtml(unitLabel(unit.item))}<br>${escapeHtml(filename)}</span></div>`)
          .addTo(overlays.zones);
      });
    });
  }

  function unitMatches(entry) {
    const country = entry.item.alliance_country || entry.item.country || (entry.item.alliance_countries || []).join(', ') || 'Unknown';
    const className = entry.item.class || entry.subtype || 'Unknown';
    return (filterState.type === 'all' || entry.kind === filterState.type) &&
      (filterState.country === 'all' || country === filterState.country) &&
      (filterState.class === 'all' || className === filterState.class);
  }

  function applyFilters() {
    unitEntries.forEach(entry => {
      const visible = unitMatches(entry);
      if (visible) entry.marker.addTo(overlays[entry.color]);
      else overlays[entry.color].removeLayer(entry.marker);
    });
  }

  function refreshFilters() {
    const values = (selector, entries, allLabel) => {
      const current = filterState[selector];
      const select = document.getElementById(`filter-${selector}`);
      const options = [...new Set(entries)].filter(Boolean).sort();
      select.innerHTML = `<option value="all">${allLabel}</option>${options.map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('')}`;
      select.value = options.includes(current) ? current : 'all';
      filterState[selector] = select.value;
    };
    values('country', unitEntries.map(entry => entry.item.alliance_country || entry.item.country || (entry.item.alliance_countries || []).join(', ') || 'Unknown'), 'All countries');
    values('class', unitEntries.map(entry => entry.item.class || entry.subtype), 'All classes');
  }

  function addCorridor(corridor, data, filename) {
    const start = corridor.start && [Number(corridor.start.latitude), Number(corridor.start.longitude)];
    const end = corridor.end && [Number(corridor.end.latitude), Number(corridor.end.longitude)];
    if (!start || !end || !start.every(Number.isFinite) || !end.every(Number.isFinite)) return false;
    const color = allegiance({ alliance: data.metadata && data.metadata.ownalliance }, allianceSets(data));
    L.polyline([start, end], { renderer: L.svg(), color: COLORS[color], weight: 3, opacity: .9, dashArray: '8 6' })
      .bindTooltip(`<div class="unit-tooltip"><strong>${escapeHtml(corridor.name || 'Air corridor')}</strong><span>air corridor · ${escapeHtml(filename)}</span></div>`)
      .addTo(overlays.corridors);
    bounds.push(start, end);
    (corridor.aircraft || []).forEach(aircraft => {
      // Corridor aircraft have no separate position; their details remain in the tooltip.
      void aircraft;
    });
    return true;
  }

  function render(data, filename) {
    const categories = [['aircraft', 'aircraft'], ['vessels', 'vessel'], ['land_units', 'land']];
    const registry = [];
    let units = 0, corridors = 0;
    categories.forEach(([key, kind]) => (data[key] || []).forEach(item => { if (addUnit(item, kind, data, filename, registry)) units++; (item.air_corridors || []).forEach(c => { if (addCorridor(c, data, filename)) corridors++; }); }));
    (data.air_stations || []).forEach(station => { if (addAirStation(station, data, filename, registry)) units++; });
    (data.air_corridors || []).forEach(c => { if (addCorridor(c, data, filename)) corridors++; });
    drawWaypoints(data, registry, filename);
    drawFormations(data, registry, filename);
    drawZones(data, registry, filename);
    const goals = drawGoals(data, filename, registry);
    return { units, corridors, goals };
  }

  function renderSymbolLegend() {
    const target = document.getElementById('symbol-legend');
    const entries = [['fighter', 'Fighter'], ['bomber', 'Bomber'], ['attack', 'Attack aircraft'],
      ['helicopter', 'Helicopter'], ['transport', 'Transport / tanker'], ['uav', 'UAV'],
      ['carrier', 'Carrier'], ['cruiser', 'Cruiser'], ['destroyer', 'Destroyer'],
      ['frigate', 'Frigate'], ['corvette', 'Corvette'], ['patrol', 'Patrol craft'],
      ['fsg', 'FSG guided-missile corvette'],
      ['submarine', 'Submarine'], ['ssbn', 'SSBN'], ['ssgn', 'SSGN'], ['ssn', 'SSN'],
      ['ssk', 'SSK / conventional submarine'], ['sam', 'SAM site'], ['mine', 'Naval mine'], ['armor', 'Armored unit'],
      ['artillery', 'Artillery'], ['land', 'Infantry / land'], ['airbase', 'Air station']];
    target.innerHTML = entries.map(([type, label]) =>
      `<div class="legend-symbol"><span>${symbol('unit', 'friendly', type)}</span><small>${label}</small></div>`).join('');
  }

  function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c])); }
  function refreshSidebar() {
    const list = document.getElementById('scenario-list');
    list.innerHTML = scenarios.length ? scenarios.map(s => `<div class="scenario-item"><span class="scenario-dot" style="background:${s.color}"></span><span class="scenario-name" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span></div>`).join('') : '<p class="muted">No scenarios loaded.</p>';
    const tasking = document.getElementById('tasking-list');
    tasking.innerHTML = scenarios.length ? scenarios.map(s => `<article class="tasking-item"><strong>${escapeHtml(s.title || s.name)}</strong><p>${escapeHtml(s.tasking || 'No tasking text in this scenario.')}</p></article>`).join('') : '<p class="muted">Tasking text appears here after loading a scenario.</p>';
    document.getElementById('unit-count').textContent = scenarios.reduce((n, s) => n + s.units, 0);
    document.getElementById('corridor-count').textContent = scenarios.reduce((n, s) => n + s.corridors, 0);
    document.getElementById('goal-count').textContent = scenarios.reduce((n, s) => n + s.goals, 0);
  }
  function loadFiles(files) {
    [...files].filter(file => file.name.toLowerCase().endsWith('.json')).forEach(file => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          const data = JSON.parse(reader.result);
          const stats = render(data, file.name);
          scenarios.push({ name: file.name, title: data.metadata && data.metadata.missiontitle,
            tasking: data.metadata && data.metadata.taskingmessage, color: `hsl(${(scenarios.length * 83) % 360} 70% 60%)`, ...stats });
          refreshFilters(); applyFilters(); refreshSidebar(); message.classList.add('hidden');
          if (bounds.length) map.fitBounds(bounds, { padding: [35, 35], maxZoom: 12 });
        } catch (error) { message.textContent = `Could not read ${file.name}: ${error.message}`; message.classList.remove('hidden'); }
      };
      reader.readAsText(file);
    });
  }
  ['dragenter', 'dragover'].forEach(event => dropzone.addEventListener(event, e => { e.preventDefault(); dropzone.classList.add('dragging'); }));
  ['dragleave', 'drop'].forEach(event => dropzone.addEventListener(event, e => { e.preventDefault(); dropzone.classList.remove('dragging'); }));
  dropzone.addEventListener('drop', e => loadFiles(e.dataTransfer.files));
  input.addEventListener('change', e => loadFiles(e.target.files));
  ['type', 'country', 'class'].forEach(name => document.getElementById(`filter-${name}`).addEventListener('change', event => {
    filterState[name] = event.target.value; applyFilters();
  }));
  document.querySelectorAll('.legend-toggle').forEach(button => button.addEventListener('click', () => {
    const name = button.dataset.toggle;
    const group = overlays[name];
    if (!group) return;
    if (map.hasLayer(group)) { map.removeLayer(group); button.classList.remove('active'); button.setAttribute('aria-pressed', 'false'); }
    else { group.addTo(map); button.classList.add('active'); button.setAttribute('aria-pressed', 'true'); }
  }));
  document.getElementById('clear-button').addEventListener('click', () => { Object.values(overlays).forEach(group => group.clearLayers()); unitEntries.length = 0; scenarios.length = 0; bounds.length = 0; document.getElementById('unit-details').textContent = 'Click a unit on the map.'; document.getElementById('unit-details').classList.add('empty'); refreshFilters(); refreshSidebar(); message.textContent = 'Load a scenario JSON to begin.'; message.classList.remove('hidden'); map.setView([20, 0], 2); });
  renderSymbolLegend();
  refreshSidebar();
})();
