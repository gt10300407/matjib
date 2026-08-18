(function(){
  const CUISINES=['전체','한식','중식','일식','양식','아시아','분식','카페','디저트'];
  const SOURCE_NAME={google:'Google',kakao:'Kakao',naver:'Naver',excellent:'공공 모범음식점'};
  let searchQuery='';
  let liveSearchIds=new Set();
  let liveCandidatePreview=[];
  function esc(v){return String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#039;'}[m]));}
  function formatReviews(n){return Number(n||0).toLocaleString('ko-KR');}
  function norm(v){return String(v??'').toLowerCase().replace(/[\s\-_/·ㆍ.,()\[\]{}'"`]+/g,'');}
  function rowSearchText(r){const e=r?.evidence||{};return norm([r?.name,r?.cuisine,r?.category,r?.business_type,r?.road_address,r?.address,...(e.queries||[]),...(e.specific_queries||[])].filter(Boolean).join(' '));}
  function matchesSearch(r){const q=norm(searchQuery);if(!q)return true;if(liveSearchIds.has(String(r?.provider_id||'')))return true;const hay=rowSearchText(r);if(hay.includes(q))return true;const terms=String(searchQuery).trim().toLowerCase().split(/\s+/).map(norm).filter(Boolean);return terms.length>0&&terms.every(t=>hay.includes(t));}
  function filterRows(filter){let rows=Array.isArray(allRestaurants)?allRestaurants:[];if(filter&&filter!=='전체')rows=rows.filter(r=>(r.cuisine||r.category||'기타')===filter);return rows.filter(matchesSearch);}
  function shortQuery(q){return String(q||'').replace(selectedCity||'','').trim();}
  function sourceBadges(r){const src=Array.isArray(r.sources)?r.sources:(r.evidence?.sources||[]);return src.map(s=>`<span class="tag sourceTag">${esc(SOURCE_NAME[s]||s)}</span>`).join('');}
  function scoreParts(e){const p=e?.score_components||{};return [
    ['사용자 평가',p.google_user_evidence],['검색 반복',p.query_repetition],['출처 교차',p.source_diversity],['공식 정보',p.official_data]
  ].filter(x=>Number(x[1]||0)>0).map(([k,v])=>`<span>${esc(k)} <b>${Number(v).toFixed(1)}</b></span>`).join('');}
  function previewHtml(){if(!liveCandidatePreview.length)return'';return `<div class="searchPreview"><b>업체는 찾았지만 추천 근거가 아직 부족해</b>${liveCandidatePreview.slice(0,5).map(x=>`<div><span>${esc(x.name||'이름없음')}</span><small>${esc(SOURCE_NAME[x.provider]||x.provider||'출처')} ${Number(x.user_rating_count||0)>0?`· ★ ${Number(x.rating||0).toFixed(1)} / 평가 ${formatReviews(x.user_rating_count)}개`:''}</small></div>`).join('')}</div>`;}

  renderRestaurantList=function(filter='전체'){
    activeRestaurantFilter=filter||'전체';
    const root=document.getElementById('restaurants'),count=document.getElementById('restaurantCount');if(!root||!count)return;
    const rows=filterRows(activeRestaurantFilter);count.textContent=searchQuery?`${rows.length}곳 검색`:`${rows.length}곳`;
    if(!selectedCity){root.innerHTML='<div class="empty">시·군을 선택하면 여러 출처의 근거를 합쳐 추천 맛집을 보여줘.</div>';return;}
    if(!rows.length){root.innerHTML=previewHtml()||`<div class="empty">${searchQuery?`“${esc(searchQuery)}” 검색 결과가 없어.<br><small>Enter를 누르면 Kakao + Google에서 상호명을 직접 확인해.</small>`:'현재 수집 근거로 추천할 맛집이 없어.'}</div>`;return;}

    root.innerHTML=rows.map(r=>{
      const e=r.evidence||{},rating=Number(r.rating||0),reviews=Number(r.user_rating_count||0),score=Number(r.taste_score||0);
      const qs=(e.specific_queries||e.queries||[]).slice(0,7).map(shortQuery).filter(Boolean);
      const ratingHtml=reviews>0?`<div class="ratingLine"><b>★ ${rating.toFixed(1)}</b><span>Google 사용자 평가 ${formatReviews(reviews)}개</span></div>`:`<div class="ratingLine noGoogle"><b>Google 평가 없음</b><span>지역 검색·다중 출처 근거로 추천</span></div>`;
      const official=e.official_excellent?'<span class="tag officialTag">공식 모범음식점</span>':'';
      const live=liveSearchIds.has(String(r.provider_id||''))?'<span class="tag liveTag">직접 검색 확인</span>':'';
      const queries=qs.length?`<div class="queryEvidence"><b>발견 검색어</b> ${qs.map(q=>`<span>${esc(q)}</span>`).join('')}</div>`:'';
      const components=scoreParts(e);
      return `<article class="restaurant evidenceRestaurant">
        <div class="thumb">🍽️</div>
        <div class="verifiedBody">
          <div class="cardTop"><div><div class="rname">${esc(r.name)}</div><div class="menu">${esc(r.cuisine||r.category||'기타')} · ${esc(r.road_address||r.address||'주소 확인 중')}</div></div><div class="evidenceScore"><b>${score.toFixed(0)}</b><small>/100</small><em>추천 근거</em></div></div>
          ${ratingHtml}
          <div class="tags"><span class="tag recTag">${esc(r.recommendation_label||'추천 맛집')}</span>${live}${sourceBadges(r)}${official}</div>
          <details class="evidenceDetails"><summary>추천 근거 보기</summary><div class="evidencePanel">
            <p><b>출처</b> ${(e.sources||r.sources||[]).map(s=>esc(SOURCE_NAME[s]||s)).join(' · ')||'수집 정보'}</p>
            ${queries}
            ${components?`<div class="scoreParts">${components}</div>`:''}
            <p class="ruleText">${esc(e.rule||'추천 근거 점수는 확률이 아니라 수집 근거의 강도를 표시해.')}</p>
            ${r.place_url?`<a class="originLink" href="${esc(r.place_url)}" target="_blank" rel="noopener">원본 장소 페이지 열기 ↗</a>`:''}
          </div></details>
        </div>
      </article>`;
    }).join('');
  };

  function renderFoods(foods){if(typeof renderFoodsData==='function'){renderFoodsData(foods||[]);return;}const root=document.getElementById('foods');if(!root)return;root.innerHTML=(foods||[]).map(x=>`<article class="foodCard"><div class="foodEmoji">${x.emoji||'🍴'}</div><div class="foodTxt"><b>${esc(x.name)}</b><small>${esc(x.subtitle||x.source_label||'지역 먹거리')}</small></div></article>`).join('')||'<div class="empty">대표 먹거리 데이터가 아직 없어.</div>';}

  async function tasteRefresh(){
    const b=document.getElementById('reloadBtn');if(!selectedCity){showToast('지역을 먼저 선택해','시·군을 선택한 뒤 추천 맛집을 확인해.');return;}
    const original=b?.innerHTML;if(b){b.disabled=true;b.innerHTML='<span>↻</span> 근거 수집 중';}
    const title=document.getElementById('reloadTitle'),sub=document.getElementById('reloadSub');if(title)title.textContent='지역 맛집 근거 수집 중...';if(sub)sub.textContent='Kakao 공간 후보망 + Google 사용자평가 + 공공정보를 합쳐 확인 중';
    try{
      const r=await fetch(`${API_BASE}/api/v1/region/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({province:selectedProvince,city:selectedCity,bbox:selectedBBox})});const d=await readApiResponse(r);
      allRestaurants=Array.isArray(d.restaurants)?d.restaurants:[];liveSearchIds.clear();liveCandidatePreview=[];renderFoods(d.foods||[]);renderRestaurantList(activeRestaurantFilter||'전체');
      if(!d.ok){const msg=d.message||'추천 데이터 수집 실패';if(title)title.textContent='맛집 근거 수집 실패';if(sub)sub.textContent=msg;showToast('수집 실패',msg);return;}
      const src=Object.entries(d.source_results||{}).filter(([,v])=>v?.ok).map(([k])=>SOURCE_NAME[k]||k).join(' + ');
      if(title)title.textContent=`추천 갱신 · ${d.recommended_count??allRestaurants.length}곳`;
      if(sub)sub.textContent=`후보 근거 ${Number(d.candidate_count||0).toLocaleString('ko-KR')}건 · ${src||'사용 가능한 출처'} · Google ${d.google_api_calls||0}회 / Kakao ${d.kakao_api_calls||0}회`;
      if(typeof loadStats==='function')await loadStats(selectedProvince,selectedCity);await refreshTasteSourceBadge();
    }catch(e){if(title)title.textContent='맛집 근거 수집 실패';if(sub)sub.textContent=e.message||String(e);showToast('수집 실패',e.message||String(e));}
    finally{if(b){b.disabled=false;b.innerHTML=original||'<span>↻</span> 새로고침';}}
  }

  async function runLiveSearch(){
    const input=document.getElementById('search');const q=(input?.value||'').trim();if(!q)return;
    if(!selectedCity){showToast('지역을 먼저 선택해','실시간 상호 검색은 시·군 선택 후 사용할 수 있어.');return;}
    const title=document.getElementById('reloadTitle'),sub=document.getElementById('reloadSub');
    if(title)title.textContent=`“${q}” 직접 확인 중...`;if(sub)sub.textContent='Enter 검색은 Kakao 1회 + Google 최대 1회를 호출해.';
    try{
      const r=await fetch(`${API_BASE}/api/v1/search/live`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({q,province:selectedProvince,city:selectedCity,bbox:selectedBBox})});
      const d=await readApiResponse(r);liveCandidatePreview=Array.isArray(d.candidate_preview)?d.candidate_preview:[];
      const found=Array.isArray(d.restaurants)?d.restaurants:[];liveSearchIds=new Set(found.map(x=>String(x.provider_id||'')));
      if(found.length){const merged=new Map((Array.isArray(allRestaurants)?allRestaurants:[]).map(x=>[String(x.provider_id||''),x]));found.forEach(x=>merged.set(String(x.provider_id||''),x));allRestaurants=[...merged.values()];if(title)title.textContent=`직접 검색 확인 · ${found.length}곳`;if(sub)sub.textContent=`Kakao ${d.kakao_api_calls||0}회 · Google ${d.google_api_calls||0}회 · 추천 기준 통과`;showToast('검색 확인 완료',`${found[0].name}${found.length>1?` 외 ${found.length-1}곳`:''}`);}else{if(title)title.textContent='업체 후보 확인 · 추천 근거 부족';if(sub)sub.textContent=`후보 ${d.candidate_count||0}건 · Kakao ${d.kakao_api_calls||0}회 · Google ${d.google_api_calls||0}회`;showToast('추천 목록에는 미포함','업체 후보는 찾았지만 현재 근거 기준을 통과하지 못했어.');}
      renderRestaurantList(activeRestaurantFilter||'전체');
    }catch(e){if(title)title.textContent='직접 검색 실패';if(sub)sub.textContent=e.message||String(e);showToast('검색 실패',e.message||String(e));}
  }

  function installTasteFilters(){const filters=document.getElementById('filters');if(!filters)return;filters.innerHTML=CUISINES.map((x,i)=>`<button class="${i===0?'active':''}" data-taste-filter="${x}">${x}</button>`).join('');filters.addEventListener('click',e=>{const btn=e.target.closest('[data-taste-filter]');if(!btn)return;e.preventDefault();e.stopImmediatePropagation();filters.querySelectorAll('button').forEach(x=>x.classList.remove('active'));btn.classList.add('active');renderRestaurantList(btn.dataset.tasteFilter)},true);}
  function installTasteReload(){const b=document.getElementById('reloadBtn');if(!b)return;b.addEventListener('click',e=>{e.preventDefault();e.stopImmediatePropagation();tasteRefresh()},true);}
  function installTasteSearch(){const input=document.getElementById('search');if(!input)return;input.placeholder='상호명·음식 검색 · Enter로 실시간 확인';input.addEventListener('input',()=>{searchQuery=input.value.trim();liveSearchIds.clear();liveCandidatePreview=[];renderRestaurantList(activeRestaurantFilter||'전체')});input.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();searchQuery=input.value.trim();runLiveSearch()}});}
  async function refreshTasteSourceBadge(){try{const d=await readApiResponse(await fetch(`${API_BASE}/api/v1/sources/status`));const badge=document.getElementById('sourceBadge'),mini=document.getElementById('apiMini');const n=[d.google?'Google':null,d.kakao?'Kakao':null,d.data_go?'공공':null].filter(Boolean);if(badge){badge.textContent=`● ${n.length}개 근거 소스 연결`;badge.style.color=n.length>=2?'#75f2c8':'#f4d47e'}if(mini)mini.textContent='Kakao 공간 후보망 · Google 평가 · Enter 실시간 상호 확인'}catch(e){}}
  function relabelUi(){const stat=document.querySelector('.summary .stat small');if(stat)stat.textContent='저장된 추천 맛집';const h=[...document.querySelectorAll('.sectionTitle h3')].find(x=>x.textContent.includes('검증 맛집')||x.textContent.includes('추천 맛집'));if(h)h.textContent='🍴 추천 맛집';const reloadSub=document.getElementById('reloadSub');if(reloadSub)reloadSub.textContent='지역 공간 후보 + 평가 근거를 다시 수집';}
  function injectStyles(){const s=document.createElement('style');s.textContent=`
    @media(min-width:1100px){.body{grid-template-columns:72px minmax(620px,1fr) clamp(440px,34vw,560px)!important}}
    .restaurant.evidenceRestaurant{grid-template-columns:54px 1fr!important;padding:12px!important;align-items:start!important;overflow:visible!important}.evidenceRestaurant .thumb{width:54px;height:54px}.verifiedBody{min-width:0}.cardTop{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px}.menu{white-space:normal;overflow-wrap:anywhere;line-height:1.45}.evidenceScore{text-align:right;min-width:62px;color:#ffd66d}.evidenceScore b{font-size:18px}.evidenceScore small{font-size:9px}.evidenceScore em{display:block;font-style:normal;font-size:8px;color:#7890b0;margin-top:1px}.ratingLine{display:flex;align-items:center;gap:9px;margin-top:8px;font-size:10px;color:#91a5c2}.ratingLine b{color:#ffd66d;font-size:12px}.ratingLine.noGoogle b{color:#9fb3d0}.tags{margin-top:7px}.recTag{color:#75f2c8!important;border-color:#276b59!important}.sourceTag{color:#9ac8ff!important}.officialTag{color:#ffd66d!important;border-color:#6d5b28!important}.liveTag{color:#ff9fe3!important;border-color:#70476a!important}.evidenceDetails{margin-top:8px;border-top:1px solid rgba(91,132,187,.16);padding-top:7px}.evidenceDetails summary{cursor:pointer;color:#8fb9e8;font-size:9px;user-select:none}.evidencePanel{margin-top:7px;padding:9px;border:1px solid rgba(72,126,187,.16);border-radius:10px;background:#07101d;font-size:9px;color:#8299b9;line-height:1.55}.evidencePanel p{margin:0 0 7px}.queryEvidence{margin:7px 0}.queryEvidence>b{display:block;color:#b7cae1;margin-bottom:4px}.queryEvidence span{display:inline-block;border:1px solid #203a59;border-radius:999px;padding:2px 6px;margin:0 4px 4px 0}.scoreParts{display:flex;gap:5px;flex-wrap:wrap;margin:7px 0}.scoreParts span{background:#0a1a2b;border-radius:7px;padding:4px 6px}.scoreParts b{color:#d8e9ff}.ruleText{opacity:.8}.originLink{display:inline-block;color:#61d7ff;text-decoration:none;margin-top:2px}#filters{display:flex;gap:6px;flex-wrap:wrap}.panelScroll{padding-bottom:80px!important}.searchPreview{padding:12px;border:1px solid #314a6b;border-radius:12px;background:#081422;color:#a8bdd7;font-size:10px}.searchPreview>b{display:block;color:#f4d47e;margin-bottom:8px}.searchPreview>div{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid rgba(91,132,187,.14)}.searchPreview small{color:#7189a8;text-align:right}
    @media(max-width:1099px){.body{grid-template-columns:64px minmax(520px,1fr) minmax(390px,44vw)!important}.panelHead{padding-left:13px;padding-right:13px}.panelScroll{padding-left:13px;padding-right:13px}}
  `;document.head.appendChild(s);}
  installTasteFilters();installTasteReload();installTasteSearch();relabelUi();injectStyles();refreshTasteSourceBadge();
})();
