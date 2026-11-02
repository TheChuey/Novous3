/**
 * Knowledge-source scan warnings (e.g. Google Docs links that cannot be indexed),
 * rendered as a warning line with an expandable list of the affected files.
 */
export function ragWarningNodes(warnings) {
  return (warnings || []).map(warning => {
    const block = document.createElement('div');
    block.className = 'rag-warning small text-warning';
    block.setAttribute('role', 'note');
    const message = document.createElement('span');
    message.textContent = `⚠ ${warning.message}`;
    block.appendChild(message);
    const paths = warning.paths || [];
    if (paths.length) {
      const details = document.createElement('details');
      const summary = document.createElement('summary');
      summary.textContent = `Show ${paths.length} file(s)`;
      const list = document.createElement('ul');
      paths.forEach(path => {
        const item = document.createElement('li');
        item.textContent = path.split('/').pop();
        item.title = path;
        list.appendChild(item);
      });
      details.append(summary, list);
      block.appendChild(details);
    }
    return block;
  });
}
