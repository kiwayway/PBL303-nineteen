(() => {
  const root = document.documentElement;
  const MINIMUM_LOADING_TIME = 800;
  let shouldShow = false;

  try {
    const navigation = performance.getEntriesByType?.('navigation')?.[0];
    const isRefresh = navigation?.type === 'reload';
    const sessionKey = 'nineteen.initial-load-seen';
    const isFirstVisitInTab =
      sessionStorage.getItem(sessionKey) !== '1';

    shouldShow = isFirstVisitInTab || isRefresh;
    sessionStorage.setItem(sessionKey, '1');
  } catch (_) {
    shouldShow = true;
  }

  if (!shouldShow) return;

  const loadingStartedAt = Date.now();
  root.classList.add('site-loader-active');

  let loadingFinished = false;

  const finishLoading = () => {
    if (loadingFinished) return;

    const elapsed = Date.now() - loadingStartedAt;
    const remaining = Math.max(0, MINIMUM_LOADING_TIME - elapsed);

    window.setTimeout(() => {
      if (loadingFinished) return;
      loadingFinished = true;

      root.classList.remove('site-loader-active');

      const loader = document.getElementById('site-loading-screen');
      if (loader) {
        loader.setAttribute('aria-hidden', 'true');
      }
    }, remaining);
  };

  if (document.readyState === 'complete') {
    finishLoading();
  } else {
    window.addEventListener('load', finishLoading, { once: true });
  }
})();