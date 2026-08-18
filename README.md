# 대한민국 지역 맛집 지도 (matjib)

대한민국 시·도 → 시·군을 선택해 **사용자 평가 근거가 있는 검증 맛집**과 지역 대표 먹거리를 탐색하는 웹 앱.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/gt10300407/matjib)

## Taste v2 원칙
이 프로젝트는 더 이상 Kakao/공공데이터에 등록된 전체 음식점을 `맛집`이라고 부르지 않는다.

사용자 화면에 노출되는 맛집은 Google Places 사용자 평가 데이터를 기준으로 아래 중 하나를 만족해야 한다.

- 평점 `4.4 이상` + 사용자 평가 `50개 이상`
- 평점 `4.2 이상` + 사용자 평가 `200개 이상`

정렬은 적은 표본의 5.0이 과대평가되지 않도록 Bayesian 보정평점을 사용하고, 사용자 평가 수를 보조 기준으로 사용한다.

## 음식 분류
- 전체
- 한식
- 중식
- 일식
- 양식
- 아시아
- 분식
- 카페
- 디저트

`해산물` 같은 음식 특성은 향후 중분류/태그 체계로 확장한다.

## 현재 구조
- Frontend: HTML/CSS/JavaScript + D3/GSAP
- Backend: FastAPI
- Verified taste cache: SQLite
- Taste evidence: Google Places `rating`, `userRatingCount`
- Auxiliary sources: Kakao Local, 공공데이터포털, TourAPI

## 로컬 실행
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

브라우저: `http://127.0.0.1:8787`

## API 키
Taste v2 핵심 키:
- `GOOGLE_PLACES_API_KEY`

보조 데이터 키:
- `KAKAO_REST_API_KEY`
- `DATA_GO_KR_SERVICE_KEY`
- `TOUR_API_SERVICE_KEY`

실제 키와 DB 파일은 GitHub에 커밋하지 않는다.

## 공개 배포
Render 환경변수에 최소 `GOOGLE_PLACES_API_KEY`를 추가해야 검증 맛집이 표시된다.
Google Places 키가 없으면 일반 음식점 목록으로 자동 대체하지 않는다.

## 속도 원칙
사용자 클릭 시 Kakao 공간 전수검색을 하지 않는다.
Google Text Search 카테고리 요청을 병렬로 실행하고 검증 기준을 통과한 결과만 저장/표시한다.

## 코드 관리
`main` 브랜치가 운영 기준이다. Pull Request와 GitHub Actions CI를 통과한 변경만 운영에 반영한다.
