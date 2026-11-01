const ICON_MARKUP = {
  'chevron-left': '<path d="m15 18-6-6 6-6"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  clipboard: '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M8 11h8"/><path d="M8 15h5"/>',
  eraser: '<path d="m7 21-4.3-4.3a1 1 0 0 1 0-1.4l10-10a1 1 0 0 1 1.4 0l5.6 5.6a1 1 0 0 1 0 1.4L13 19"/><path d="M22 21H7"/><path d="m5 11 9 9"/>',
  'folder-open': '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
  'more-horizontal': '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  play: '<path d="m6 3 14 9-14 9z"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  'save-as': '<path d="M14 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v5"/><path d="M7 3v5h8"/><path d="M17 15v6"/><path d="M14 18h6"/>',
  square: '<rect x="5" y="5" width="14" height="14" rx="2"/>',
  activity: '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  bug: '<path d="M12 20v-8"/><path d="M8 20v-4a4 4 0 0 1 8 0v4"/><path d="M19 8h-3"/><path d="M8 8H5"/><path d="M19 12h-3"/><path d="M8 12H5"/><path d="M16 4a4 4 0 0 0-8 0"/>',
  'check-square': '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m9 12 2 2 4-4"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  'file-code': '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="m10 13-2 2 2 2"/><path d="m14 13 2 2-2 2"/>',
  gauge: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/><path d="M12 14a2 2 0 1 0-2-2"/>',
  'play-circle': '<circle cx="12" cy="12" r="10"/><path d="m10 8 6 4-6 4z"/>',
  refresh: '<path d="M20 7v5h-5"/><path d="M4 17v-5h5"/><path d="M5.6 9a7 7 0 0 1 11.55-2.6L20 12"/><path d="M4 12l2.85 5.6A7 7 0 0 0 18.4 15"/>',
  reset: '<path d="M3 12a9 9 0 1 0 2.64-6.36L3 8"/><path d="M3 3v5h5"/>',
  save: '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><path d="M17 21v-8H7v8"/><path d="M7 3v5h8"/>',
  send: '<path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/>',
  terminal: '<rect x="2" y="3" width="20" height="18" rx="2"/><path d="m7 9 3 3-3 3"/><path d="M13 15h4"/>',
  'trash-2': '<path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="m19 6-1 14H6L5 6"/><path d="M10 11v5"/><path d="M14 11v5"/>'
};

export function createIcon(name) {
  const markup = ICON_MARKUP[name];
  if (!markup) throw new Error(`Unknown icon: ${name}`);

  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', '16');
  svg.setAttribute('height', '16');
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '2');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  svg.classList.add('chat-icon');
  svg.innerHTML = markup;
  return svg;
}

/** The same icon as markup, for HTML templates. */
export function iconSvg(name, size = 16) {
  const markup = ICON_MARKUP[name];
  if (!markup) throw new Error(`Unknown icon: ${name}`);
  return `<svg class="icon" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor"`
    + ` stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${markup}</svg>`;
}
