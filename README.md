# 대한민국 지역 맛집 지도 (matjib)

대한민국 시·도 → 시·군을 선택해 지역 대표 먹거리와 실제 맛집 후보를 탐색하는 웹 앱.

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/gt10300407/matjib)

## 현재 구조
- Frontend: HTML/CSS/JavaScript + D3/GSAP
- Backend: FastAPI
- Local cache: SQLite
- External data: Kakao Local, 공공데이터포털, TourAPI

## 로컬 실행
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

브라우저: `http://127.0.0.1:8787`

## API 키
로컬에서는 화면의 `설정` 메뉴에서 입력 가능하다.
실제 키와 DB 파일은 GitHub에 커밋하지 않는다.

## 공개 배포
저장소 루트의 `render.yaml`을 Render Blueprint가 사용한다.

배포 시 Secret으로 다음 값을 입력한다.
- `KAKAO_REST_API_KEY`
- `DATA_GO_KR_SERVICE_KEY`
- `TOUR_API_SERVICE_KEY`

공개 배포에서는 API 키 관리/진단 UI와 DB 복구 기능을 숨기고 서버의 Secret 환경변수를 사용한다.

자세한 절차는 `DEPLOY.md` 참고.

## 데이터 저장 원칙
- DB에 기존 결과가 있으면 즉시 표시
- 새로고침 시 API 최신 결과 확인
- 신규/변경 데이터 반영
- API/DB 장애 시 가능한 기존 데이터 또는 메모리 결과를 우선 표시

## 코드 관리
`main` 브랜치가 운영 기준이다. `.github/workflows/ci.yml`에서 Python/JavaScript/FastAPI 기본 검수를 수행한다.

## 보안
`.gitignore`에서 다음을 제외한다.
- `.env`
- SQLite/DB 파일
- 가상환경/캐시
- 로그
