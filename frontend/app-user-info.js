(() => {
  // Consumer UI only. Detailed ranking/evidence stays in the API payload for developer validation.
  const root = document.getElementById('restaurants');
  const count = document.getElementById('restaurantCount');
  if (!root || !count) return;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'
  }[ch]));
  const norm = value => String(value ?? '').toLowerCase().replace(/[\s\-_/·ㆍ.,()\[\]{}'"`]+/g, '');
  const formatReviews = value => Number(value || 0).toLocaleString('ko-KR');

  function currentSearch() {
    return String(document.getElementById('search')?.value || '').trim();
  }

  function matchesSearch(row, query) {
    const q = norm(query);
    if (!q) return true;
    const evidence = row?.evidence || {};
    const hay = norm([
      row?.name,
      row?.road_address,
      row?.address,
      row?.phone,
      ...(evidence.queries || []),
      ...(evidence.specific_queries || []),
    ].filter(Boolean).join(' '));
    return hay.includes(q);
  }

  function localBadges(row) {
    const evidence = row?.evidence || {};
    const queries = [...(evidence.specific_queries || []), ...(evidence.keyword_queries || [])]
      .map(q => String(q || ''));
    const text = queries.join(' ');
    const badges = [];

    if (/현지인\s*맛집/.test(text)) badges.push('현지인 맛집 검색');
    if (/로컬\s*맛집/.test(text)) badges.push('로컬 맛집 검색');
    if (/오래된\s*맛집/.test(text)) badges.push('오래된 맛집 검색');
    if (!badges.length && /유명\s*맛집/.test(text)) badges.push('지역 유명 맛집 검색');

    const sources = new Set(Array.isArray(row?.sources) ? row.sources : (evidence.sources || []));
    if (sources.has('google') && sources.has('kakao')) badges.push('Google · Kakao 확인');
    else if (sources.size >= 2) badges.push('여러 출처 확인');

    if (evidence.official_excellent) badges.push('공식 정보 확인');
    return [...new Set(badges)].slice(0, 3);
  }

  function checkedText(row) {
    if (!row?.updated_at) return '이번 조회에서 확인';
    const date = new Date(row.updated_at);
    if (Number.isNaN(date.getTime())) return '최근 확인';
    return `최근 확인 ${new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(date)}`;
  }

  renderRestaurantList = function renderUserRestaurantList() {
    try { activeRestaurantFilter = '전체'; } catch (_) {}
    const query = currentSearch();
    const rows = (Array.isArray(allRestaurants) ? allRestaurants : []).filter(row => matchesSearch(row, query));
    count.textContent = query ? `${rows.length}곳 검색` : `${rows.length}곳`;

    if (!selectedCity) {
      root.innerHTML = '<div class="empty">시·군을 선택하면 이 지역 맛집을 보여줘.</div>';
      return;
    }
    if (!rows.length) {
      root.innerHTML = `<div class="empty">${query ? `“${esc(query)}” 검색 결과가 없어.` : '현재 저장된 맛집이 없어.'}</div>`;
      return;
    }

    root.innerHTML = rows.map((row, index) => {
      const rating = Number(row.rating || 0);
      const reviews = Number(row.user_rating_count || 0);
      const address = row.road_address || row.address || '주소 확인 중';
      const badges = localBadges(row);
      const ratingHtml = reviews > 0
        ? `<div class="userRating"><b>★ ${rating.toFixed(1)}</b><span>Google 평가 ${formatReviews(reviews)}개</span></div>`
        : '<div class="userRating muted"><span>Google 평가 정보 없음</span></div>';
      const phoneHtml = row.phone ? `<span class="userPhone">☎ ${esc(row.phone)}</span>` : '';
      const badgeHtml = badges.map(label => `<span class="userBadge">${esc(label)}</span>`).join('');
      const linkHtml = row.place_url
        ? `<a class="userPlaceLink" href="${esc(row.place_url)}" target="_blank" rel="noopener">장소 보기 ↗</a>`
        : '';

      return `<article class="restaurant userRestaurantCard">
        <div class="userRank">${index + 1}</div>
        <div class="userRestaurantBody">
          <div class="rname">${esc(row.name || '이름없음')}</div>
          ${ratingHtml}
          <div class="userAddress">📍 ${esc(address)}</div>
          ${phoneHtml ? `<div class="userContact">${phoneHtml}</div>` : ''}
          ${badgeHtml ? `<div class="userBadges">${badgeHtml}</div>` : ''}
          <div class="userCardFoot"><span>${esc(checkedText(row))}</span>${linkHtml}</div>
        </div>
      </article>`;
    }).join('');
  };

  const style = document.createElement('style');
  style.textContent = `
    .userRestaurantCard{display:grid!important;grid-template-columns:36px minmax(0,1fr)!important;gap:11px!important;padding:13px!important;align-items:start!important}
    .userRank{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;background:#0b1a2d;border:1px solid #21466f;color:#d8ebff;font-size:12px;font-weight:800}
    .userRestaurantBody{min-width:0}.userRestaurantBody .rname{font-size:14px;line-height:1.35}
    .userRating{display:flex;gap:8px;align-items:center;margin-top:7px;font-size:10px;color:#9db1cc}.userRating b{font-size:13px;color:#ffd66d}.userRating.muted{color:#70839d}
    .userAddress{margin-top:7px;color:#a9bdd6;font-size:10px;line-height:1.45;overflow-wrap:anywhere}
    .userContact{margin-top:5px;color:#8da6c8;font-size:9.5px}.userBadges{display:flex;gap:5px;flex-wrap:wrap;margin-top:9px}
    .userBadge{padding:4px 7px;border-radius:999px;border:1px solid #284a6d;background:#0a1728;color:#9fc5ef;font-size:8.5px;font-weight:700}
    .userCardFoot{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:10px;padding-top:8px;border-top:1px solid rgba(88,126,173,.12);font-size:8.5px;color:#687f9d}
    .userPlaceLink{color:#86bfff;text-decoration:none;font-weight:700;white-space:nowrap}
    .userPlaceLink:hover{text-decoration:underline}
    @media(max-width:767px){.userRestaurantCard{grid-template-columns:32px minmax(0,1fr)!important;padding:11px!important}.userRank{width:28px;height:28px}.userRestaurantBody .rname{font-size:13px}.userCardFoot{align-items:flex-end}}
  `;
  document.head.appendChild(style);

  renderRestaurantList('전체');
})();
