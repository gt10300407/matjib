(function(){
  const CUISINES=['전체','한식','중식','일식','양식','아시아','분식','카페','디저트'];

  function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]));}
  function formatReviews(n){return Number(n||0).toLocaleString('ko-KR');}
  function evidenceText(r){return `Google ★ ${Number(r.rating||0).toFixed(1)} · 사용자 평가 ${formatReviews(r.user_rating_count||0)}개`;}
  function badgeReason(r){const rating=Number(r.rating||0),reviews=Number(r.user_rating_count||0);if(rating>=4.4&&reviews>=50)return'고평점 검증';if(rating>=4.2&&reviews>=200)return'다수평가 검증';return'검증 기준 통과';}
  function filterRows(filter){const rows=Array.isArray(allRestaurants)?allRestaurants:[];if(!filter||filter==='전체')return rows;return rows.filter(r=>(r.cuisine||r.category||'기타')===filter);}

  renderRestaurantList=function(filter='전체'){
    activeRestaurantFilter=filter||'전체';
    const root=document.getElementById('restaurants'),count=document.getElementById('restaurantCount');
    if(!root||!count)return;
    const rows=filterRows(activeRestaurantFilter);count.textContent=`${rows.length}곳`;
    if(!selectedCity){root.innerHTML='<div class="empty">시·군을 선택하면 사용자 평가 근거가 있는 검증 맛집만 표시해.</div>';return;}
    if(!rows.length){root.innerHTML='<div class="empty">이 분류에서 현재 검증 기준을 통과한 맛집이 없어.<br><small>기준: ★4.4+ & 평가 50개+ 또는 ★4.2+ & 평가 200개+</small></div>';return;}
    root.innerHTML=rows.map(r=>{const cuisine=esc(r.cuisine||r.category||'기타'),address=esc(r.road_address||r.address||''),rating=Number(r.rating||0).toFixed(1),reviews=formatReviews(r.user_rating_count||0),hits=Number(r.query_hits||1);return `<article class="restaurant verifiedRestaurant" ${r.place_url?`data-url="${esc(r.place_url)}"`:''}><div class="thumb">⭐</div><div class="verifiedBody"><div class="rname">${esc(r.name)}</div><div class="menu">${cuisine}${address?` · ${address}`:''}</div><div class="ratingLine"><b>★ ${rating}</b><span>사용자 평가 ${reviews}개</span></div><div class="tags"><span class="tag" style="color:#75f2c8;border-color:#276b59">검증 맛집</span><span class="tag">${esc(badgeReason(r))}</span>${hits>=2?`<span class="tag">검색 교차 ${hits}회</span>`:''}</div><div class="evidenceLine">${esc(evidenceText(r))}</div></div></article>`}).join('');
    root.querySelectorAll('.restaurant[data-url]').forEach(el=>{el.style.cursor='pointer';el.onclick=()=>window.open(el.dataset.url,'_blank','noopener')});
  };

  function renderFoods(foods){
    if(typeof renderFoodsData==='function'){renderFoodsData(foods||[]);return;}
    const root=document.getElementById('foods');if(!root)return;
    root.innerHTML=(foods||[]).map(x=>`<article class="foodCard"><div class="foodEmoji">${x.emoji||'🍴'}</div><div class="foodTxt"><b>${esc(x.name)}</b><small>${esc(x.subtitle||x.source_label||'지역 먹거리')}</small></div></article>`).join('')||'<div class="empty">대표 먹거리 데이터가 아직 없어.</div>';
  }

  async function tasteRefresh(){
    const b=document.getElementById('reloadBtn');
    if(!selectedCity){showToast('지역을 먼저 선택해','시·군을 선택한 뒤 검증 맛집을 확인해.');return;}
    const original=b?.innerHTML;
    if(b){b.disabled=true;b.innerHTML='<span>↻</span> 검증 중';}
    const title=document.getElementById('reloadTitle'),sub=document.getElementById('reloadSub');
    if(title)title.textContent='사용자 평가 기반 맛집 검증 중...';
    if(sub)sub.textContent='Google Places 평가·평가 수를 확인하고 있어.';
    try{
      const r=await fetch(`${API_BASE}/api/v1/region/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({province:selectedProvince,city:selectedCity,bbox:selectedBBox})});
      const d=await readApiResponse(r);
      if(!d.ok){
        allRestaurants=Array.isArray(d.restaurants)?d.restaurants:[];
        renderRestaurantList(activeRestaurantFilter||'전체');
        const msg=d.source_results?.google?.error?.message||d.message||'검증 맛집 수집 실패';
        if(title)title.textContent='맛집 검증 실패';if(sub)sub.textContent=msg;showToast('맛집 검증 실패',msg);return;
      }
      allRestaurants=Array.isArray(d.restaurants)?d.restaurants:[];
      renderFoods(d.foods||[]);
      renderRestaurantList(activeRestaurantFilter||'전체');
      if(title)title.textContent=`검증 완료 · ${d.verified_count||allRestaurants.length}곳`;
      if(sub)sub.textContent=`Google 검색 ${d.google_api_calls||0}회 · 사용자 평가 기준 통과 ${d.verified_count||allRestaurants.length}곳`;
      if(typeof loadStats==='function')await loadStats(selectedProvince,selectedCity);
      await refreshTasteSourceBadge();
    }catch(e){
      if(title)title.textContent='맛집 검증 실패';if(sub)sub.textContent=e.message||String(e);showToast('맛집 검증 실패',e.message||String(e));
    }finally{if(b){b.disabled=false;b.innerHTML=original||'<span>↻</span> 새로고침';}}
  }

  function installTasteFilters(){const filters=document.getElementById('filters');if(!filters)return;filters.innerHTML=CUISINES.map((x,i)=>`<button class="${i===0?'active':''}" data-taste-filter="${x}">${x}</button>`).join('');filters.addEventListener('click',e=>{const btn=e.target.closest('[data-taste-filter]');if(!btn)return;e.preventDefault();e.stopImmediatePropagation();filters.querySelectorAll('button').forEach(x=>x.classList.remove('active'));btn.classList.add('active');renderRestaurantList(btn.dataset.tasteFilter)},true);}
  function installTasteReload(){const b=document.getElementById('reloadBtn');if(!b)return;b.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();tasteRefresh()},true);}

  async function refreshTasteSourceBadge(){try{const d=await readApiResponse(await fetch(`${API_BASE}/api/v1/sources/status`));const badge=document.getElementById('sourceBadge'),mini=document.getElementById('apiMini');if(badge){badge.textContent=d.google?'● Google 평가 연동':'● Google Places 키 필요';badge.style.color=d.google?'#75f2c8':'#f4d47e'}if(mini)mini.textContent=d.google?'맛집 기준: Google 사용자평가':'맛집 평가 데이터 미연동'}catch(e){}}
  function relabelUi(){const stat=document.querySelector('.summary .stat small');if(stat)stat.textContent='저장된 검증 맛집';const h=[...document.querySelectorAll('.sectionTitle h3')].find(x=>x.textContent.includes('추천 맛집'));if(h)h.textContent='🍴 검증 맛집';const reloadSub=document.getElementById('reloadSub');if(reloadSub)reloadSub.textContent='사용자 평점·평가 수 기준으로 검증 맛집을 다시 확인';}
  function injectStyles(){const s=document.createElement('style');s.textContent=`.ratingLine{display:flex;align-items:center;gap:9px;margin-top:7px;font-size:10px;color:#91a5c2}.ratingLine b{color:#ffd66d;font-size:12px}.evidenceLine{margin-top:7px;font-size:8.5px;color:#6f87a8}.verifiedRestaurant{opacity:1!important;pointer-events:auto!important}.verifiedBody{min-width:0}#filters{display:flex;gap:6px;flex-wrap:wrap}`;document.head.appendChild(s);}

  installTasteFilters();installTasteReload();relabelUi();injectStyles();refreshTasteSourceBadge();
})();
