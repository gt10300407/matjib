(() => {
  const MOBILE_QUERY = '(max-width: 767px)';
  const app = document.querySelector('.app');
  const header = app?.querySelector('.top');
  if (!app || !header) return;

  const nav = document.createElement('nav');
  nav.className = 'mobileViewTabs';
  nav.setAttribute('aria-label', '모바일 보기 전환');
  nav.innerHTML = `
    <button type="button" data-mobile-view="map" aria-selected="true">🗺️ 지도</button>
    <button type="button" data-mobile-view="list" aria-selected="false">🍴 맛집 목록</button>
  `;
  header.insertAdjacentElement('afterend', nav);

  const mq = window.matchMedia(MOBILE_QUERY);
  const buttons = [...nav.querySelectorAll('[data-mobile-view]')];
  let currentView = 'map';
  let resizeTimer = null;

  function redrawMapForViewport() {
    if (!mq.matches) return;
    try {
      if (typeof mode === 'undefined') return;
      if (mode === 'korea' && typeof drawKorea === 'function') {
        drawKorea();
        return;
      }
      if (mode === 'province' && typeof provinces !== 'undefined' && provinces?.features && typeof pName === 'function' && typeof drawProvince === 'function') {
        const feature = provinces.features.find(f => pName(f) === selectedProvince);
        if (feature) drawProvince(feature);
      }
    } catch (err) {
      console.debug('[mobile] map redraw skipped', err);
    }
  }

  function applyView(view, { redraw = true } = {}) {
    currentView = view === 'list' ? 'list' : 'map';
    if (!mq.matches) {
      app.classList.remove('mobile-view-map', 'mobile-view-list');
      buttons.forEach(btn => {
        btn.classList.remove('active');
        btn.setAttribute('aria-selected', 'false');
      });
      return;
    }

    app.classList.toggle('mobile-view-map', currentView === 'map');
    app.classList.toggle('mobile-view-list', currentView === 'list');
    buttons.forEach(btn => {
      const active = btn.dataset.mobileView === currentView;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    if (currentView === 'map' && redraw) {
      requestAnimationFrame(() => requestAnimationFrame(redrawMapForViewport));
    }
  }

  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      applyView(btn.dataset.mobileView);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });

  function syncViewport() {
    if (mq.matches) applyView(currentView, { redraw: currentView === 'map' });
    else applyView(currentView, { redraw: false });
  }

  if (typeof mq.addEventListener === 'function') mq.addEventListener('change', syncViewport);
  else mq.addListener(syncViewport);

  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      syncViewport();
      if (mq.matches && currentView === 'map') redrawMapForViewport();
    }, 180);
  }, { passive: true });

  const breadcrumb = document.getElementById('breadcrumb');
  if (breadcrumb) {
    let previous = breadcrumb.textContent || '';
    const observer = new MutationObserver(() => {
      const next = breadcrumb.textContent || '';
      const citySelected = next.split('>').length >= 3;
      if (mq.matches && next !== previous && citySelected) {
        applyView('list', { redraw: false });
        window.scrollTo({ top: 0, behavior: 'smooth' });
      }
      previous = next;
    });
    observer.observe(breadcrumb, { childList: true, subtree: true, characterData: true });
  }

  applyView('map');
})();
