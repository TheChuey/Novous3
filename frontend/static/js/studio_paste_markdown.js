const SECTION_NAMES = [
  'role', 'purpose', 'boundaries', 'instructions', 'tools',
  'context & memory', 'context', 'memory', 'workflow', 'output format', 'output', 'examples',
];

function inlineCodeMask(text) {
  const mask = Array(text.length).fill(false);
  let delimiter = 0;
  for (let i = 0; i < text.length; i++) {
    if (text[i] !== '`') {
      mask[i] = delimiter > 0;
      continue;
    }
    let end = i + 1;
    while (text[end] === '`') end++;
    const length = end - i;
    mask.fill(true, i, end);
    if (!delimiter) delimiter = length;
    else if (delimiter === length) delimiter = 0;
    i = end - 1;
  }
  return delimiter ? null : mask;
}

function formatFlattenedAgent(text) {
  const match = text.match(/^\\?---[ \t]+(.+?)[ \t]+---[ \t]+(#[ \t]+.+?)(\n?)$/);
  if (!match) return null;
  const [, rawHeader, rawBody, ending] = match;
  const header = rawHeader.replace(/\\_/g, '_');
  const body = rawBody.replace(/\\`([^`]*?)\\`/g, (_, code) => `\`${code.replace(/\\_/g, '_')}\``);
  if (/['"`#\n]/.test(header)) return null;

  const fields = [...header.matchAll(/(?:^|[ \t]+)(id|name|description|mode|model|tools|environment|version):[ \t]+/g)];
  if (fields.length < 3 || fields[0].index !== 0 || fields[0][1] !== 'id'
      || new Set(fields.map(field => field[1])).size !== fields.length) return null;
  const lines = [];
  for (let i = 0; i < fields.length; i++) {
    const field = fields[i];
    const value = header.slice(field.index + field[0].length, fields[i + 1]?.index ?? header.length).trim();
    if (!value) return null;
    if (field[1] === 'tools') {
      if (value === '[]') {
        lines.push('tools: []');
      } else {
        if (!/^(?:-[ \t]+[A-Za-z0-9_.-]+)(?:[ \t]+-[ \t]+[A-Za-z0-9_.-]+)*$/.test(value)) return null;
        lines.push('tools:', ...value.split(/[ \t]+(?=-[ \t]+)/).map(item => `  ${item}`));
      }
    } else {
      if (/:\s|(?:^|\s)[{}\[\],&*!|>%@`]/.test(value)) return null;
      lines.push(`${field[1]}: ${value}`);
    }
  }

  const markers = [];
  const bodyCode = inlineCodeMask(body);
  if (!bodyCode) return null;
  for (let i = 0; i < body.length; i++) {
    if (!bodyCode[i] && (i === 0 || body[i - 1] === ' ') && body[i] === '#') {
      const marker = body.slice(i).match(/^(#{1,6})[ \t]+/);
      if (marker) { markers.push({ start: i, end: i + marker[0].length, level: marker[1] }); i += marker[0].length - 1; }
    }
  }
  if (markers.length < 2 || markers[0].start !== 0 || markers[0].level !== '#') return null;

  const sections = [];
  for (let i = 0; i < markers.length; i++) {
    const marker = markers[i];
    const value = body.slice(marker.end, markers[i + 1]?.start ?? body.length).trim();
    if (!value) return null;
    if (i === 0) { sections.push(`# ${value}`); continue; }
    const name = SECTION_NAMES.find(candidate =>
      value.toLowerCase().startsWith(candidate) &&
      (value.length === candidate.length || /[ \t]/.test(value[candidate.length])));
    if (!name) return null;
    let content = value.slice(name.length).trim();
    const positions = [];
    const contentCode = inlineCodeMask(content);
    if (!contentCode) return null;
    for (let j = 0; j < content.length - 2; j++) {
      if (!contentCode[j] && content.slice(j, j + 3) === ' - ') positions.push(j);
    }
    if (content.startsWith('- ')) positions.unshift(-1);
    if (positions.length >= 2 && ['boundaries', 'instructions', 'tools', 'workflow'].includes(name)) {
      const prefix = positions[0] === -1 ? '' : content.slice(0, positions[0]).trim();
      const list = positions.map((position, index) =>
        content.slice(position === -1 ? 0 : position + 1, positions[index + 1] ?? content.length).trim());
      content = [prefix, list.join('\n')].filter(Boolean).join('\n\n');
    }
    sections.push(`${marker.level} ${value.slice(0, name.length)}${content ? `\n\n${content}` : ''}`);
  }
  return `---\n${lines.join('\n')}\n---\n\n${sections.join('\n\n')}${ending}`;
}

export function formatPastedMarkdown(text) {
  const normalized = text.replace(/\r\n?/g, '\n');
  const flattened = formatFlattenedAgent(normalized);
  if (flattened !== null) return flattened;
  let fence = null;
  const formatted = normalized.split('\n').map(line => {
    const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
    if (marker) {
      if (!fence) fence = marker[1];
      else if (marker[1][0] === fence[0] && marker[1].length >= fence.length
          && line.slice(marker[0].length).trim() === '') fence = null;
      return line;
    }
    if (fence) return line;
    const flattenedLine = formatFlattenedAgent(line);
    if (flattenedLine !== null) return flattenedLine;
    const heading = line.match(/^(ROLE|PURPOSE|BOUNDARIES|INSTRUCTIONS|TOOLS|CONTEXT|MEMORY|WORKFLOW|OUTPUT|EXAMPLES):?[ \t]*$/);
    const labeledHeading = line.match(/^(role|purpose|boundaries|instructions|tools|context|memory|workflow|output|examples):[ \t]*$/i);
    return heading || labeledHeading ? `## ${(heading || labeledHeading)[1]}` : line;
  }).join('\n');
  return formatted === normalized ? text : formatted;
}
