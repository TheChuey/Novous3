const DOCUMENT_EXTENSIONS = /\.(pdf|docx?|xlsx?|pptx?)(?:[?#].*)?$/i;
const PREVIEWABLE_LANGUAGES = new Set(['html', 'htm', 'svg']);

function safeUrl(value) {
  try {
    const url = new URL(value, window.location.href);
    return ['http:', 'https:', 'mailto:'].includes(url.protocol) ? url : null;
  } catch {
    return null;
  }
}

function createLink(label, href) {
  const url = safeUrl(href);
  if (!url) return document.createTextNode(label);
  const link = document.createElement('a');
  link.textContent = label;
  link.href = url.href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  if (DOCUMENT_EXTENSIONS.test(url.pathname + url.search)) {
    link.className = 'md-doc-button btn btn-sm';
    link.textContent = `📄 View ${label}`;
    link.title = url.href;
  }
  return link;
}

export function appendMarkdownInline(parent, text) {
  const pattern = /(`[^`]+`|https?:\/\/[^\s<>"'`]+|\*\*[^*]+\*\*|(?<![\w])__[^_]+__(?![\w])|\*[^*]+\*|(?<![\w])_[^_]+_(?![\w])|~~[^~]+~~|\[([^\]]+)\]\(([^)\s]+)\))/g;
  let lastIndex = 0;
  let match;

  while ((match = pattern.exec(text)) !== null) {
    parent.appendChild(document.createTextNode(text.slice(lastIndex, match.index)));
    const token = match[0];
    let element;

    if (token.startsWith('`')) {
      element = document.createElement('code');
      element.textContent = token.slice(1, -1);
    } else if (token.startsWith('**') || token.startsWith('__')) {
      element = document.createElement('strong');
      element.textContent = token.slice(2, -2);
    } else if (token.startsWith('~~')) {
      element = document.createElement('del');
      element.textContent = token.slice(2, -2);
    } else if (token.startsWith('*') || token.startsWith('_')) {
      element = document.createElement('em');
      element.textContent = token.slice(1, -1);
    } else if (match[2] !== undefined) {
      element = createLink(match[2], match[3]);
    } else {
      const trimmed = token.replace(/[.,;:!?)\]]+$/, '');
      element = createLink(trimmed, trimmed);
      parent.appendChild(element);
      element = document.createTextNode(token.slice(trimmed.length));
    }

    parent.appendChild(element);
    lastIndex = pattern.lastIndex;
  }

  parent.appendChild(document.createTextNode(text.slice(lastIndex)));
}

function createCodeBlock(code, rawLanguage) {
  const language = (rawLanguage || '').trim().split(/\s+/, 1)[0].toLowerCase();
  const block = document.createElement('div');
  block.className = 'md-code-block';

  const header = document.createElement('div');
  header.className = 'md-code-header';
  const label = document.createElement('span');
  label.className = 'md-code-language';
  label.textContent = language || 'text';
  const actions = document.createElement('div');
  actions.className = 'md-code-actions';

  const copyButton = document.createElement('button');
  copyButton.type = 'button';
  copyButton.className = 'btn btn-sm btn-ghost';
  copyButton.textContent = 'Copy';
  copyButton.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(code);
      copyButton.textContent = 'Copied!';
    } catch {
      copyButton.textContent = 'Copy failed';
    }
    setTimeout(() => { copyButton.textContent = 'Copy'; }, 2000);
  });
  actions.appendChild(copyButton);

  const pre = document.createElement('pre');
  const codeElement = document.createElement('code');
  if (language) codeElement.className = `language-${language}`;
  codeElement.textContent = code;
  pre.appendChild(codeElement);

  let frame = null;
  if (PREVIEWABLE_LANGUAGES.has(language)) {
    const previewButton = document.createElement('button');
    previewButton.type = 'button';
    previewButton.className = 'btn btn-sm';
    previewButton.textContent = 'Preview page';
    previewButton.addEventListener('click', () => {
      if (frame) {
        frame.remove();
        frame = null;
        pre.hidden = false;
        previewButton.textContent = 'Preview page';
        return;
      }
      frame = document.createElement('iframe');
      frame.className = 'md-html-preview';
      frame.title = 'HTML preview';
      // No same-origin access: scripts in the reply cannot touch the app.
      frame.setAttribute('sandbox', 'allow-scripts');
      frame.srcdoc = language === 'svg'
        ? `<!doctype html><body style="margin:0">${code}</body>`
        : code;
      pre.hidden = true;
      block.appendChild(frame);
      previewButton.textContent = 'Show code';
    });
    actions.appendChild(previewButton);
  }

  header.append(label, actions);
  block.append(header, pre);
  return block;
}

