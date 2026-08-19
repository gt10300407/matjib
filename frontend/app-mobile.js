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

  // Mobile is utility-first: almost no decorative motion while core behavior is being stabilized.
  const motionStyle = document.createElement('style');
  motionStyle.textContent = `
    @media (max-width:767px){
      *,*::before,*::after{
        animation-duration:.001ms!important;
        animation-delay:0ms!important;
        animation-iteration-count:1!important;
        transition-duration:.001ms!important;
        transition-delay:0ms!important;
        scroll-behavior:auto!important;
      }
    }
  `;
  document.head.appendChild(motionStyle);

  function syncMotionPolicy() {
    if (window.gsap?.globalTimeline?.timeScale) {
      window.gsap.globalTimeline.timeScale(mq.matches ? 1000 : 1);
    }
  }
  syncMotionPolicy();

  /*
   * Desktop map geometry uses a minimum 700px SVG viewBox. Mobile uses the
   * actual rendered map size. The override is installed before the async
   * GeoJSON load finishes, so the first real map draw already uses mobile size.
   */
  const desktopDims = typeof dims === 'function' ? dims : null;
  const desktopDrawKorea = typeof drawKorea === 'function' ? drawKorea : null;
  const desktopDrawProvince = typeof drawProvince === 'function' ? drawProvince : null;

  if (desktopDims) {
    dims = function responsiveMapDims() {
      if (!mq.matches) return desktopDims();
      const el = document.getElementById('map');
      const r = el?.getBoundingClientRect?.() || { width: 0, height: 0 };
      return [
        Math.max(300, Math.round(r.width || window.innerWidth || 360)),
        Math.max(420, Math.round(r.height || window.innerHeight * 0.62 || 520)),
      ];
    };
  }

  function scaleMobileMapGroup(selector, scale) {
    if (!mq.matches || typeof svg === 'undefined' || typeof dims !== 'function') return;
    const target = svg.select(selector);
    if (target.empty()) return;
    const group = d3.select(target.node().parentNode);
    if (group.empty()) return;
    const [w, h] = dims();
    group.attr('transform', `translate(${w / 2} ${h / 2}) scale(${scale}) translate(${-w / 2} ${-h / 2})`);
  }

  if (desktopDrawKorea) {
    drawKorea = function responsiveDrawKorea(...args) {
      const result = desktopDrawKorea.apply(this, args);
      if (mq.matches && typeof provinces !== 'undefined' && provinces?.features?.length) {
        // Apply final scale in the same paint cycle: no small-map -> large-map pop.
        scaleMobileMapGroup('.province', 1.32);
      }
      return result;
    };
  }

  if (desktopDrawProvince) {
    drawProvince = function responsiveDrawProvince(...args) {
      const result = desktopDrawProvince.apply(this, args);
      if (mq.matches) {
        scaleMobileMapGroup('.municipality', 1.22);
      }
      return result;
    };
  }

  function redrawMapForViewport() {
    if (!mq.matches) return;
    try {
      if (typeof mode === 'undefined') return;
      if (mode === 'korea' && typeof drawKorea === 'function') {
        // Critical startup guard: never draw the crude fallback while GeoJSON is still loading.
        if (typeof provinces === 'undefined') return;
        if (!provinces?.features?.length) return;
        drawKorea();
        return;
      }
      if (mode === 'province' && typeof provinces !== 'undefined' && provinces?.features?.length && typeof pName === 'function' && typeof drawProvince === 'function') {
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

    if (currentView === 'map' && redraw) redrawMapForViewport();
  }

  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      applyView(btn.dataset.mobileView);
      window.scrollTo({ top: 0, behavior: 'auto' });
    });
  });

  function syncViewport() {
    syncMotionPolicy();
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
    }, 120);
  }, { passive: true });

  const breadcrumb = document.getElementById('breadcrumb');
  if (breadcrumb) {
    let previous = breadcrumb.textContent || '';
    const observer = new MutationObserver(() => {
      const next = breadcrumb.textContent || '';
      const citySelected = next.split('>').length >= 3;
      if (mq.matches && next !== previous && citySelected) {
        applyView('list', { redraw: false });
        window.scrollTo({ top: 0, behavior: 'auto' });
      }
      previous = next;
    });
    observer.observe(breadcrumb, { childList: true, subtree: true, characterData: true });
  }

  // Do not redraw on startup. app-settings.js will draw once after real GeoJSON arrives.
  applyView('map', { redraw: false });
})();
