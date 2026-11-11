/** Manage workspace folders and browse their files. */
import { Api } from '../api.js';
import { navigate } from '../shell.js';
import { renderTree } from '../tree.js';

export function renderFileManagerView(container) {
  container.innerHTML = `
    <div class="sq-page">
      <header class="sq-page-head">
        <div>
          <h2>File Manager</h2>
          <p class="text-muted">Configure allowed workspace folders and browse their files.</p>
        </div>
      </header>
      <div class="ov-layout">
        <section class="card">
          <h3>Workspace folders</h3>
          <p class="text-muted small">Add absolute folder paths, one per line. Removing a folder only removes it from the picker.</p>
          <ul class="root-path-list" data-role="roots" aria-label="Configured workspace folders"></ul>
          <label class="small" for="file-manager-paths">Folder paths</label>
          <textarea id="file-manager-paths" class="form-input root-path-input" rows="3"
            placeholder="E:\\Projects"></textarea>
          <div class="root-management-actions">
            <button class="btn btn-primary" type="button" data-roots-action="add">Add folders</button>
            <button class="btn btn-danger" type="button" data-roots-action="remove">Remove folders</button>
          </div>
          <p class="small" data-role="status" role="status" aria-live="polite"></p>
        </section>
        <section class="card dash-tree-card">
          <h3>Workspace files</h3>
          <div class="tree-pane" data-role="tree"></div>
        </section>
      </div>
    </div>`;

  const rootList = container.querySelector('[data-role="roots"]');
  const input = container.querySelector('#file-manager-paths');
  const status = container.querySelector('[data-role="status"]');
  const actionButtons = [...container.querySelectorAll('[data-roots-action]')];
  const tree = renderTree(container.querySelector('[data-role="tree"]'), {
    manageRoots: true,
    onSelect: path => navigate('editor', { path, managedFile: true })
  });

  function setStatus(message, kind = 'muted') {
    status.textContent = message;
    status.className = `small text-${kind}`;
  }

  async function refreshRoots() {
    const config = await Api.getFileManagerRoots();
    const createItem = (root, missing) => {
      const item = document.createElement('li');
      const name = document.createElement('strong');
      const path = document.createElement('code');
      name.textContent = root.name;
      path.textContent = root.path;
      item.append(name, path);
      if (missing) {
        item.classList.add('root-missing');
        const badge = document.createElement('span');
        badge.className = 'badge badge-danger';
        badge.textContent = 'missing';
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'btn btn-sm btn-danger';
        remove.textContent = 'Remove';
        remove.dataset.removeRoot = root.path;
        item.append(badge, remove);
      }
      return item;
    };
    rootList.replaceChildren(
      ...config.roots.map(root => createItem(root, false)),
      ...(config.missing || []).map(root => createItem(root, true))
    );
  }

  rootList.addEventListener('click', async event => {
    const button = event.target.closest('[data-remove-root]');
    if (!button) return;
    button.disabled = true;
    try {
      await Api.manageFileManagerRoots('remove', [button.dataset.removeRoot]);
      await refreshRoots();
      await tree.reload();
      setStatus('Removed the missing folder from the picker.', 'success');
    } catch (error) {
      setStatus(`Could not remove folder: ${error.message}`, 'danger');
      button.disabled = false;
    }
  });

  actionButtons.forEach(button => button.addEventListener('click', async () => {
    const paths = input.value.split(/\r?\n/).map(path => path.trim()).filter(Boolean);
    if (!paths.length) {
      setStatus('Paste at least one absolute folder path.', 'danger');
      input.focus();
      return;
    }
    actionButtons.forEach(item => { item.disabled = true; });
    setStatus('Updating workspace folders…');
    try {
      const action = button.dataset.rootsAction;
      const result = await Api.manageFileManagerRoots(action, paths);
      const changed = action === 'add' ? result.added : result.removed;
      setStatus(`${action === 'add' ? 'Added' : 'Removed'} ${changed.length} folder(s).`, 'success');
      await refreshRoots();
      await tree.reload();
    } catch (error) {
      setStatus(`Could not update workspace folders: ${error.message}`, 'danger');
    } finally {
      actionButtons.forEach(item => { item.disabled = false; });
    }
  }));

  refreshRoots().catch(error => setStatus(`Could not load workspace folders: ${error.message}`, 'danger'));
}
