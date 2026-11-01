/**
 * Squad Architect canvas: draws a squad's lead, members and pattern-specific flow as inline SVG.
 *   sequential  lead → m1 → m2 → … → lead (synthesis)
 *   fanout      lead → every member in parallel → lead
 *   router      lead → route decision → chosen members → lead
 */
import { esc } from '../shell.js';

const NODE_W = 156;
const NODE_H = 58;
const GAP = 28;
const LEAD_Y = 24;
const MEMBER_Y = 210;

function clip(text, max) {
  const value = String(text || '');
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

function nodeSvg(node, x, y, { role, selected }) {
  const tools = node.tools?.length || 0;
  const subtitle = role === 'lead'
    ? `lead · ${tools} tool${tools === 1 ? '' : 's'}`
    : `${node.kind === 'subagent' ? 'sub-agent' : 'agent'} · ${tools} tool${tools === 1 ? '' : 's'}`;
  return `
    <g class="sq-node sq-node-${role === 'lead' ? 'lead' : node.kind}${selected ? ' is-selected' : ''}"
       data-node-id="${esc(node.id)}" tabindex="0" role="button" aria-label="${esc(node.name || node.id)} (${esc(subtitle)})">
      <rect x="${x}" y="${y}" width="${NODE_W}" height="${NODE_H}" rx="10"></rect>
      <text class="sq-node-title" x="${x + 12}" y="${y + 24}">${esc(clip(node.name || node.id, 19))}</text>
      <text class="sq-node-sub" x="${x + 12}" y="${y + 43}">${esc(subtitle)}</text>
    </g>`;
}

function edge(x1, y1, x2, y2, cls = '') {
  const midY = (y1 + y2) / 2;
  return `<path class="sq-edge ${cls}" d="M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2}" marker-end="url(#sq-arrow)"></path>`;
}

export function renderArchitectCanvas(host, blueprint, { selectedId = null, onSelect = () => {} } = {}) {
  const members = (blueprint.members || []).filter(Boolean);
  const pattern = blueprint.pattern || 'sequential';
  const count = Math.max(members.length, 1);
  const width = Math.max(560, count * NODE_W + (count + 1) * GAP);
  const height = MEMBER_Y + NODE_H + 70;
  const leadX = (width - NODE_W) / 2;
  const leadBottom = LEAD_Y + NODE_H;
  const leadCenter = width / 2;
  const startX = (width - (members.length * NODE_W + (members.length - 1) * GAP)) / 2;
  const memberX = index => startX + index * (NODE_W + GAP);

  const edges = [];
  const extras = [];
  if (members.length) {
    if (pattern === 'sequential') {
      edges.push(edge(leadCenter - 30, leadBottom, memberX(0) + NODE_W / 2, MEMBER_Y));
      members.forEach((_, i) => {
        if (i === members.length - 1) return;
        const y = MEMBER_Y + NODE_H / 2;
        edges.push(`<path class="sq-edge" d="M${memberX(i) + NODE_W},${y} L${memberX(i + 1)},${y}" marker-end="url(#sq-arrow)"></path>`);
      });
      const last = memberX(members.length - 1) + NODE_W / 2;
      edges.push(edge(last, MEMBER_Y, leadCenter + 30, leadBottom, 'sq-edge-return'));
      members.forEach((_, i) => {
        extras.push(`<text class="sq-step" x="${memberX(i) + NODE_W - 16}" y="${MEMBER_Y - 8}">${i + 1}</text>`);
      });
    } else if (pattern === 'fanout') {
      members.forEach((_, i) => {
        const cx = memberX(i) + NODE_W / 2;
        edges.push(edge(leadCenter - 12, leadBottom, cx - 12, MEMBER_Y));
        edges.push(edge(cx + 12, MEMBER_Y, leadCenter + 12, leadBottom, 'sq-edge-return'));
      });
      extras.push(`<text class="sq-pattern-label" x="${leadCenter}" y="${(leadBottom + MEMBER_Y) / 2}">parallel</text>`);
    } else {
      const dy = (leadBottom + MEMBER_Y) / 2;
      extras.push(`<polygon class="sq-router" points="${leadCenter},${dy - 18} ${leadCenter + 26},${dy} ${leadCenter},${dy + 18} ${leadCenter - 26},${dy}"></polygon>`);
      extras.push(`<text class="sq-pattern-label" x="${leadCenter + 34}" y="${dy + 4}" text-anchor="start">route</text>`);
      edges.push(`<path class="sq-edge" d="M${leadCenter},${leadBottom} L${leadCenter},${dy - 18}" marker-end="url(#sq-arrow)"></path>`);
      members.forEach((_, i) => {
        edges.push(edge(leadCenter, dy + 18, memberX(i) + NODE_W / 2, MEMBER_Y, 'sq-edge-optional'));
      });
    }
  }

  host.innerHTML = `
    <svg class="sq-canvas" viewBox="0 0 ${width} ${height}" role="group" aria-label="Squad structure">
      <defs>
        <marker id="sq-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" class="sq-arrow-head"></path>
        </marker>
      </defs>
      ${edges.join('')}
      ${extras.join('')}
      ${blueprint.lead ? nodeSvg(blueprint.lead, leadX, LEAD_Y, { role: 'lead', selected: selectedId === blueprint.lead.id }) : ''}
      ${members.map((m, i) => nodeSvg(m, memberX(i), MEMBER_Y, { role: 'member', selected: selectedId === m.id })).join('')}
      ${members.length ? '' : `<text class="sq-empty" x="${leadCenter}" y="${MEMBER_Y + 30}">No members yet — the lead works alone.</text>`}
    </svg>`;

  const select = target => {
    const node = target.closest('[data-node-id]');
    if (node) onSelect(node.dataset.nodeId);
  };
  host.querySelector('svg').addEventListener('click', event => select(event.target));
  host.querySelector('svg').addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      select(event.target);
    }
  });
}
