(() => {
  const POLL_MS = 1800;
  let pollToken = 0;

  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  function buttonState(text, loading = false) {
    const btn = document.getElementById('reloadBtn');
    if (!btn) return;
    btn.classList.toggle('loading', loading);
    btn.innerHTML = text;
  }

  function setRefreshCopy(title, sub) {
    const titleEl = document.getElementById('reloadTitle');
    const subEl = document.getElementById('reloadSub');
    if (titleEl) titleEl.textContent = title;
    if (subEl) subEl.textContent = sub;
  }

  async function reloadCachedRegion(province, city) {
    if (typeof selectedProvince !== 'undefined' && typeof selectedCity !== 'undefined') {
      if (selectedProvince === province && selectedCity === city && typeof setPanel === 'function') {
        await Promise.resolve(setPanel(city));
      }
    }
    if (typeof loadStats === 'function') {
      await Promise.resolve(loadStats(province, city));
    }
    if (typeof loadRuntimeStatus === 'function') {
      await Promise.resolve(loadRuntimeStatus());
    }
  }

  async function pollRefresh(province, city, token) {
    while (token === pollToken) {
      await sleep(POLL_MS);
      if (token !== pollToken) return;
      try {
        const params = new URLSearchParams({province, city});
        const res = await fetch(`${API_BASE}/api/v1/region/refresh-status?${params.toString()}`);
        const data = await readApiResponse(res);

        if (data.status === 'queued' || data.status === 'running') {
          const cached = Number(data.cached_count || 0);
          setRefreshCopy(
            '저장 데이터 표시 중 · 서버 최신화 중',
            `${city} · 현재 ${cached}곳 즉시 사용 가능 · 수집은 서버가 계속 진행`
          );
          buttonState('<span>↻</span> 서버 최신화 중', true);
          continue;
        }

        if (data.status === 'completed') {
          const result = data.result || {};
          const count = Number(result.recommended_count ?? data.cached_count ?? 0);
          const time = new Date().toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit'});
          setRefreshCopy(`최신화 완료 · ${time}`, `${city} · 추천 ${count}곳 · 화면 대기 없이 서버에서 갱신 완료`);
          buttonState('<span>↻</span> 최신화', false);
          await reloadCachedRegion(province, city);
          if (typeof showToast === 'function') showToast('맛집 최신화 완료', `${city} 추천 데이터를 새로 반영했어.`);
          return;
        }

        if (data.status === 'failed' || data.status === 'cancelled') {
          const message = data.error?.message || '서버 최신화가 완료되지 않았어.';
          setRefreshCopy('최신화 실패', message);
          buttonState('<span>↻</span> 다시 시도', false);
          if (typeof showToast === 'function') showToast('최신화 실패', message);
          return;
        }

        buttonState('<span>↻</span> 최신화', false);
        return;
      } catch (err) {
        // A transient status-poll failure must not restart the expensive collection.
        // Keep the job running server-side and retry a few seconds later.
        setRefreshCopy('서버 최신화 진행 중', '상태 확인이 잠시 끊겼어. 수집 작업 자체는 다시 시작하지 않아.');
        await sleep(2200);
      }
    }
  }

  async function startNonBlockingRefresh() {
    if (typeof selectedCity === 'undefined' || !selectedCity) {
      setRefreshCopy('먼저 시·군을 선택해', '지역을 선택하면 저장 데이터는 즉시 보고, 최신화는 서버에서 따로 진행해.');
      return;
    }

    const province = selectedProvince;
    const city = selectedCity;
    const bbox = typeof selectedBBox !== 'undefined' ? selectedBBox : null;
    const token = ++pollToken;

    buttonState('<span>↻</span> 요청 중', true);
    setRefreshCopy('저장 데이터는 그대로 사용', `${province} ${city} · 최신화 요청만 서버에 전달 중`);

    try {
      const res = await fetch(`${API_BASE}/api/v1/region/refresh-async`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({province, city, bbox}),
      });
      const data = await readApiResponse(res);
      const cached = Number(data.cached_count || 0);

      setRefreshCopy(
        '저장 데이터 표시 중 · 서버 최신화 시작',
        `${city} · 현재 ${cached}곳 즉시 사용 가능 · 폰을 계속 들고 기다릴 필요 없음`
      );
      buttonState('<span>↻</span> 서버 최신화 중', true);
      if (typeof showToast === 'function') {
        showToast('백그라운드 최신화 시작', '다른 지역을 보거나 화면을 닫아도 서버가 계속 수집해.');
      }
      pollRefresh(province, city, token);
    } catch (err) {
      setRefreshCopy('최신화 요청 실패', err.message || '서버 요청에 실패했어.');
      buttonState('<span>↻</span> 다시 시도', false);
    }
  }

  const btn = document.getElementById('reloadBtn');
  if (!btn) return;

  // app-map.js keeps the old synchronous handler for backward compatibility.
  // Capture first and stop it so normal users never wait on the long request.
  btn.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    startNonBlockingRefresh();
  }, {capture: true});
})();
