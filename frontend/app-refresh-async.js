(() => {
  const oldButton = document.getElementById('reloadBtn');
  if (!oldButton) return;

  // app-taste.js historically attached a synchronous /region/refresh handler.
  // Replacing the node removes that listener without coupling this performance
  // layer to the rest of the taste UI implementation.
  const button = oldButton.cloneNode(true);
  oldButton.replaceWith(button);

  let pollToken = 0;

  function currentRegionMatches(province, city) {
    try {
      return selectedProvince === province && selectedCity === city;
    } catch (_) {
      return false;
    }
  }

  async function loadFreshSnapshot(province, city) {
    const q = new URLSearchParams({ province, city, limit: '300' });
    const response = await fetch(`${API_BASE}/api/v1/region?${q.toString()}`);
    const data = await readApiResponse(response);
    if (!currentRegionMatches(province, city)) return data;
    allRestaurants = Array.isArray(data.restaurants) ? data.restaurants : [];
    if (typeof renderRestaurantList === 'function') {
      renderRestaurantList(activeRestaurantFilter || '전체');
    }
    if (typeof loadStats === 'function') {
      await loadStats(province, city);
    }
    return data;
  }

  function setProgress(titleText, subText) {
    const title = document.getElementById('reloadTitle');
    const sub = document.getElementById('reloadSub');
    if (title && titleText) title.textContent = titleText;
    if (sub && subText) sub.textContent = subText;
  }

  async function pollStatus(province, city, token) {
    const q = new URLSearchParams({ province, city });
    for (let i = 0; i < 240 && token === pollToken; i += 1) {
      await new Promise(resolve => setTimeout(resolve, 1250));
      if (token !== pollToken) return;
      try {
        const response = await fetch(`${API_BASE}/api/v1/region/refresh-status?${q.toString()}`, { cache: 'no-store' });
        const job = await readApiResponse(response);
        if (!currentRegionMatches(province, city)) continue;

        if (job.status === 'queued' || job.status === 'running') {
          setProgress(
            '백그라운드 최신화 중',
            `기존 ${Number(job.cached_count || 0).toLocaleString('ko-KR')}곳은 바로 사용 가능 · 화면을 계속 써도 돼`
          );
          continue;
        }

        if (job.status === 'completed') {
          const snapshot = await loadFreshSnapshot(province, city);
          const result = job.result || {};
          const calls = `Google ${result.google_api_calls || 0}회 · Kakao ${result.kakao_api_calls || 0}회 · 공공 ${result.public_api_calls || 0}회`;
          setProgress(`최신화 완료 · ${Number(snapshot.verified_count || 0).toLocaleString('ko-KR')}곳`, calls);
          button.innerHTML = '<span>↻</span> 최신화';
          showToast('최신화 완료', '새 추천 데이터를 반영했어.');
          return;
        }

        if (job.status === 'failed' || job.status === 'cancelled') {
          const message = job.error?.message || '백그라운드 최신화가 중단됐어.';
          setProgress('최신화 실패', message);
          button.innerHTML = '<span>↻</span> 다시 최신화';
          showToast('최신화 실패', message);
          return;
        }
      } catch (err) {
        // A status request can briefly fail during Render wake/redeploy. The data
        // job may still be running, so keep polling instead of failing the UI.
        console.debug('[refresh-status]', err);
      }
    }
  }

  async function startRefresh() {
    if (!selectedCity) {
      showToast('지역을 먼저 선택해', '시·군을 선택한 뒤 추천 맛집을 최신화해.');
      return;
    }

    const province = selectedProvince;
    const city = selectedCity;
    const token = ++pollToken;
    button.innerHTML = '<span>↻</span> 백그라운드 최신화';
    setProgress('최신화 요청 중...', '현재 저장된 맛집은 그대로 보고 사용할 수 있어.');

    try {
      const response = await fetch(`${API_BASE}/api/v1/region/refresh-async`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ province, city, bbox: selectedBBox }),
      });
      const accepted = await readApiResponse(response);
      if (token !== pollToken) return;
      setProgress(
        accepted.already_running ? '이미 백그라운드 최신화 중' : '백그라운드 최신화 시작',
        `기존 ${Number(accepted.cached_count || 0).toLocaleString('ko-KR')}곳 즉시 사용 · 완료되면 자동 반영`
      );
      button.innerHTML = '<span>↻</span> 최신화 중';
      // Deliberately not disabled: clicking again is cheap because the server
      // deduplicates one in-flight task per region.
      pollStatus(province, city, token);
    } catch (err) {
      setProgress('최신화 요청 실패', err.message || String(err));
      button.innerHTML = '<span>↻</span> 다시 최신화';
      showToast('최신화 요청 실패', err.message || String(err));
    }
  }

  button.addEventListener('click', event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    startRefresh();
  }, true);
})();