export function appendMarkdown(parent, markdown) {
  const lines = String(markdown ?? '').replace(/\r\n?/g, '\n').split('\n');
  let paragraph = [];
  let list = null;
  let codeLines = null;
  let codeLanguage = '';
  const isTableDelimiter = line =>
    /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
  const splitTableRow = line =>
    line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const block = document.createElement('p');
    paragraph.forEach((line, index) => {
      if (index) block.appendChild(document.createTextNode(' '));
      appendMarkdownInline(block, line);
    });
    parent.appendChild(block);
    paragraph = [];
  };
  const flushList = () => {
    if (list) parent.appendChild(list);
    list = null;
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const fence = line.match(/^\s*```([^`]*)$/);
    if (codeLines) {
      if (fence) {
        parent.appendChild(createCodeBlock(codeLines.join('\n'), codeLanguage));
        codeLines = null;
      } else {
        codeLines.push(line);
      }
      continue;
    }
    if (fence) {
      flushParagraph();
      flushList();
      codeLines = [];
      codeLanguage = fence[1];
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    if (/^\s{0,3}(?:---+|___+|\*\*\*+)\s*$/.test(line)) {
      flushParagraph();
      flushList();
      parent.appendChild(document.createElement('hr'));
      continue;
    }

    if (line.includes('|') && lines[index + 1] && isTableDelimiter(lines[index + 1])) {
      flushParagraph();
      flushList();
      const headers = splitTableRow(line);
      const table = document.createElement('table');
      table.className = 'prompt-markdown-table';
      const thead = document.createElement('thead');
      const headerRow = document.createElement('tr');
      headers.forEach(text => {
        const cell = document.createElement('th');
        cell.scope = 'col';
        appendMarkdownInline(cell, text);
        headerRow.appendChild(cell);
      });
      thead.appendChild(headerRow);
      table.appendChild(thead);

      const tbody = document.createElement('tbody');
      for (index += 2; index < lines.length && lines[index].includes('|'); index += 1) {
        const row = document.createElement('tr');
        splitTableRow(lines[index]).forEach((text, cellIndex) => {
          const cell = document.createElement('td');
          if (headers[cellIndex]) appendMarkdownInline(cell, text);
          row.appendChild(cell);
        });
        tbody.appendChild(row);
      }
      table.appendChild(tbody);
      parent.appendChild(table);
      index -= 1;
      continue;
    }

    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
    if (heading) {
      flushParagraph();
      flushList();
      const element = document.createElement(`h${heading[1].length}`);
      appendMarkdownInline(element, heading[2]);
      parent.appendChild(element);
      continue;
    }

    const item = line.match(/^\s{0,3}([-*+]|\d+[.)])\s+(.+)$/);
    if (item) {
      flushParagraph();
      const ordered = /^\d/.test(item[1]);
      if (!list || (list.tagName === 'OL') !== ordered) {
        flushList();
        list = document.createElement(ordered ? 'ol' : 'ul');
      }
      const element = document.createElement('li');
      const task = item[2].match(/^\[([ xX])\]\s+(.+)$/);
      if (task) {
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = task[1].toLowerCase() === 'x';
        checkbox.disabled = true;
        checkbox.setAttribute('aria-label', checkbox.checked ? 'Completed task' : 'Incomplete task');
        element.classList.add('prompt-markdown-task');
        element.append(checkbox, document.createTextNode(' '));
        appendMarkdownInline(element, task[2]);
      } else {
        appendMarkdownInline(element, item[2]);
      }
      list.appendChild(element);
      continue;
    }

    flushList();
    const quote = line.match(/^\s{0,3}>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      const blockquote = document.createElement('blockquote');
      appendMarkdownInline(blockquote, quote[1]);
      parent.appendChild(blockquote);
      continue;
    }

    paragraph.push(line.trim());
  }

  if (codeLines) parent.appendChild(createCodeBlock(codeLines.join('\n'), codeLanguage));
  flushParagraph();
  flushList();
}
