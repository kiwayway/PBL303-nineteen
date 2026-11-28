/* UI upload foto profil: pilih/seret, validasi, pratinjau. Server tetap memvalidasi ulang. */
(() => {
  const root = document.querySelector('[data-avatar-upload]');
  if (!root) return;

  const input = root.querySelector('input[type="file"]');
  const drop = root.querySelector('[data-avatar-drop]');
  const pickBtn = root.querySelector('[data-avatar-pick]');
  const img = root.querySelector('[data-avatar-img]');
  const initial = root.querySelector('[data-avatar-initial]');
  const errorEl = root.querySelector('[data-avatar-error]');
  const nameEl = root.querySelector('[data-avatar-name]');
  const actions = root.querySelector('[data-avatar-actions]');
  const cancelBtn = root.querySelector('[data-avatar-cancel]');
  const maxBytes = Number(root.dataset.maxBytes) || 2 * 1024 * 1024;
  const allowed = ['image/jpeg', 'image/png', 'image/webp'];

  const originalSrc = img.getAttribute('src') || '';
  let previewUrl = null;

  const show = (el, on) => el.classList.toggle('hidden', !on);
  const fmtSize = b => (b >= 1048576 ? (b / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1024)) + ' KB');

  function setError(msg) {
    errorEl.textContent = msg || '';
    show(errorEl, !!msg);
  }

  function reset() {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = null;
    input.value = '';
    if (originalSrc) { img.src = originalSrc; show(img, true); }
    else { img.removeAttribute('src'); show(img, false); }
    if (initial) show(initial, !originalSrc);
    show(nameEl, false);
    actions.classList.add('hidden');
    actions.classList.remove('flex');
  }

  function handle(file) {
    setError('');
    if (!file) { reset(); return; }
    if (!allowed.includes(file.type)) { reset(); setError('Format foto harus JPG, PNG, atau WebP.'); return; }
    if (file.size > maxBytes) { reset(); setError('Ukuran foto melebihi batas 2 MB (' + fmtSize(file.size) + ').'); return; }
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    previewUrl = URL.createObjectURL(file);
    img.src = previewUrl;
    show(img, true);
    if (initial) show(initial, false);
    nameEl.textContent = file.name + ' · ' + fmtSize(file.size);
    show(nameEl, true);
    actions.classList.remove('hidden');
    actions.classList.add('flex');
  }

  const open = () => input.click();
  pickBtn.addEventListener('click', open);
  drop.addEventListener('click', open);
  drop.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); }
  });
  input.addEventListener('change', () => handle(input.files[0]));
  cancelBtn.addEventListener('click', () => { setError(''); reset(); });

  ['dragenter', 'dragover'].forEach(t => drop.addEventListener(t, e => {
    e.preventDefault(); drop.dataset.over = 'true';
  }));
  ['dragleave', 'drop'].forEach(t => drop.addEventListener(t, e => {
    e.preventDefault(); drop.dataset.over = 'false';
  }));
  drop.addEventListener('drop', e => {
    const file = e.dataTransfer && e.dataTransfer.files[0];
    if (!file) return;
    const dt = new DataTransfer();   // isi input agar form mengirim file yang diseret
    dt.items.add(file);
    input.files = dt.files;
    handle(file);
  });
})();
