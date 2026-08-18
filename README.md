# 대한민국 지역 맛집 지도 (matjib)

대한민국 시·도 → 시·군을 선택해 지역 대표 먹거리와 실제 맛집 후보를 탐색하는 웹 앱.

## 현재 구조
- Frontend: HTML/CSS/JavaScript + D3/GSAP
- Backend: FastAPI
- Local cache: SQLite
- External data: Kakao Local, 공공데이터포털, TourAPI

## 로컬 실행
### macOS
```bash
chmod +x START_MAC.command
./START_MAC.command
```

브라우저: `http://127.0.0.1:8787`

### Windows
`START_WINDOWS.bat` 실행.

## API 키
로컬에서는 화면의 `설정` 메뉴에서 입력 가능.
실제 키는 GitHub에 커밋하지 않는다.

## 공개 배포
`render.yaml`이 포함되어 있다. 자세한 절차는 `DEPLOY.md` 참고.

공개 배포에서는 API 키 설정/삭제 UI와 DB 복구 기능을 차단하고, 호스팅 서비스의 Secret 환경변수를 사용한다.

## 데이터 저장 원칙
- DB에 기존 결과가 있으면 즉시 표시
- 새로고침 시 API 최신 결과 확인
- 신규/변경 데이터 반영
- API/DB 장애 시 가능한 기존 데이터 또는 메모리 결과를 우선 표시

## 보안
`.gitignore`에서 다음을 제외한다.
- `.env`
- SQLite/DB 파일
- 가상환경/캐시
- 로그
